from __future__ import annotations
import json
import os
import sys
from typing import List, Tuple

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score

from sentinel.core import get_logger, CONFIG
from stage1_ingestion.transaction_ingester import TransactionIngester
from stage1_ingestion.kyc_enricher import KYCEnricher
from stage1_ingestion.feature_engineer import FeatureEngineer
from stage1_ingestion.schemas import EnrichedTransaction
from .lightgbm_scorer import LightGBMScorer
from .extended_if_scorer import AnomalyScorer

logger = get_logger(__name__)

RAW_TX_PATH = "data/raw_transactions.jsonl"
KYC_PATH = "data/kyc_profiles.json"
METRICS_PATH = "models/metrics.json"
TRAIN_SPLIT_RATIO = 0.8


def load_and_enrich(
    path: str,
    kyc_enricher: KYCEnricher,
    feature_engineer: FeatureEngineer,
    ingester: TransactionIngester,
) -> Tuple[List[EnrichedTransaction], List[int]]:
    """
    Streams raw_transactions.jsonl once, running each record through
    ingestion -> KYC enrichment -> feature engineering IN FILE ORDER. This
    order dependence is deliberate: FeatureEngineer's velocity/rolling
    features are stateful and must be computed relative to true arrival
    order, not recomputed after-the-fact from a sorted list. The
    ground-truth `is_fraud` label lives only on the raw record and is
    captured here before it's dropped by RawTransaction's schema.
    """
    enriched_txs: List[EnrichedTransaction] = []
    labels: List[int] = []
    last_ts = None
    out_of_order_count = 0
    missing_label_count = 0

    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)

            if "is_fraud" not in record:
                missing_label_count += 1
            label = int(record.get("is_fraud", 0))

            tx = ingester.ingest(record)
            if tx is None:
                continue  # rejection already logged by the ingester

            if last_ts is not None and tx.timestamp < last_ts:
                out_of_order_count += 1
            last_ts = tx.timestamp

            profile, peer_avg_amount, peer_avg_volume = kyc_enricher.enrich(tx.sender_account)
            enriched = EnrichedTransaction(
                raw=tx,
                kyc=profile,
                peer_avg_amount=peer_avg_amount,
                peer_avg_monthly_volume=peer_avg_volume,
            )
            enriched = feature_engineer.compute(enriched)

            enriched_txs.append(enriched)
            labels.append(label)

    if missing_label_count:
        logger.warning(f"{missing_label_count} record(s) in {path} had no `is_fraud` "
                        f"field — defaulted to label 0")
    if out_of_order_count:
        logger.warning(
            f"{out_of_order_count} record(s) in {path} arrived out of chronological "
            f"order — rolling/velocity features were computed relative to file order. "
            f"Verify the source is pre-sorted by timestamp."
        )

    n_fraud = sum(labels)
    logger.info(f"Loaded and enriched {len(enriched_txs)} transactions "
                f"({n_fraud} fraud / {len(labels) - n_fraud} legitimate) from {path}; "
                f"ingester rejected {ingester._rejected} record(s)")
    return enriched_txs, labels


def chronological_split(
    enriched_txs: List[EnrichedTransaction],
    labels: List[int],
    train_ratio: float = TRAIN_SPLIT_RATIO,
):
    """
    Splits by index, not by shuffling — the list is already in chronological
    arrival order from `load_and_enrich`, so a straight prefix/suffix split
    is a true time-based split: train on the past, test on the future. This
    avoids the data leakage a random split would introduce (letting the
    model implicitly see post-test-window behavior via rolling features).
    """
    n = len(enriched_txs)
    split_idx = int(n * train_ratio)
    X_train, y_train = enriched_txs[:split_idx], labels[:split_idx]
    X_test, y_test = enriched_txs[split_idx:], labels[split_idx:]
    logger.info(f"Chronological split: {len(X_train)} train / {len(X_test)} test "
                f"(ratio={train_ratio})")
    return X_train, y_train, X_test, y_test


def evaluate(lgb_scorer: LightGBMScorer, X_test: List[EnrichedTransaction], y_test: List[int]) -> dict:
    y_true = np.asarray(y_test)
    y_prob = np.array([lgb_scorer.predict_proba(tx) for tx in X_test])
    y_pred = (y_prob >= lgb_scorer._threshold).astype(int)

    metrics = {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1_score": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": None,
        "threshold": lgb_scorer._threshold,
        "n_test_samples": int(len(y_test)),
        "n_test_fraud": int(y_true.sum()),
    }

    # ROC-AUC is mathematically undefined with a single class present;
    # guard rather than let sklearn raise and abort the whole run.
    if len(set(y_true.tolist())) > 1:
        metrics["roc_auc"] = float(roc_auc_score(y_true, y_prob))
    else:
        logger.warning("ROC-AUC undefined: test set contains only one class")

    return metrics


def main() -> None:
    if not os.path.exists(KYC_PATH):
        logger.error(f"KYC store not found at {KYC_PATH}")
        sys.exit(1)
    if not os.path.exists(RAW_TX_PATH):
        logger.error(f"Raw transaction file not found at {RAW_TX_PATH}")
        sys.exit(1)

    kyc_enricher = KYCEnricher(KYC_PATH)
    ingester = TransactionIngester()
    # Wire recipient KYC cross-referencing into feature engineering so
    # `receiver_account_age_days` is populated during training the same way
    # it should be wired in the live orchestrator.
    feature_engineer = FeatureEngineer(kyc_lookup=lambda acct: kyc_enricher.enrich(acct)[0])

    enriched_txs, labels = load_and_enrich(RAW_TX_PATH, kyc_enricher, feature_engineer, ingester)
    if not enriched_txs:
        logger.error("No transactions were successfully enriched — aborting training")
        sys.exit(1)

    X_train, y_train, X_test, y_test = chronological_split(enriched_txs, labels)
    if sum(y_train) == 0:
        logger.warning("Training split contains zero positive (fraud) labels — "
                        "scale_pos_weight will degrade to a no-op and the model "
                        "will have nothing to learn the fraud class from")

    # ---- Train supervised model on the full training split ----
    lgb_scorer = LightGBMScorer()
    lgb_scorer.train(X_train, y_train)

    # ---- Train unsupervised model on non-fraud training transactions only ----
    # (Isolation Forest models "normal" behavior; contaminating the fit set
    # with known fraud would bias its notion of what's normal.)
    normal_train_txs = [tx for tx, y in zip(X_train, y_train) if y == 0]
    if not normal_train_txs:
        logger.error("No non-fraud transactions in the training split — cannot fit AnomalyScorer")
        sys.exit(1)
    anomaly_scorer = AnomalyScorer()
    anomaly_scorer.fit(normal_train_txs)

    # ---- Evaluate on the held-out, strictly-future test set ----
    metrics = evaluate(lgb_scorer, X_test, y_test)

    metrics_dir = os.path.dirname(METRICS_PATH)
    if metrics_dir:
        os.makedirs(metrics_dir, exist_ok=True)
    with open(METRICS_PATH, "w") as fh:
        json.dump(metrics, fh, indent=2)

    logger.info("=== Training complete ===")
    logger.info(f"Precision: {metrics['precision']:.4f}")
    logger.info(f"Recall:    {metrics['recall']:.4f}")
    logger.info(f"F1-score:  {metrics['f1_score']:.4f}")
    if metrics["roc_auc"] is not None:
        logger.info(f"ROC-AUC:   {metrics['roc_auc']:.4f}")
    else:
        logger.info("ROC-AUC:   N/A (single-class test set)")
    logger.info(f"Metrics saved to {METRICS_PATH}")


if __name__ == "__main__":
    main()
