from __future__ import annotations
from typing import Optional

from stage1_ingestion.schemas import EnrichedTransaction
from .schemas import DetectionResult


def _compute_signal_floor(
    enriched: EnrichedTransaction,
    drift_stage: str,
    ring_flag: bool,
) -> float:
    """
    A "floor" score in [0.0, 1.0] derived directly from interpretable Stage 1
    features, independent of the ML models. This exists so that a
    transaction with several strong, human-explainable red flags can't be
    fully suppressed by a low model score (e.g. a novel pattern the models
    haven't learned yet) — it's a rule-based backstop blended into fusion,
    not a replacement for the models.

    Combines two views and takes the stronger of the two:
      - a continuous, magnitude-weighted blend of the amount/structuring/
        fan-in/inflow-spike signals, each pre-normalized to [0,1]; and
      - a discrete count of independently-strong binary flags (cross-border,
        device novelty, active drift, ring membership, extreme deviation,
        near-certain structuring) — several of these firing together should
        raise the floor even if no single one dominates on magnitude.
    """
    f = enriched.features

    amount_dev_component = min(abs(f.amount_deviation) / 5.0, 1.0)
    structuring_component = min(max(f.structuring_proximity, 0.0), 1.0)
    fan_in_component = min(f.fan_in_out_ratio / 6.0, 1.0)
    inflow_spike_component = min(f.receiver_inflow_spike_ratio / 6.0, 1.0)

    continuous_floor = (
        0.30 * amount_dev_component
        + 0.30 * structuring_component
        + 0.20 * fan_in_component
        + 0.20 * inflow_spike_component
    )  # weights sum to 1.0, so this is naturally bounded to [0, 1]

    strong_signals = [
        bool(f.cross_border),
        bool(f.device_novelty),
        drift_stage not in ("stable", ""),
        bool(ring_flag),
        abs(f.amount_deviation) >= 4.0,
        f.structuring_proximity >= 0.95,
        # declared income strain: e.g. an 8.5x-declared-salary transfer by
        # a salaried individual. Kept as its own strong signal (rather than
        # only feeding the continuous blend) so it counts toward the
        # "3+ flags saturate the floor" rule alongside cross-border/device/
        # ring signals — this is what makes the India/offshore/gambling/
        # new-device conjunction saturate even before the model is retrained.
        getattr(f, "salary_strain_index", 0.0) >= 5.0,
    ]
    strong_count = sum(1 for s in strong_signals if s)
    count_floor = min(strong_count / 3.0, 1.0)  # 3+ simultaneous flags saturates the floor

    floor = max(continuous_floor, count_floor)
    return float(max(0.0, min(1.0, floor)))


def fuse_scores(
    transaction_id: str,
    lgb_prob: float,
    anomaly_score: float,
    drift_severity: int,
    drift_stage: str,
    ring_flag: bool,
    ring_centrality: float,
    enriched: Optional[EnrichedTransaction] = None,
) -> DetectionResult:
    """
    Combines the supervised (LightGBM), unsupervised (Isolation Forest),
    drift, and graph-ring signals into a single calibrated fraud_score,
    with an interpretable rule-based floor and high-confidence boosts layered
    on top. Returns a validated `DetectionResult` ready for Stage 3/4.
    """
    drift_norm = drift_severity / 3.0
    ring_norm = min(ring_centrality, 1.0)

    model_score = (
        0.50 * lgb_prob
        + 0.25 * anomaly_score
        + 0.125 * drift_norm
        + 0.125 * ring_norm
    )

    signal_floor = _compute_signal_floor(enriched, drift_stage, ring_flag) if enriched is not None else 0.0

    raw_score = 0.70 * model_score + 0.30 * signal_floor

    # High-confidence boosts: cases where a single strong signal alone
    # warrants escalating the score beyond what the weighted blend gives.
    if lgb_prob > 0.85:
        raw_score += 0.15
    if enriched is not None and enriched.raw.amount > 50_000:
        raw_score += 0.10

    raw_score = max(0.0, raw_score)  # guard against any unexpected negative inputs
    fraud_score = round(min(raw_score * 100.0, 100.0), 1)

    spread = max(lgb_prob, anomaly_score) - min(lgb_prob, anomaly_score)
    if spread < 0.2:
        confidence_band = "high"
    elif spread < 0.45:
        confidence_band = "medium"
    else:
        confidence_band = "low"

    return DetectionResult(
        transaction_id=transaction_id,
        lightgbm_prob=lgb_prob,
        anomaly_score=anomaly_score,
        drift_stage=drift_stage,
        drift_severity=drift_severity,
        ring_flag=bool(ring_flag),
        ring_centrality=ring_centrality,
        fraud_score=fraud_score,
        confidence_band=confidence_band,
    )
