from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class RawTransaction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    timestamp: datetime
    sender_account: str
    receiver_account: str
    amount: float
    channel: str
    device_fingerprint_hash: str
    ip_geolocation: str
    merchant_category_code: str

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_iso8601(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            return datetime.fromisoformat(v)
        return v


class KYCProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: str
    account_age_days: int
    kyc_tier: str
    occupation_category: str
    declared_income_band: str
    historical_avg_monthly_volume: float
    linked_phone_count: int
    linked_email_count: int
    linked_device_count: int
    known_device_hashes: List[str] = Field(default_factory=list)
    # Receiver accounts this account has transacted with before, per KYC/CRM
    # records — the durable counterpart to the in-memory `known_cps` LRU
    # cache, used to close the same cold-start gap for counterparty novelty
    # that `known_device_hashes` closes for device novelty.
    known_counterparties: List[str] = Field(default_factory=list)


class DerivedFeatures(BaseModel):
    """
    Feature matrix — sender + receiver side signals.

    NOTE: this schema is the model's frozen input contract (see
    stage2_detection/lightgbm_scorer.py FEATURE_NAMES). Extended from
    18 -> 23 features to add declared-income/occupation strain signals,
    then 23 -> 26 to add receiver-profile cross-reference signals
    (receiver historical volume, receiver declared-income anchor, and
    the sender-amount-to-receiver-volume relational ratio); any
    previously trained model artifact (models/*.txt) is now STALE and
    must be retrained via train_models.py before deployment, or inference
    will raise on a feature-count mismatch.
    """
    model_config = ConfigDict(from_attributes=True)

    # --- Essential Features ---
    velocity_1h: float = 0.0
    velocity_24h: float = 0.0
    amount_deviation: float = 0.0
    income_ratio: float = 0.0
    structuring_proximity: float = 0.0
    merchant_risk_tier: int = 0
    receiver_velocity_1h: float = 0.0
    receiver_account_age_days: int = 0
    new_counterparty: int = 0

    # --- Add-on Features ---
    velocity_6h: float = 0.0
    receiver_inflow_spike_ratio: float = 0.0
    receiver_distinct_senders_24h: int = 0
    fan_in_out_ratio: float = 0.0
    cross_border: int = 0
    hour_risk_bucket: int = 0
    device_novelty: int = 0
    round_amount: int = 0
    drift_amount_slope: float = 0.0

    # --- Declared-income / occupation strain features ---
    # amount relative to the sender's declared monthly income band (not
    # historical volume) — catches "8x salary in one transfer" patterns
    # that a pure volume-ratio feature misses.
    declared_income_ratio: float = 0.0
    # declared_income_ratio scaled by an occupation-risk factor (salaried/
    # student amplified, business/high-net-worth dampened).
    salary_strain_index: float = 0.0
    # count of independently high-risk conjunction signals present on this
    # transaction: cross-border routing, gambling MCC (7995), new device.
    compounding_risk_signal_count: int = 0
    # explicit interaction term: strain magnitude * how many high-risk
    # signals co-occur with it. Gives the model a direct, pre-computed
    # handle on the compound pattern instead of relying on it to discover
    # a 3-4 way split interaction from few labeled examples.
    strain_x_compound_risk: float = 0.0
    # 1 if no KYC profile was found for the sender. Missing/unverifiable
    # KYC on a large transfer is itself a risk signal, not a neutral
    # default — kept explicit rather than silently defaulting to "MEDIUM".
    kyc_missing: int = 0
    # Ratio of observed historical volume vs self-declared income capacity
    declared_vs_actual_mismatch_ratio: float = 1.0

    # --- Receiver profile cross-reference features ---
    # Receiver's own historical average monthly volume, from the receiver's
    # KYC profile (looked up separately from the sender's `kyc` on
    # EnrichedTransaction). 0.0 if unavailable.
    receiver_historical_avg_monthly_volume: float = 0.0
    # Receiver's declared-income band mapped through the same
    # CONFIG.income_band_anchors table used for the sender, giving a
    # numeric anchor comparable across accounts. 0.0 if unavailable.
    receiver_declared_income_anchor: float = 0.0
    # Relational signal: this transaction's amount vs. the receiver's
    # historical monthly volume baseline. Flags cases where a single
    # inbound transfer dwarfs what the receiver normally handles (e.g. a
    # low-volume/mule-like account suddenly receiving an outsized sum).
    sender_amount_to_receiver_volume_ratio: float = 0.0


class EnrichedTransaction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    raw: RawTransaction
    kyc: Optional[KYCProfile] = None
    features: DerivedFeatures = Field(default_factory=DerivedFeatures)

    peer_avg_amount: float = 0.0
    peer_avg_monthly_volume: float = 0.0

    case_id: Optional[str] = None
