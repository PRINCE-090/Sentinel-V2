"""
stage5_gateway/case_store.py

In-memory, thread-safe store that persists finalized case records
(Stage 3 dossier + Stage 4 decision) across requests, lets investigators
attach review annotations to them, and serves the filtered review queue
and dashboard-summary aggregations for the UI.

CRITICAL INVARIANT: nothing in this module ever calls back into
PipelineOrchestrator or any Stage 1-4 component. `update_review` mutates
only review_status / investigator_note / updated_at on an already-stored
dossier — it never touches detection_result, graph_summary, reason
codes, or the stored ActionDirective. `list_cases` and `get_summary` are
pure reads/aggregations over what's already stored — they never score,
investigate, or decide anything. Annotating, listing, or summarizing
cases must never re-score, re-investigate, or re-decide them.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from stage2_detection.schemas import DetectionResult
from stage3_investigation.schemas import (
    GraphSummary,
    InvestigationDossier,
    ReviewStatus,
)
from stage4_decisioning.schemas import ActionDirective

REVIEW_STATUSES: Tuple[str, ...] = ("Suspected", "Verified", "Needs Review")
ACTIONS: Tuple[str, ...] = ("BLOCK", "HOLD", "MONITOR", "RELEASE")
PRIORITIES: Tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@dataclass
class CaseRecord:
    dossier: InvestigationDossier
    action_directive: ActionDirective


class CaseStore:
    """
    Simple in-memory case store keyed by transaction_id.

    Lifecycle matches the rest of PipelineOrchestrator's stateful
    components (e.g. GraphTracker): one instance lives for the life of
    the process and resets on restart. Swap this class out for a
    Redis/Postgres-backed implementation later if durability across
    deploys is needed — keep the same save_case / get_case /
    update_review / list_cases / get_summary interface so the API layer
    doesn't have to change.
    """

    def __init__(self, seed_sample_cases: bool = True):
        self._lock = threading.Lock()
        self._cases: Dict[str, CaseRecord] = {}
        if seed_sample_cases:
            self._seed_sample_cases()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_case(
        self, dossier: InvestigationDossier, action_directive: ActionDirective
    ) -> None:
        """
        Called once by the orchestrator after a transaction finishes
        processing (after the hard AML override and SAR synthesis, so
        the stored record reflects the true final verdict). Overwrites
        any prior record for the same transaction_id — re-evaluation
        isn't expected in normal operation, but this keeps the store
        idempotent rather than raising.
        """
        with self._lock:
            self._cases[dossier.transaction_id] = CaseRecord(
                dossier=dossier, action_directive=action_directive
            )

    def update_review(
        self,
        transaction_id: str,
        review_status: Optional[ReviewStatus] = None,
        investigator_note: Optional[str] = None,
    ) -> Optional[CaseRecord]:
        """
        Apply investigator annotations to an existing case record.

        Only review_status, investigator_note, and updated_at are ever
        written here. detection_result, graph_summary, reason codes,
        typology matches, proceeds_trail, and the stored ActionDirective
        are left completely untouched — this is what guarantees labeling
        a case can't alter or re-trigger scoring/decisioning.

        Returns None if no case exists for transaction_id (caller should
        surface this as a 404).
        """
        with self._lock:
            record = self._cases.get(transaction_id)
            if record is None:
                return None
            if review_status is not None:
                record.dossier.review_status = review_status
            if investigator_note is not None:
                record.dossier.investigator_note = investigator_note
            record.dossier.updated_at = datetime.now(timezone.utc)
            return record

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_case(self, transaction_id: str) -> Optional[CaseRecord]:
        """Fetch the stored record for a transaction_id, or None if not found."""
        with self._lock:
            return self._cases.get(transaction_id)

    def list_cases(
        self,
        review_status: Optional[str] = None,
        action: Optional[str] = None,
        priority: Optional[str] = None,
        min_fraud_score: Optional[float] = None,
        max_fraud_score: Optional[float] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[CaseRecord], int]:
        """
        Return (page_of_records, total_matching_count) for the review
        queue, filtered by the given criteria and sorted by most
        recently updated/created first (falls back to transaction_id for
        deterministic ordering when timestamps tie or are absent).

        Purely a filter/sort/slice over the in-memory dict — read-only,
        no side effects, no interaction with any scoring/decisioning
        component.
        """
        with self._lock:
            records = list(self._cases.values())

        def matches(r: CaseRecord) -> bool:
            if review_status is not None and r.dossier.review_status != review_status:
                return False
            if action is not None and r.action_directive.action != action:
                return False
            if (
                priority is not None
                and r.dossier.investigation_priority != priority
            ):
                return False
            score = r.dossier.detection_result.fraud_score
            if min_fraud_score is not None and score < min_fraud_score:
                return False
            if max_fraud_score is not None and score > max_fraud_score:
                return False
            return True

        filtered = [r for r in records if matches(r)]

        def sort_key(r: CaseRecord):
            # Most-recently-touched first. Records without updated_at
            # (never annotated) sort after ones that have been touched,
            # ties broken by transaction_id for stable pagination.
            ts = r.dossier.updated_at
            has_ts = ts is not None
            return (
                not has_ts,
                -(ts.timestamp()) if has_ts else 0,
                r.dossier.transaction_id,
            )

        filtered.sort(key=sort_key)

        total = len(filtered)
        page = filtered[offset : offset + limit]
        return page, total

    def get_summary(self) -> Dict:
        """
        Compute the dashboard aggregate counts. All breakdown keys
        (review_status / action / priority) are always present with a
        count, even 0, so the UI never has to special-case a missing
        key. Read-only aggregation over the store's current contents.
        """
        with self._lock:
            records = list(self._cases.values())

        by_review_status = {k: 0 for k in REVIEW_STATUSES}
        by_action = {k: 0 for k in ACTIONS}
        by_priority = {k: 0 for k in PRIORITIES}
        high_priority_pending = 0

        for r in records:
            rs = r.dossier.review_status
            act = r.action_directive.action
            pri = r.dossier.investigation_priority

            if rs in by_review_status:
                by_review_status[rs] += 1
            if act in by_action:
                by_action[act] += 1
            if pri in by_priority:
                by_priority[pri] += 1

            if rs == "Needs Review" and (
                act in ("BLOCK", "HOLD") or pri in ("CRITICAL", "HIGH")
            ):
                high_priority_pending += 1

        return {
            "total_cases": len(records),
            "by_review_status": by_review_status,
            "by_action": by_action,
            "by_priority": by_priority,
            "high_priority_pending_count": high_priority_pending,
        }

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    def _seed_sample_cases(self) -> None:
        """
        Populate a handful of sample cases at startup so the dashboard
        and review queue have immediate, non-empty data during
        testing/judging, without waiting for real transactions to flow
        through /v1/transactions/evaluate.

        These are constructed directly (bypassing the pipeline entirely)
        precisely so seeding can never be mistaken for, or accidentally
        trigger, real scoring/investigation/decisioning — they're just
        static rows in the store.

        NOTE: DetectionResult and GraphSummary are built with
        `model_construct`/defaults using only the fields this module
        actually reads (`fraud_score`, `transaction_id`, and
        GraphSummary's declared defaults). If stage2's DetectionResult
        gains additional *required* fields with no defaults, extend the
        kwargs below accordingly.
        """
        now = datetime.now(timezone.utc)

        def make_detection_result(transaction_id: str, fraud_score: float) -> DetectionResult:
            # model_construct bypasses validation so this seed helper
            # doesn't need to track every field DetectionResult might
            # require — only the ones downstream code (list/summary/API
            # responses) actually reads: transaction_id, fraud_score.
            return DetectionResult.model_construct(
                transaction_id=transaction_id,
                fraud_score=fraud_score,
            )

        seed_specs = [
            dict(
                transaction_id="SEED-TXN-001",
                sender_account="ACC-1001",
                receiver_account="ACC-2044",
                amount=785000.0,
                fraud_score=91.5,
                priority="CRITICAL",
                action="HOLD",
                primary_reason="hard_aml_safety_override",
                requires_manual_review=True,
                review_status="Verified",
                investigator_note=(
                    "Confirmed layering pattern across 3 linked mule accounts; "
                    "escalated to compliance for SAR filing."
                ),
                mule_ring_flag=True,
                centrality_score=0.82,
                cycle_detected=True,
            ),
            dict(
                transaction_id="SEED-TXN-002",
                sender_account="ACC-3312",
                receiver_account="ACC-4470",
                amount=142000.0,
                fraud_score=63.0,
                priority="HIGH",
                action="MONITOR",
                primary_reason="velocity_threshold_exceeded",
                requires_manual_review=True,
                review_status="Suspected",
                investigator_note=(
                    "Unusual overnight transfer velocity vs. peer profile; "
                    "awaiting KYC follow-up before escalation."
                ),
                mule_ring_flag=False,
                centrality_score=0.41,
                cycle_detected=False,
            ),
            dict(
                transaction_id="SEED-TXN-003",
                sender_account="ACC-5501",
                receiver_account="ACC-6689",
                amount=18500.0,
                fraud_score=22.0,
                priority="LOW",
                action="RELEASE",
                primary_reason="no_risk_factors_triggered",
                requires_manual_review=False,
                review_status="Needs Review",
                investigator_note=None,
                mule_ring_flag=False,
                centrality_score=0.05,
                cycle_detected=False,
            ),
        ]

        with self._lock:
            for spec in seed_specs:
                dossier = InvestigationDossier(
                    transaction_id=spec["transaction_id"],
                    timestamp=now.isoformat(),
                    sender_account=spec["sender_account"],
                    receiver_account=spec["receiver_account"],
                    amount=spec["amount"],
                    detection_result=make_detection_result(
                        spec["transaction_id"], spec["fraud_score"]
                    ),
                    graph_summary=GraphSummary(
                        mule_ring_flag=spec["mule_ring_flag"],
                        centrality_score=spec["centrality_score"],
                        cycle_detected=spec["cycle_detected"],
                    ),
                    investigation_priority=spec["priority"],
                    sar_suggested=spec["priority"] in ("CRITICAL", "HIGH"),
                    review_status=spec["review_status"],
                    investigator_note=spec["investigator_note"],
                    updated_at=now if spec["investigator_note"] else None,
                )
                action_directive = ActionDirective(
                    transaction_id=spec["transaction_id"],
                    action=spec["action"],
                    primary_reason=spec["primary_reason"],
                    all_reasons=[],
                    requires_manual_review=spec["requires_manual_review"],
                )
                self._cases[spec["transaction_id"]] = CaseRecord(
                    dossier=dossier, action_directive=action_directive
                )
