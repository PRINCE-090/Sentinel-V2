from __future__ import annotations
import json
import threading
from typing import Dict, Any, List, Iterator

from sentinel.core import get_logger
from .schemas import RawTransaction

logger = get_logger(__name__)

_VALID_CHANNELS = {"UPI", "NEFT", "RTGS", "CARD", "IMPS", "CASH"}
_VALID_CHANNELS_UPPER = {c.upper() for c in _VALID_CHANNELS}


class TransactionIngester:

    def __init__(self):
        self._processed = 0
        self._rejected = 0
        # Guards `_processed`/`_rejected` counters against lost updates when
        # `ingest()` is called concurrently from multiple worker threads.
        self._counter_lock = threading.Lock()

    def ingest(self, record: Dict[str, Any]) -> RawTransaction | None:
        try:
            tx = self._parse(record)
            with self._counter_lock:
                self._processed += 1
            return tx
        except Exception as exc:
            with self._counter_lock:
                self._rejected += 1
            logger.warning(f"Rejected record {record.get('transaction_id')} — {exc}")
            return None

    def ingest_batch(self, records: List[Dict[str, Any]]) -> List[RawTransaction]:
        return [tx for r in records if (tx := self.ingest(r))]

    def ingest_jsonl(self, path: str) -> Iterator[RawTransaction]:
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    record = json.loads(line)
                    tx = self.ingest(record)
                    if tx:
                        yield tx

    def _parse(self, r: Dict[str, Any]) -> RawTransaction:
        required = ["transaction_id", "sender_account", "receiver_account", "amount"]
        for f in required:
            if f not in r:
                raise ValueError(f"Missing field: {f}")

        # Timestamp normalization (ISO 8601 string parsing, 'Z'-suffix handling,
        # epoch numeric coercion) is delegated entirely to RawTransaction's
        # `_parse_iso8601` field_validator — no manual `datetime.fromisoformat()`
        # call here, keeping parsing logic in exactly one place.
        ts = r.get("timestamp")
        if ts is None:
            raise ValueError("Missing timestamp")

        channel = str(r.get("channel", "UPI")).upper()

        amount = float(r["amount"])
        if amount <= 0:
            raise ValueError("Amount must be positive")

        return RawTransaction(
            transaction_id=str(r["transaction_id"]),
            timestamp=ts,
            sender_account=str(r["sender_account"]),
            receiver_account=str(r["receiver_account"]),
            amount=amount,
            channel=channel,
            device_fingerprint_hash=str(r.get("device_fingerprint_hash", "")),
            ip_geolocation=str(r.get("ip_geolocation", "IN")),
            merchant_category_code=str(r.get("merchant_category_code", "0000")),
        )


def mcc_risk_tier(mcc: str) -> int:
    high_risk = {"7995", "6051", "4829"}    # Gambling, Crypto, Foreign Wire Transfer
    medium_risk = {"6011", "6012"}          # ATM Cash Wires, Financial Institutions
    if mcc in high_risk:
        return 3
    elif mcc in medium_risk:
        return 2
    return 1  # Default/retail (e.g. "5411", "5812")
