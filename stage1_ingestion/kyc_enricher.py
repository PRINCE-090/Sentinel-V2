from __future__ import annotations
import json
import os
from collections import defaultdict
from typing import Dict, Optional, Tuple

from sentinel.core import get_logger, CONFIG
from .schemas import KYCProfile

logger = get_logger(__name__)


class KYCEnricher:
    def __init__(self, kyc_store_path: str = "data/kyc_profiles.json"):
        self._store: Dict[str, dict] = {}
        # Pre-computed (avg_amount, avg_volume) per (occupation, income_band) group.
        # Built once at load time in a single O(N) pass — replaces the original
        # lazy-on-cache-miss O(N) group scan + per-record Pydantic instantiation.
        self._peer_cache: Dict[Tuple[str, str], Tuple[float, float]] = {}

        # Static fallback, precomputed once instead of recomputed on every call.
        self._default_peer: Tuple[float, float] = (
            CONFIG.reporting_threshold / 2,
            CONFIG.reporting_threshold * 5,
        )

        if os.path.exists(kyc_store_path):
            self._load(kyc_store_path)
            logger.info(f"KYC store loaded: {len(self._store)} profiles")
            logger.info(f"KYC peer groups indexed: {len(self._peer_cache)}")
        else:
            logger.warning(f"KYC store not found at {kyc_store_path}")

    def enrich(self, account_id: str) -> Tuple[Optional[KYCProfile], float, float]:
        raw = self._store.get(account_id)
        if raw is None:
            return None, *self._default_peer

        try:
            profile = KYCProfile(**raw)
        except Exception as e:
            logger.error(f"Malformed KYC profile for {account_id}: {e}")
            return None, *self._default_peer

        peer_avg_amount, peer_avg_volume = self._peer_averages(profile)
        return profile, peer_avg_amount, peer_avg_volume

    def _load(self, path: str) -> None:
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception as e:
            logger.error(f"Failed to load KYC data: {e}")
            return

        if isinstance(data, list):
            self._store = {item["account_id"]: item for item in data}
        else:
            self._store = data

        self._build_peer_index()

    def _build_peer_index(self) -> None:
        """
        Single O(N) pass over the raw profile store, aggregating running sums
        per (occupation_category, declared_income_band) group directly from
        the raw dicts — no per-record Pydantic instantiation, no repeated
        rescans on cache misses. Result is a fully materialized O(1) lookup
        table consumed by `_peer_averages`.
        """
        sums: Dict[Tuple[str, str], float] = defaultdict(float)
        counts: Dict[Tuple[str, str], int] = defaultdict(int)

        for account_id, raw in self._store.items():
            occ = raw.get("occupation_category")
            income = raw.get("declared_income_band")
            vol = raw.get("historical_avg_monthly_volume")
            if occ is None or income is None or vol is None:
                continue
            try:
                vol = float(vol)
            except (TypeError, ValueError):
                logger.warning(
                    f"Skipping profile {account_id}: non-numeric "
                    f"historical_avg_monthly_volume={vol!r}"
                )
                continue

            key = (occ, income)
            sums[key] += vol
            counts[key] += 1

        self._peer_cache = {
            key: (total / counts[key] / 20.0, total / counts[key])
            for key, total in sums.items()
        }

    def _peer_averages(self, profile: KYCProfile) -> Tuple[float, float]:
        """O(1) lookup against the pre-computed peer index, with safe fallback."""
        key = (profile.occupation_category, profile.declared_income_band)
        return self._peer_cache.get(key, self._default_peer)
