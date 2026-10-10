from __future__ import annotations
import bisect
import math
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from sentinel.core import get_logger, CONFIG
from .schemas import RawTransaction, KYCProfile, DerivedFeatures, EnrichedTransaction
from .transaction_ingester import mcc_risk_tier

logger = get_logger(__name__)

# Bounds -----------------------------------------------------------------
_MAX_HISTORY_RECORDS = 500      # hard cap on retained tx records per account
_MAX_HISTORY_AGE_DAYS = 30      # nothing older than this is needed (max stat window)
_MAX_TRACKED_DEVICES = 500      # LRU cap per account
_MAX_TRACKED_COUNTERPARTS = 500 # LRU cap per account

# Declared-income / occupation strain -------------------------------------
# Income anchors and occupation strain factors are sourced from CONFIG
# (sentinel.core.config.SentinelConfig), not hardcoded here, so compliance
# can retune them via env vars without a code change/redeploy. See
# CONFIG.income_band_anchors / CONFIG.occupation_strain_factors.
_GAMBLING_MCC = "7995"

# Caps prevent a single pathological input (near-zero declared income,
# brand-new account) from producing an unbounded feature value that
# destabilizes gradient boosting splits. Not tunable via CONFIG — these are
# numerical-stability guards, not business thresholds.
_MAX_INCOME_RATIO = 100.0
_MAX_STRAIN_INDEX = 250.0


def _hour_bucket(hour: int) -> int:
    if 2 <= hour <= 5:
        return 3
    if 22 <= hour or hour <= 1:
        return 2
    if 9 <= hour <= 18:
        return 0
    return 1


def _lru_touch(store: "OrderedDict[str, None]", key: str, max_size: int) -> bool:
    """
    O(1) bounded LRU set membership + insert.
    Returns True if `key` was newly inserted (i.e. was novel), False if already present.
    Evicts the least-recently-used entry once max_size is exceeded, preventing
    unbounded memory growth of a plain `set()` implementation.
    """
    is_new = key not in store
    if is_new:
        store[key] = None
    else:
        store.move_to_end(key)
    if len(store) > max_size:
        store.popitem(last=False)  # evict oldest (least-recently-used)
    return is_new


class FeatureEngineer:
    def __init__(self, kyc_lookup: Optional[Callable[[str], Optional[KYCProfile]]] = None):
        """
        kyc_lookup: optional callable(account_id) -> Optional[KYCProfile], used to
        safely cross-reference the *recipient's* KYC profile for
        `receiver_account_age_days`. This is separate from `enriched.kyc`, which
        carries the sender's profile. If not provided (default), receiver-age
        defaults gracefully to 0 rather than raising or blocking the pipeline.
        """
        # Sender-side rolling outbound history, kept strictly sorted ascending by
        # timestamp. `_ts_index[acct]` is a parallel list of timestamps used purely
        # for bisect-based binary search, avoiding O(N) linear scans per tx.
        self._history: Dict[str, List[dict]] = defaultdict(list)
        self._ts_index: Dict[str, List[datetime]] = defaultdict(list)

        # Receiver-side rolling inbound history — mirrors the sender-side structure
        # so the same O(log N) bisect helpers apply to fan-in / inflow features.
        self._receiver_history: Dict[str, List[dict]] = defaultdict(list)
        self._receiver_ts_index: Dict[str, List[datetime]] = defaultdict(list)

        # Bounded LRU membership trackers (sender-side novelty only).
        self._known_devices: Dict[str, "OrderedDict[str, None]"] = defaultdict(OrderedDict)
        self._known_counterparts: Dict[str, "OrderedDict[str, None]"] = defaultdict(OrderedDict)

        self._kyc_lookup = kyc_lookup

    def compute(self, enriched: EnrichedTransaction) -> EnrichedTransaction:
        tx = enriched.raw
        kyc = enriched.kyc
        now = tx.timestamp
        sender_acct = tx.sender_account
        receiver_acct = tx.receiver_account

        # ============================ SENDER SIDE ============================
        s_ts = self._ts_index[sender_acct]
        s_hist = self._history[sender_acct]
        self._prune_stale(s_ts, s_hist, now)

        known_devices = self._known_devices[sender_acct]
        known_cps = self._known_counterparts[sender_acct]

        # `kyc_known_device_hashes` is the durable, authoritative record of this
        # sender's devices — independent of `known_devices` (the in-memory LRU
        # cache), which is ephemeral and starts empty after every process
        # restart, and which a transaction can also reach before it's had a
        # chance to be seeded. Building this set is O(k) in the size of this
        # one user's own device history (bounded, small), and every later
        # membership check against it is then O(1) — this is looked up once
        # per transaction, not once per historical record, so it stays cheap
        # regardless of how many transactions have flowed through the pipeline.
        kyc_known_device_hashes = (
            {d for d in kyc.known_device_hashes if d}
            if kyc is not None and getattr(kyc, "known_device_hashes", None)
            else frozenset()
        )

        # Same rationale as `kyc_known_device_hashes` above, applied to
        # counterparty novelty: the durable, authoritative record of accounts
        # this sender has transacted with before, independent of the
        # ephemeral `known_cps` LRU cache (empty after a restart, or not yet
        # seeded for a transaction that arrives before seeding runs below).
        kyc_known_counterparties = (
            {c for c in kyc.known_counterparties if c}
            if kyc is not None and getattr(kyc, "known_counterparties", None)
            else frozenset()
        )

        if kyc is not None:
            # Seed the live LRU cache from KYC history once, while it's still
            # cold, purely as a warm-up optimization so most lookups can hit
            # the fast in-memory path. This is NOT the source of correctness
            # anymore: device_novelty below always cross-references
            # `kyc_known_device_hashes` directly in addition to the cache, so
            # a restart-emptied cache — or a transaction landing before this
            # seeding step has run — can never misclassify a device the KYC
            # profile already knows about as novel.
            if kyc_known_device_hashes and not known_devices:
                for dev_hash in kyc_known_device_hashes:
                    _lru_touch(known_devices, dev_hash, _MAX_TRACKED_DEVICES)
            
            # Seed the live counterparty cache from KYC history the same way,
            # for the same warm-up reason — not the source of correctness
            # (new_cp always cross-references `kyc_known_counterparties`
            # directly too), just a fast-path optimization.
            if kyc_known_counterparties and not known_cps:
                for cp_acct in kyc.known_counterparties:
                    if cp_acct:
                        _lru_touch(known_cps, cp_acct, _MAX_TRACKED_COUNTERPARTS)

        v1h  = self._count_in_window(s_ts, now, hours=1)
        v6h  = self._count_in_window(s_ts, now, hours=6)
        v24h = self._count_in_window(s_ts, now, hours=24)

        # s_hist is already bounded to the last _MAX_HISTORY_AGE_DAYS days, so
        # this is a direct O(N_recent) pass with no additional timestamp filtering.
        amounts_30d = [h["amount"] for h in s_hist]
        n = len(amounts_30d)
        if n >= 5:
            mu = sum(amounts_30d) / n
            var = sum((a - mu) ** 2 for a in amounts_30d) / n
            std = math.sqrt(var) or (mu * 0.3)
            amount_dev = (tx.amount - mu) / std if std else 0.0
        elif n >= 1:
            mu_own = sum(amounts_30d) / n
            mu_peer = enriched.peer_avg_amount if enriched.peer_avg_amount > 0 else mu_own
            mu_blend = 0.4 * mu_own + 0.6 * mu_peer
            std_blend = mu_blend * 0.5 or 1.0
            amount_dev = (tx.amount - mu_blend) / std_blend if std_blend else 0.0
        else:
            peer_avg = enriched.peer_avg_amount if enriched.peer_avg_amount > 0 else 5_000.0
            peer_std = peer_avg * 0.6 or 1.0
            amount_dev = (tx.amount - peer_avg) / peer_std if peer_std else 0.0

        # income_ratio: transaction size relative to declared/historical monthly
        # volume. Gracefully degrades to peer baseline, then to 0.0 if no signal.
        income_baseline = 0.0
        if kyc is not None and kyc.historical_avg_monthly_volume > 0:
            income_baseline = kyc.historical_avg_monthly_volume
        elif enriched.peer_avg_monthly_volume > 0:
            income_baseline = enriched.peer_avg_monthly_volume
        income_ratio = (tx.amount / income_baseline) if income_baseline > 0 else 0.0

        # A counterparty counts as known if it's either in the live session
        # cache OR in the sender's historical KYC counterparty list — same
        # cold-start fix pattern as `device_novelty` above, so a memory
        # restart (or a transaction landing before the cache is seeded)
        # can't misclassify a genuinely known counterparty as new.
        cp_in_session_cache = tx.receiver_account in known_cps
        cp_in_kyc_history = tx.receiver_account in kyc_known_counterparties
        new_cp = int(not (cp_in_session_cache or cp_in_kyc_history))

        _india_tokens = {"IN", "INDIA", "IND", ""}
        cross_border = int(tx.ip_geolocation.strip().upper() not in _india_tokens)

        hour_bucket = _hour_bucket(now.hour)
        # A device counts as known if it's either in the live session cache
        # OR in the user's historical KYC device list — the KYC check is what
        # closes the cold-start gap (fresh process, or a transaction arriving
        # before the cache got seeded) without waiting on `known_devices` to
        # warm up first.
        device_in_session_cache = tx.device_fingerprint_hash in known_devices
        device_in_kyc_history = tx.device_fingerprint_hash in kyc_known_device_hashes
        device_novelty = int(not (device_in_session_cache or device_in_kyc_history))
        round_amount = int(tx.amount % 100 == 0 and tx.amount >= 1000)

        thresh = CONFIG.reporting_threshold
        proximity = max(0.0, 1.0 - abs(tx.amount - thresh) / thresh) if thresh else 0.0

        mcc_tier = mcc_risk_tier(tx.merchant_category_code)
        drift_amount = 0.0

        # --- Declared-income / occupation strain (independent of volume history) ---
        kyc_missing = int(kyc is None)
        if kyc is not None:
            income_anchor = CONFIG.income_band_anchors.get(
                kyc.declared_income_band, CONFIG.income_anchor_default
            )
            occ_factor = CONFIG.occupation_strain_factors.get(
                kyc.occupation_category, CONFIG.occupation_strain_factor_default
            )
            declared_vs_actual_mismatch_ratio = min(kyc.historical_avg_monthly_volume / max(income_anchor, 1.0), 100.0)
        else:
            # No KYC on file: assume the conservative (most cautious) anchor
            # rather than silently treating the sender as low-risk/MEDIUM.
            income_anchor = CONFIG.income_anchor_default
            occ_factor = CONFIG.occupation_strain_factor_default
            declared_vs_actual_mismatch_ratio = 1.0

        declared_income_ratio = min(tx.amount / max(income_anchor, 1.0), _MAX_INCOME_RATIO)
        salary_strain_index = min(declared_income_ratio * occ_factor, _MAX_STRAIN_INDEX)

        is_gambling_mcc = int(tx.merchant_category_code == _GAMBLING_MCC)
        compounding_risk_signal_count = cross_border + is_gambling_mcc + device_novelty
        strain_x_compound_risk = salary_strain_index * compounding_risk_signal_count

        # ============================ RECEIVER SIDE ===========================
        r_ts = self._receiver_ts_index[receiver_acct]
        r_hist = self._receiver_history[receiver_acct]
        self._prune_stale(r_ts, r_hist, now)

        receiver_velocity_1h = float(self._count_in_window(r_ts, now, hours=1))

        # Distinct-sender count within the 24h window: O(log N) to locate the
        # window boundary + O(K) over just the in-window slice (K = recent
        # inbound txs), never a full scan of receiver history.
        idx_24h = bisect.bisect_left(r_ts, now - timedelta(hours=24))
        window_slice = r_hist[idx_24h:]
        receiver_distinct_senders_24h = len({h["sender"] for h in window_slice})
        inbound_count_24h = len(window_slice)

        # Fan-out: how many outbound txs has this receiver account itself sent
        # in the same 24h window (i.e. does it also behave as a sender)?
        # Reuses the sender-side history structure keyed by the same account id.
        out_ts = self._ts_index.get(receiver_acct, [])
        outbound_count_24h = self._count_in_window(out_ts, now, hours=24) if out_ts else 0

        if outbound_count_24h > 0:
            fan_in_out_ratio = inbound_count_24h / outbound_count_24h
        else:
            # Pure fan-in (money-mule pattern: inflow with no matching outflow yet).
            fan_in_out_ratio = float(inbound_count_24h) if inbound_count_24h > 0 else 0.0

        # Inflow spike ratio: current amount vs. this receiver's own historical
        # inbound average, with graceful cold-start fallback to the peer baseline.
        recv_amounts = [h["amount"] for h in r_hist]
        if recv_amounts:
            avg_inbound = sum(recv_amounts) / len(recv_amounts)
        else:
            avg_inbound = enriched.peer_avg_amount if enriched.peer_avg_amount > 0 else tx.amount
        receiver_inflow_spike_ratio = (tx.amount / avg_inbound) if avg_inbound > 0 else 0.0

        # Recipient KYC cross-reference — safe/optional, never blocks the pipeline.
        receiver_account_age_days = 0
        receiver_historical_avg_monthly_volume = 0.0
        receiver_declared_income_anchor = 0.0
        if self._kyc_lookup is not None:
            try:
                receiver_kyc = self._kyc_lookup(receiver_acct)
                if receiver_kyc is not None:
                    receiver_account_age_days = receiver_kyc.account_age_days
                    receiver_historical_avg_monthly_volume = receiver_kyc.historical_avg_monthly_volume
                    receiver_declared_income_anchor = CONFIG.income_band_anchors.get(
                        receiver_kyc.declared_income_band, CONFIG.income_anchor_default
                    )
            except Exception as e:
                logger.warning(f"Receiver KYC lookup failed for {receiver_acct}: {e}")

        # Relational signal: this tx's amount vs. the receiver's own
        # historical monthly volume. Falls back to the shared peer-volume
        # baseline (same fallback `income_ratio` uses above) when the
        # receiver has no volume history of its own, then to 0.0 if neither
        # signal is available — never a silent divide-by-zero.
        if receiver_historical_avg_monthly_volume > 0:
            receiver_volume_baseline = receiver_historical_avg_monthly_volume
        elif enriched.peer_avg_monthly_volume > 0:
            receiver_volume_baseline = enriched.peer_avg_monthly_volume
        else:
            receiver_volume_baseline = 0.0
        sender_amount_to_receiver_volume_ratio = (
            tx.amount / receiver_volume_baseline if receiver_volume_baseline > 0 else 0.0
        )

        # ============================ STATE UPDATES ===========================
        self._insert_record(
            s_ts, s_hist, now,
            {"ts": now, "amount": tx.amount, "cp": tx.receiver_account,
             "device": tx.device_fingerprint_hash},
        )
        self._cap_size(s_ts, s_hist, _MAX_HISTORY_RECORDS)

        self._insert_record(
            r_ts, r_hist, now,
            {"ts": now, "amount": tx.amount, "sender": tx.sender_account},
        )
        self._cap_size(r_ts, r_hist, _MAX_HISTORY_RECORDS)

        _lru_touch(known_devices, tx.device_fingerprint_hash, _MAX_TRACKED_DEVICES)
        _lru_touch(known_cps, tx.receiver_account, _MAX_TRACKED_COUNTERPARTS)

        enriched.features = DerivedFeatures(
            # Essential
            velocity_1h=v1h,
            velocity_24h=v24h,
            amount_deviation=round(amount_dev, 4),
            income_ratio=round(income_ratio, 4),
            structuring_proximity=round(proximity, 4),
            merchant_risk_tier=mcc_tier,
            receiver_velocity_1h=receiver_velocity_1h,
            receiver_account_age_days=receiver_account_age_days,
            new_counterparty=int(new_cp),
            # Add-ons
            velocity_6h=v6h,
            receiver_inflow_spike_ratio=round(receiver_inflow_spike_ratio, 4),
            receiver_distinct_senders_24h=receiver_distinct_senders_24h,
            fan_in_out_ratio=round(fan_in_out_ratio, 4),
            cross_border=int(cross_border),
            hour_risk_bucket=int(hour_bucket),
            device_novelty=int(device_novelty),
            round_amount=int(round_amount),
            drift_amount_slope=drift_amount,
            declared_income_ratio=round(declared_income_ratio, 4),
            salary_strain_index=round(salary_strain_index, 4),
            compounding_risk_signal_count=int(compounding_risk_signal_count),
            strain_x_compound_risk=round(strain_x_compound_risk, 4),
            kyc_missing=kyc_missing,
            declared_vs_actual_mismatch_ratio=round(declared_vs_actual_mismatch_ratio, 4),
            receiver_historical_avg_monthly_volume=round(receiver_historical_avg_monthly_volume, 4),
            receiver_declared_income_anchor=round(receiver_declared_income_anchor, 4),
            sender_amount_to_receiver_volume_ratio=round(sender_amount_to_receiver_volume_ratio, 4),
        )
        return enriched

    # ---------------------------------------------------------------- #
    # Periodic maintenance (not part of the per-transaction hot path)
    # ---------------------------------------------------------------- #
    def prune_all_stale_accounts(self, now: datetime) -> None:
        """
        Sweep every tracked account in `_history` and `_receiver_history` and
        prune records older than `_MAX_HISTORY_AGE_DAYS`, regardless of
        whether that account has transacted recently.

        `compute()` only calls `_prune_stale` for the sender/receiver
        involved in the *current* transaction, so an account that goes
        fully dormant (no further activity as either sender or receiver)
        never gets touched again — its stale records, and eventually its
        now-empty list, sit in these dicts for the lifetime of the process.
        Since `_history` / `_receiver_history` are plain (defaultdict-backed)
        dicts with no LRU/size cap of their own (unlike `_known_devices` /
        `_known_counterparts`), that's unbounded growth in the number of
        tracked accounts, not just in per-account list size.

        This is intended to be invoked periodically by an external
        scheduler (e.g. a background maintenance task run every few
        minutes/hours) rather than on the per-transaction request path —
        it walks the *entire* account population, so its cost scales with
        total tracked accounts rather than with a single transaction.
        """
        sender_pruned = self._prune_all_accounts(self._ts_index, self._history, now)
        receiver_pruned = self._prune_all_accounts(self._receiver_ts_index, self._receiver_history, now)

        if sender_pruned or receiver_pruned:
            logger.info(
                "prune_all_stale_accounts: dropped %d dormant sender account(s) "
                "and %d dormant receiver account(s)",
                sender_pruned,
                receiver_pruned,
            )

    @staticmethod
    def _prune_all_accounts(
        ts_index: Dict[str, List[datetime]],
        history: Dict[str, List[dict]],
        now: datetime,
    ) -> int:
        """
        Runs `_prune_stale` against every account currently tracked in
        `ts_index`/`history`, then deletes any account whose record list
        is left empty. Returns the number of accounts removed.

        Keys are collected up front into a plain list before mutating
        `ts_index`/`history` (deleting dormant accounts), since deleting
        dict entries while iterating the dict directly would raise a
        `RuntimeError: dictionary changed size during iteration`.
        """
        tracked_accounts = list(ts_index.keys())
        dormant_accounts: List[str] = []

        for acct in tracked_accounts:
            ts_list = ts_index[acct]
            hist = history[acct]
            FeatureEngineer._prune_stale(ts_list, hist, now)
            if not ts_list:
                dormant_accounts.append(acct)

        for acct in dormant_accounts:
            del ts_index[acct]
            del history[acct]

        return len(dormant_accounts)

    @staticmethod
    def _count_in_window(ts_list: List[datetime], now: datetime, hours: int) -> int:
        """O(log N) windowed count via binary search on a sorted timestamp index."""
        cutoff = now - timedelta(hours=hours)
        idx = bisect.bisect_left(ts_list, cutoff)
        return len(ts_list) - idx

    @staticmethod
    def _prune_stale(ts_list: List[datetime], history: List[dict], now: datetime) -> None:
        """O(log N) locate + O(K) slice removal of records past the max stat window."""
        if not ts_list:
            return
        cutoff = now - timedelta(days=_MAX_HISTORY_AGE_DAYS)
        idx = bisect.bisect_left(ts_list, cutoff)
        if idx > 0:
            del ts_list[:idx]
            del history[:idx]

    @staticmethod
    def _insert_record(ts_list: List[datetime], history: List[dict], ts: datetime, record: dict) -> None:
        """
        Maintains the sorted invariant required for bisect-based lookups.
        Fast path: O(1) append for in-order (non-decreasing) timestamp arrival —
        the expected pattern for a real-time stream.
        Fallback: bounded O(N) insert (N <= _MAX_HISTORY_RECORDS) for rare
        out-of-order arrivals, preserving correctness of subsequent searches.
        """
        if not ts_list or ts >= ts_list[-1]:
            ts_list.append(ts)
            history.append(record)
        else:
            pos = bisect.bisect_left(ts_list, ts)
            ts_list.insert(pos, ts)
            history.insert(pos, record)

    @staticmethod
    def _cap_size(ts_list: List[datetime], history: List[dict], max_size: int) -> None:
        """O(K) trim of the oldest records once the hard count cap is exceeded."""
        overflow = len(ts_list) - max_size
        if overflow > 0:
            del ts_list[:overflow]
            del history[:overflow]
