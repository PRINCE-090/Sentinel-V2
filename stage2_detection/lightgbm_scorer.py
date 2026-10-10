from __future__ import annotations
import json
import os
from typing import List, Optional

import numpy as np

from sentinel.core import get_logger, CONFIG
from stage1_ingestion.schemas import EnrichedTransaction

logger = get_logger(__name__)

try:
    import lightgbm as lgb
    _HAS_LIGHTGBM = True
except ImportError:
    lgb = None
    _HAS_LIGHTGBM = False
    logger.warning("lightgbm not installed — LightGBMScorer will run in heuristic-only mode")

# Exact feature order matching Stage 1's frozen `DerivedFeatures` schema.
# This ordering IS the model's input contract — any drift here silently
# corrupts inference against a trained model, so `_to_vector` derives from
# this list rather than hand-listing attribute access twice.
#
# NOTE: extended from 18 -> 23 features (declared-income/occupation strain
# signals appended at the end). Any model artifact trained against the old
# 18-feature contract is STALE — retrain via train_models.py before this
# scorer is deployed, or `_to_vector`'s output width will no longer match
# what the loaded booster expects.
FEATURE_NAMES: List[str] = [
    "velocity_1h", "velocity_24h", "amount_deviation", "income_ratio",
    "structuring_proximity", "merchant_risk_tier", "receiver_velocity_1h",
    "receiver_account_age_days", "new_counterparty", "velocity_6h",
    "receiver_inflow_spike_ratio", "receiver_distinct_senders_24h",
    "fan_in_out_ratio", "cross_border", "hour_risk_bucket",
    "device_novelty", "round_amount", "drift_amount_slope",
    "declared_income_ratio", "salary_strain_index",
    "compounding_risk_signal_count", "strain_x_compound_risk",
    "kyc_missing","declared_vs_actual_mismatch_ratio",
]

# Features where risk must be monotonically non-decreasing as the feature
# value increases (LightGBM `monotone_constraints`, +1 = increasing). This
# bakes in domain knowledge directly — e.g. cross-border routing, gambling
# MCC exposure, and an unrecognized device can never *reduce* the model's
# output — instead of overriding the model's score after the fact in
# score_fusion.py, which would break score calibration/backtestability.
MONOTONIC_INCREASING_FEATURES = {
    "cross_border",
    "device_novelty",
    "merchant_risk_tier",
    "declared_income_ratio",
    "salary_strain_index",
    "compounding_risk_signal_count",
    "strain_x_compound_risk",
    "kyc_missing",
    "declared_vs_actual_mismatch_ratio",
}

_DEFAULT_MODEL_PATH = "models/lightgbm_model.txt"
_DEFAULT_THRESHOLD_PATH = "models/threshold.json"
_DEFAULT_THRESHOLD = 0.5


def _to_vector(tx: EnrichedTransaction) -> np.ndarray:
    """
    Extracts all 18 features from `tx.features` in the exact FEATURE_NAMES
    order. All fields — including integer/binary ones (new_counterparty,
    cross_border, hour_risk_bucket, device_novelty, round_amount,
    merchant_risk_tier, receiver_account_age_days,
    receiver_distinct_senders_24h) — are cast to float, since the model
    expects a uniform float32/float64 input matrix.
    """
    features = tx.features
    values = [float(getattr(features, name)) for name in FEATURE_NAMES]
    return np.array(values, dtype=float)


class LightGBMScorer:
    def __init__(
        self,
        model_path: Optional[str] = None,
        threshold_path: str = _DEFAULT_THRESHOLD_PATH,
    ):
        self._model_path = model_path or getattr(CONFIG, "lgb_model_path", _DEFAULT_MODEL_PATH)
        self._threshold: float = _DEFAULT_THRESHOLD
        self._model = None
        self._fitted: bool = False

        self._load_threshold(threshold_path)
        self._load_model()

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _load_threshold(self, threshold_path: str) -> None:
        try:
            with open(threshold_path) as fh:
                data = json.load(fh)
            self._threshold = float(data.get("threshold", _DEFAULT_THRESHOLD))
        except FileNotFoundError:
            logger.info(f"No threshold file at {threshold_path}, using default {_DEFAULT_THRESHOLD}")
        except Exception as e:
            logger.warning(f"Failed to load threshold from {threshold_path}: {e} — using default")
            self._threshold = _DEFAULT_THRESHOLD

    def _load_model(self) -> None:
        if not _HAS_LIGHTGBM:
            return
        if not os.path.exists(self._model_path):
            logger.info(f"No LightGBM model file at {self._model_path} — heuristic fallback active")
            return
        try:
            self._model = lgb.Booster(model_file=self._model_path)
            self._fitted = True
            logger.info(f"LightGBM model loaded from {self._model_path}")
        except Exception as e:
            logger.warning(f"Failed to load LightGBM model from {self._model_path}: {e}")
            self._model = None
            self._fitted = False

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #
    def predict_proba(self, tx: EnrichedTransaction) -> float:
        if self._fitted and self._model is not None:
            try:
                vec = _to_vector(tx).reshape(1, -1)
                prob = float(self._model.predict(vec)[0])
                return round(prob, 4)
            except Exception as e:
                logger.warning(f"LightGBM inference failed for {tx.raw.transaction_id}, "
                                f"falling back to heuristic: {e}")
        return round(self._heuristic(tx), 4)

    def predict_fraud(self, tx: EnrichedTransaction) -> int:
        return int(self.predict_proba(tx) >= self._threshold)

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #
    def train(self, transactions: list, labels: list) -> None:
        if not _HAS_LIGHTGBM:
            raise RuntimeError("lightgbm is not installed — cannot train LightGBMScorer")

        X = np.vstack([_to_vector(tx) for tx in transactions])
        y = np.asarray(labels)

        pos_count = int((y == 1).sum())
        neg_count = int((y == 0).sum())
        scale_pos_weight = neg_count / max(pos_count, 1)

        monotone_constraints = [
            1 if name in MONOTONIC_INCREASING_FEATURES else 0
            for name in FEATURE_NAMES
        ]

        # Upweight the compound high-risk pattern (multiple strong signals
        # co-occurring) in addition to the class-level scale_pos_weight.
        # This is the actual root-cause fix for under-prediction on
        # scenarios like Scenario A: LightGBM needs enough *effective*
        # weight on this conjunction in training to justify deep enough
        # splits to isolate it, not just the features existing.
        compound_flags = np.array([
            float(getattr(tx.features, "compounding_risk_signal_count", 0)) >= 2
            for tx in transactions
        ])
        sample_weight = np.where(
            (y == 1) & compound_flags, 2.0,
            np.where(y == 1, 1.0, 1.0),
        )

        clf = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.03,
            num_leaves=31,
            scale_pos_weight=scale_pos_weight,
            monotone_constraints=monotone_constraints,
            monotone_constraints_method="advanced",
            random_state=42,
        )
        clf.fit(X, y, sample_weight=sample_weight)

        self._model = clf.booster_
        self._fitted = True

        model_dir = os.path.dirname(self._model_path)
        if model_dir:
            os.makedirs(model_dir, exist_ok=True)
        clf.booster_.save_model(self._model_path)
        logger.info(
            f"LightGBM model trained on {len(y)} samples "
            f"(pos={pos_count}, neg={neg_count}, scale_pos_weight={scale_pos_weight:.4f}) "
            f"and saved to {self._model_path}"
        )

    # ------------------------------------------------------------------ #
    # Heuristic fallback
    # ------------------------------------------------------------------ #
    def _heuristic(self, tx: EnrichedTransaction) -> float:
        """
        Rule-of-thumb fraud probability used when no trained model is
        available (cold start) or inference fails. Combines sender velocity,
        structuring/deviation signals, receiver-side fan-in/spike signals,
        and device/geo/merchant risk into a single [0, 1] score. Each term's
        weight is capped before summation, so the weighted sum is naturally
        bounded — a final clamp is applied purely as a defensive safety net.
        """
        f = tx.features

        score = 0.0
        score += 0.15 * min(f.velocity_1h / 5.0, 1.0)              # sender burst (1h)
        score += 0.10 * min(f.velocity_6h / 15.0, 1.0)             # sender burst (6h)
        score += 0.15 * min(max(f.structuring_proximity, 0.0), 1.0)  # threshold-skirting
        score += 0.15 * min(abs(f.amount_deviation) / 4.0, 1.0)    # amount outlier-ness
        score += 0.15 * min(f.fan_in_out_ratio / 5.0, 1.0)         # receiver fan-in (mule pattern)
        score += 0.10 * min(f.receiver_inflow_spike_ratio / 5.0, 1.0)  # sudden inflow spike
        score += 0.08 * float(f.device_novelty)                    # unseen device
        score += 0.07 * float(f.cross_border)                      # cross-border flag
        score += 0.05 * (min(f.merchant_risk_tier, 3) / 3.0)       # merchant category risk

        # Declared-income strain, boosted further when it co-occurs with
        # other independently high-risk signals (cross-border, gambling
        # MCC, new device) — mirrors the monotonic constraints used in
        # the trained model so the heuristic degrades safely to the same
        # domain logic, not a weaker version of it.
        score += 0.20 * min(getattr(f, "salary_strain_index", 0.0) / 10.0, 1.0)
        score += 0.15 * min(getattr(f, "strain_x_compound_risk", 0.0) / 20.0, 1.0)
        score += 0.05 * float(getattr(f, "kyc_missing", 0))

        return max(0.0, min(1.0, score))
