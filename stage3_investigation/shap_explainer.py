from __future__ import annotations
from typing import List

from sentinel.core import get_logger
from stage1_ingestion.schemas import EnrichedTransaction
from stage2_detection.lightgbm_scorer import LightGBMScorer, FEATURE_NAMES, _to_vector
from .schemas import ReasonCode

logger = get_logger(__name__)

_FEATURE_LABELS = {
    "velocity_1h":                   "1h transaction velocity",
    "velocity_6h":                   "6h transaction velocity",
    "velocity_24h":                  "24h transaction velocity",
    "amount_deviation":              "Amount deviation from 30d baseline",
    "new_counterparty":              "New counterparty flag",
    "cross_border":                  "Cross-border transaction",
    "hour_risk_bucket":              "Unusual hour risk bucket",
    "device_novelty":                "New/unrecognized device flag",
    "round_amount":                  "Round amount indicator",
    "structuring_proximity":         "Proximity to reporting threshold",
    "merchant_risk_tier":            "High-risk merchant tier",
    "drift_amount_slope":            "Rising transaction amount trend",
    "drift_freq_slope":              "Rising transaction frequency trend",
    "income_ratio":                  "Transaction amount as % of monthly income",
    "receiver_velocity_1h":          "Receiver 1h inbound velocity",
    "receiver_account_age_days":     "Receiver account age (days)",
    "receiver_inflow_spike_ratio":   "Receiver inbound spike ratio",
    "receiver_distinct_senders_24h": "Receiver distinct senders count",
    "fan_in_out_ratio":              "Fan-in / fan-out structural ratio",
}

# Approximate typical per-feature scale, used only by the feature-importance
# fallback to normalize raw feature magnitude into a roughly comparable
# [0, 1] range before weighting by model importance. These are illustrative
# defaults, NOT computed from real training-set statistics (no persisted
# feature-stats artifact is wired into this module) — if/when actual
# per-feature std devs from the training set are available, they should
# replace this table for a materially better-calibrated fallback.
_FEATURE_SCALE = {
    "velocity_1h": 2.0,
    "velocity_6h": 5.0,
    "velocity_24h": 10.0,
    "amount_deviation": 1.5,
    "new_counterparty": 0.5,
    "cross_border": 0.5,
    "hour_risk_bucket": 1.0,
    "device_novelty": 0.5,
    "round_amount": 0.5,
    "structuring_proximity": 0.3,
    "merchant_risk_tier": 1.0,
    "drift_amount_slope": 1.0,
    "drift_freq_slope": 1.0,
    "income_ratio": 2.0,
    "receiver_velocity_1h": 3.0,
    "receiver_account_age_days": 200.0,
    "receiver_inflow_spike_ratio": 3.0,
    "receiver_distinct_senders_24h": 3.0,
    "fan_in_out_ratio": 3.0,
}

# Features where a HIGHER raw value corresponds to LOWER fraud risk (i.e. the
# feature is protective rather than risk-increasing as it grows). Used only
# by the feature-importance fallback to derive a signed, directional
# contribution from an otherwise-unsigned importance-weighted magnitude —
# plain feature importance carries no directionality on its own, so without
# this table every fallback contribution would default to "positive" (risk-
# increasing) regardless of what the feature actually represents.
#
# Every feature NOT listed here defaults to the more common polarity: higher
# raw value -> higher fraud risk. This table only needs to capture the
# exceptions.
_INVERSE_RISK_FEATURES = {
    # Older receiver accounts are, all else equal, lower risk than
    # freshly-opened ones — this is the canonical example: a large
    # `receiver_account_age_days` value should be reported as *decreasing*
    # risk, not increasing it.
    "receiver_account_age_days",
}


class SHAPExplainer:
    def __init__(self, scorer: LightGBMScorer):
        self._scorer = scorer
        self._shap_available = False
        try:
            import shap  # noqa: F401
            self._shap_available = True
            logger.info("SHAP library available — using TreeExplainer")
        except ImportError:
            logger.warning("shap not installed — using feature-importance fallback")

    def explain(self, tx: EnrichedTransaction, top_k: int = 5) -> List[ReasonCode]:
        if self._shap_available and getattr(self._scorer, "_fitted", False) and self._scorer._model is not None:
            try:
                return self._shap_explain(tx, top_k)
            except Exception as e:
                logger.warning(
                    f"SHAP explanation failed for {tx.raw.transaction_id}, "
                    f"falling back to feature-importance scoring: {e}"
                )
        return self._fallback_explain(tx, top_k)

    # ------------------------------------------------------------------ #
    # SHAP path
    # ------------------------------------------------------------------ #
    def _shap_explain(self, tx: EnrichedTransaction, top_k: int) -> List[ReasonCode]:
        import shap

        x = _to_vector(tx).reshape(1, -1)
        explainer = shap.TreeExplainer(self._scorer._model)
        shap_values = explainer.shap_values(x)

        # Different SHAP/model-backend combinations return either a list
        # (per-class arrays, classification) or a single 2D array — handle
        # both rather than assuming one shape.
        if isinstance(shap_values, list):
            sv = shap_values[1][0]
        else:
            sv = shap_values[0]
        sv = sv.tolist() if hasattr(sv, "tolist") else list(sv)

        contributions = list(zip(FEATURE_NAMES, sv))
        contributions.sort(key=lambda t: abs(t[1]), reverse=True)
        top = contributions[:top_k]

        return [self._to_reason_code(tx, name, contrib) for name, contrib in top]

    # ------------------------------------------------------------------ #
    # Feature-importance fallback (no SHAP / model not fitted / SHAP failure)
    # ------------------------------------------------------------------ #
    def _fallback_explain(self, tx: EnrichedTransaction, top_k: int) -> List[ReasonCode]:
        fi = getattr(self._scorer, "feature_importances", None)
        if not fi:
            fi = {name: 1.0 for name in FEATURE_NAMES}

        scores = []
        for name, importance in fi.items():
            val = float(getattr(tx.features, name, 0.0))
            scale = _FEATURE_SCALE.get(name, 1.0)
            normalized = min(abs(val) / scale, 1.0) if scale > 0 else 0.0

            # Plain feature importance is unsigned magnitude, but each
            # feature has a known polarity relative to fraud risk: for most
            # features a larger raw value pushes risk up, while a small set
            # of protective features (_INVERSE_RISK_FEATURES) push risk down
            # as they grow. Applying that per-feature polarity here gives a
            # genuinely signed contribution instead of forcing every
            # fallback-derived reason code to report "positive" impact
            # regardless of what the underlying feature means.
            polarity = -1.0 if name in _INVERSE_RISK_FEATURES else 1.0
            signed_contribution = polarity * normalized * importance
            scores.append((name, signed_contribution))

        # Rank by magnitude of contribution (as the SHAP path does), not by
        # raw signed value — otherwise every protective (negative) feature
        # would be sorted to the bottom and could never surface in the top_k
        # reason codes even when it was the single biggest driver.
        scores.sort(key=lambda t: abs(t[1]), reverse=True)
        top = scores[:top_k]

        return [self._to_reason_code(tx, name, contrib) for name, contrib in top]

    # ------------------------------------------------------------------ #
    # Shared conversion
    # ------------------------------------------------------------------ #
    def _to_reason_code(self, tx: EnrichedTransaction, name: str, contribution: float) -> ReasonCode:
        value = float(getattr(tx.features, name, 0.0))
        contrib = round(float(contribution), 4)
        impact = "positive" if contrib > 0 else "negative"
        label = _FEATURE_LABELS.get(name, name)

        return ReasonCode(
            feature_name=name,
            label=label,
            value=value,
            impact=impact,
            attribution_score=contrib,
            human_description=self._build_description(label, value, contrib),
        )

    @staticmethod
    def _build_description(label: str, value: float, contrib: float) -> str:
        # Sign and direction must agree at the contrib == 0 boundary — both
        # gated on the same `> 0` condition used for `impact` classification,
        # avoiding a "decreased risk by +0.0000" contradiction.
        direction = "increased" if contrib > 0 else "decreased"
        sign = "+" if contrib > 0 else ""
        return f"{label} (value: {value:.2f}) {direction} risk by {sign}{contrib:.4f}"
