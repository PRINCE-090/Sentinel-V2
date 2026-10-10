from __future__ import annotations
import os
import pickle
from typing import List, Optional

import numpy as np

from sentinel.core import get_logger, CONFIG
from stage1_ingestion.schemas import EnrichedTransaction
# Reuse the exact same 18-feature order and vectorizer as the supervised
# scorer — importing rather than redefining guarantees the two models can
# never silently drift out of sync on feature ordering.
from .lightgbm_scorer import FEATURE_NAMES, _to_vector

logger = get_logger(__name__)

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import IsolationForest
    _HAS_SKLEARN = True
except ImportError:
    StandardScaler = None
    IsolationForest = None
    _HAS_SKLEARN = False
    logger.warning("scikit-learn not installed — AnomalyScorer will run in heuristic-only mode")

_DEFAULT_MODEL_PATH = "models/anomaly_scorer.pkl"


class AnomalyScorer:
    def __init__(self, model_path: Optional[str] = None):
        self._model_path = model_path or getattr(CONFIG, "anomaly_model_path", _DEFAULT_MODEL_PATH)
        self._model = None
        self._scaler = None
        self._p05: Optional[float] = None
        self._p95: Optional[float] = None
        self._use_eif: bool = False
        self._fitted: bool = False

        self._load()

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not os.path.exists(self._model_path):
            logger.info(f"No anomaly model bundle at {self._model_path} — heuristic fallback active")
            return
        try:
            with open(self._model_path, "rb") as fh:
                bundle = pickle.load(fh)

            saved_features = bundle.get("feature_names")
            if saved_features is not None and saved_features != FEATURE_NAMES:
                logger.warning(
                    f"Feature order mismatch in {self._model_path} — "
                    f"saved model ignored, heuristic fallback active"
                )
                return

            self._model = bundle["model"]
            self._scaler = bundle["scaler"]
            self._p05 = bundle["p05"]
            self._p95 = bundle["p95"]
            self._use_eif = bundle.get("use_eif", False)
            self._fitted = True
            backend = "eif" if self._use_eif else "IsolationForest"
            logger.info(f"Anomaly scorer loaded from {self._model_path} (backend={backend})")
        except Exception as e:
            logger.warning(f"Failed to load anomaly model bundle from {self._model_path}: {e}")
            self._model = None
            self._scaler = None
            self._fitted = False

    # ------------------------------------------------------------------ #
    # Fitting
    # ------------------------------------------------------------------ #
    def fit(self, transactions: List[EnrichedTransaction]) -> None:
        if not _HAS_SKLEARN:
            raise RuntimeError("scikit-learn is not installed — cannot fit AnomalyScorer")
        if not transactions:
            raise ValueError("fit() requires at least one transaction")

        X = np.vstack([_to_vector(tx) for tx in transactions])
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)

        self._scaler = scaler
        self._model = self._fit_backend(Xs)  # also sets self._use_eif

        raw_scores = self._raw_scores(Xs)
        self._p05 = float(np.percentile(raw_scores, 5))
        self._p95 = float(np.percentile(raw_scores, 95))
        self._fitted = True

        self._save()

    def _fit_backend(self, Xs: np.ndarray):
        """
        Prefers Extended Isolation Forest (`eif`) when installed, since it
        addresses the axis-aligned splitting bias of standard Isolation
        Forest for correlated/high-dimensional fraud features. Falls back to
        sklearn's IsolationForest with the specified params if `eif` is
        unavailable or its (less stable, version-varying) API fails —
        production code should never hard-depend on an optional library.
        """
        try:
            import eif as eif_lib
            sample_size = min(256, Xs.shape[0])
            model = eif_lib.iForest(
                Xs.astype(np.float64),
                ntrees=150,
                sample_size=sample_size,
                ExtensionLevel=Xs.shape[1] - 1,
            )
            self._use_eif = True
            logger.info("Fitted Extended Isolation Forest (eif) backend")
            return model
        except Exception as e:
            logger.info(f"eif unavailable or failed ({e}) — falling back to sklearn IsolationForest")
            model = IsolationForest(n_estimators=150, contamination=0.05, random_state=42)
            model.fit(Xs)
            self._use_eif = False
            return model

    def _raw_scores(self, Xs: np.ndarray) -> np.ndarray:
        """
        Unifies both backends onto a single convention: HIGHER = MORE
        ANOMALOUS. eif's `compute_paths` already returns scores in this
        direction; sklearn's `score_samples` is the opposite (higher = more
        normal), so it is negated here. This is what makes `score()`'s
        percentile normalization correct regardless of which backend loaded.
        """
        if self._use_eif:
            return np.array([self._model.compute_paths(X_in=Xs[i:i + 1])[0] for i in range(Xs.shape[0])])
        return -self._model.score_samples(Xs)

    def _save(self) -> None:
        bundle = {
            "model": self._model,
            "scaler": self._scaler,
            "p05": self._p05,
            "p95": self._p95,
            "feature_names": FEATURE_NAMES,
            "use_eif": self._use_eif,
        }
        model_dir = os.path.dirname(self._model_path)
        if model_dir:
            os.makedirs(model_dir, exist_ok=True)
        with open(self._model_path, "wb") as fh:
            pickle.dump(bundle, fh)
        backend = "eif" if self._use_eif else "IsolationForest"
        logger.info(f"Anomaly scorer saved to {self._model_path} (backend={backend})")

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def score(self, tx: EnrichedTransaction) -> float:
        if not self._fitted or self._model is None or self._scaler is None:
            return round(self._heuristic(tx), 4)

        try:
            vec = _to_vector(tx).reshape(1, -1)
            Xs = self._scaler.transform(vec)
            raw = self._raw_scores(Xs)[0]

            if self._p05 is None or self._p95 is None or self._p95 == self._p05:
                norm = 0.5  # degenerate calibration guard — avoids zero-division
            else:
                norm = (raw - self._p05) / (self._p95 - self._p05)

            norm = float(max(0.0, min(1.0, norm)))
            return round(norm, 4)
        except Exception as e:
            logger.warning(f"Anomaly scoring failed for {tx.raw.transaction_id}, "
                            f"falling back to heuristic: {e}")
            return round(self._heuristic(tx), 4)

    # ------------------------------------------------------------------ #
    # Heuristic fallback
    # ------------------------------------------------------------------ #
    def _heuristic(self, tx: EnrichedTransaction) -> float:
        """
        Cold-start / failure-mode fallback. Rather than a flat weighted sum,
        this looks for known *combinations* of features that are individually
        weak signals but jointly strong indicators — mirroring what an
        Isolation Forest's multivariate splits would tend to isolate.
        """
        f = tx.features
        score = 0.0

        # Velocity burst + brand-new device: account-takeover signature.
        if f.velocity_1h >= 3 and f.device_novelty == 1:
            score += 0.25

        # Fan-in concentration + sudden inflow spike: money-mule receiving signature.
        if f.fan_in_out_ratio >= 3.0 and f.receiver_inflow_spike_ratio >= 2.0:
            score += 0.25

        # Amount deliberately hovering near the reporting threshold.
        score += 0.20 * min(max(f.structuring_proximity, 0.0), 1.0)

        # Statistical outlier relative to the sender's own baseline.
        score += 0.15 * min(abs(f.amount_deviation) / 4.0, 1.0)

        # Cross-border payment into a high-risk merchant category.
        if f.cross_border == 1 and f.merchant_risk_tier >= 3:
            score += 0.15

        return max(0.0, min(1.0, score))
