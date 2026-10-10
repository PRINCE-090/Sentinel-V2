"""
stage5_gateway/orchestrator.py

Pipeline orchestrator for the Sentinel Stage 5 API Gateway.

`PipelineOrchestrator` wires together Stages 1 through 4 — KYC-aware
feature engineering, dual-model (LightGBM + anomaly) fraud scoring via
score fusion, deep investigation, and automated decisioning — behind a
single `process_transaction` entry point that maps a gateway
`TransactionRequest` to a gateway `EvaluationResponse`.

Execution order is deliberately: Stage 3 gathers investigation data ->
Stage 4 decides -> hard AML safety override (if any) -> SAR narrative
synthesis (only if the final action warrants it) -> persist the final
case record. The LLM-backed SAR narrative is the most expensive step in
the pipeline, so it must run last and only conditionally — RELEASE'd
transactions never pay for it. The case is persisted only after all of
that is final, so /v1/cases lookups and review-label updates always see
the true final verdict, never an intermediate one.
"""

from sentinel.core import get_logger

from .schemas import TransactionRequest, EvaluationResponse
from .case_store import CaseStore

from stage1_ingestion.schemas import RawTransaction, EnrichedTransaction
from stage1_ingestion.feature_engineer import FeatureEngineer
from stage1_ingestion.kyc_enricher import KYCEnricher

from stage2_detection.lightgbm_scorer import LightGBMScorer
from stage2_detection.extended_if_scorer import AnomalyScorer
from stage2_detection.score_fusion import fuse_scores

from stage3_investigation.graph_tracker import GraphTracker
from stage3_investigation.investigation_pipeline import InvestigationPipeline

from stage4_decisioning.decision_engine import DecisionEngine
from stage4_decisioning.schemas import DecisionReason


class PipelineOrchestrator:
    """
    End-to-end orchestrator that drives a single transaction through
    Sentinel's full Stage 1 -> Stage 4 pipeline and returns the final
    gateway-facing EvaluationResponse.

    One orchestrator instance owns long-lived, stateful pipeline
    components (the KYC enricher, feature engineer, both Stage 2
    scorers, the graph tracker, the investigator, the decision engine,
    and the case store) so that graph state, model resources, and
    persisted case records survive across transactions rather than
    being rebuilt on every request.
    """

    def __init__(self):
        self.logger = get_logger(__name__)

        self._kyc_enricher = KYCEnricher()

        # FeatureEngineer needs a synchronous KYC lookup to enrich
        # profile-derived features; `enrich()` returns a
        # (kyc_profile, peer_avg_amt, peer_avg_vol) tuple, so we only
        # hand it the profile (index 0) here.
        self._feature_engineer = FeatureEngineer(
            kyc_lookup=lambda acct: self._kyc_enricher.enrich(acct)[0]
        )

        self._lgb_scorer = LightGBMScorer()
        self._anomaly_scorer = AnomalyScorer()

        self._graph_tracker = GraphTracker()
        self._investigator = InvestigationPipeline(self._lgb_scorer, self._graph_tracker)
        self._decision_engine = DecisionEngine()

        # Persists finalized case records (dossier + decision) so they
        # can be fetched and annotated with investigator review labels
        # via the /v1/cases endpoints, independent of and without
        # affecting the scoring/decisioning components above.
        self.case_store = CaseStore()

    def process_transaction(self, req: TransactionRequest) -> EvaluationResponse:
        """
        Run a single inbound transaction through the full Sentinel
        pipeline (Stages 1-4) and return the resulting EvaluationResponse.

        Any failure at any stage is caught, logged with the offending
        transaction_id, and re-raised as a RuntimeError so the gateway
        layer can translate it into an appropriate HTTP error response
        without leaking internal stage-level exception types.
        """
        try:
            # --- Step 1: map the gateway request into a Stage 1 RawTransaction ---
            # device_fingerprint_hash / ip_geolocation / merchant_category_code
            # are now first-class (optional) fields on TransactionRequest, each
            # with the same neutral defaults previously hardcoded here, so
            # callers that supply real values get them passed through and
            # callers that don't still get the prior safe fallback behavior.
            raw_tx = RawTransaction(
                transaction_id=req.transaction_id,
                sender_account=req.sender_account,
                receiver_account=req.receiver_account,
                amount=req.amount,
                timestamp=req.timestamp,
                channel=req.channel,
                device_fingerprint_hash=req.device_fingerprint_hash,
                ip_geolocation=req.ip_geolocation,
                merchant_category_code=req.merchant_category_code,
            )

            # --- Step 2: Stage 1 - KYC enrichment + feature engineering ---
            kyc_profile, peer_avg_amt, peer_avg_vol = self._kyc_enricher.enrich(
                raw_tx.sender_account
            )
            enriched_tx = EnrichedTransaction(
                raw=raw_tx,
                kyc=kyc_profile,
                peer_avg_amount=peer_avg_amt,
                peer_avg_monthly_volume=peer_avg_vol,
            )
            enriched_tx = self._feature_engineer.compute(enriched_tx)

            # --- Step 3: Stage 2 - dual-model scoring + fusion ---
            lgb_prob = self._lgb_scorer.predict_proba(enriched_tx)
            anomaly_score = self._anomaly_scorer.score(enriched_tx)

            # Ingest this transaction into the graph tracker *before*
            # fusion so ring/centrality signals reflect live graph state
            # rather than neutral defaults. This is now the single
            # ingestion point for this transaction — InvestigationPipeline
            # no longer re-ingests it in Stage 3 (see the graph-ingestion
            # note in investigation_pipeline.py) to avoid double-counting
            # the edge in fan-in/fan-out, cycle detection, and centrality.
            self._graph_tracker.add_transaction(
                raw_tx.sender_account,
                raw_tx.receiver_account,
                raw_tx.amount,
                raw_tx.timestamp,
            )
            graph_summary = self._graph_tracker.get_graph_summary(raw_tx.sender_account)

            # Drift signals still aren't available at this point in the
            # flow (no drift-tracking component wired in), so fusion still
            # runs with neutral drift defaults matching DetectionResult's
            # own defaults. Ring signals, however, now come from the live
            # graph rather than neutral defaults.
            det_result = fuse_scores(
                transaction_id=raw_tx.transaction_id,
                lgb_prob=lgb_prob,
                anomaly_score=anomaly_score,
                drift_severity=0,
                drift_stage="stable",
                ring_flag=graph_summary.mule_ring_flag,
                ring_centrality=graph_summary.centrality_score,
                enriched=enriched_tx,
            )

            # --- Step 4: Stage 3 - deep investigation (data gathering only) ---
            # `investigate()` no longer makes an LLM call. It returns a
            # dossier with `llm_sar_narrative=None`; the narrative is
            # synthesized later, conditionally, once Stage 4's decision
            # (and any hard override below) is final. The dossier also
            # carries default review_status="Needs Review" and no note
            # until an investigator annotates it via the /v1/cases API.
            dossier = self._investigator.investigate(enriched_tx, det_result)

            # --- Step 5: Stage 4 - deterministic decisioning ---
            action_directive = self._decision_engine.evaluate(dossier)

            # --- Hard AML Safety Override ---
            # Deterministic guard against high-value edge-case transactions
            # slipping through with an unintended RELEASE action despite
            # elevated investigation priority, a 3x jump over historical
            # monthly volume, or a high absolute transaction value.
            historical_vol = (
                enriched_tx.kyc.historical_avg_monthly_volume
                if enriched_tx.kyc
                else enriched_tx.peer_avg_monthly_volume
            )
            is_3x_volume = (historical_vol > 0) and (raw_tx.amount >= 3 * historical_vol)
            is_high_value = raw_tx.amount >= 500000.0
            # Only HIGH/CRITICAL count as "elevated" here — this mirrors
            # DecisionEngine's own severity mapping (HIGH -> MONITOR,
            # CRITICAL -> HOLD; MEDIUM triggers no rule there at all and is
            # treated the same as LOW). Including MEDIUM previously caused
            # this override to force a HOLD on transactions the decision
            # engine had already correctly released (any fraud_score in the
            # 30-59 "MEDIUM priority but below every real threshold" band),
            # regardless of transaction size.
            is_elevated_priority = dossier.investigation_priority in (
                "HIGH",
                "CRITICAL",
            )

            if action_directive.action == "RELEASE" and (
                is_elevated_priority or is_3x_volume or is_high_value
            ):
                # Forced to "HOLD" (not "FLAG_FOR_REVIEW") to satisfy the
                # EvaluationResponse Pydantic Literal constraint.
                action_directive.action = "HOLD"
                action_directive.all_reasons.append(
                    DecisionReason(
                        rule_name="hard_aml_safety_override",
                        description="Overridden to HOLD by deterministic threshold guard.",
                    )
                )
                action_directive.requires_manual_review = True
                # Keep primary_reason in sync with the actual action taken —
                # otherwise a HOLD response can carry a stale
                # "no_risk_factors_triggered" primary_reason from the
                # pre-override RELEASE verdict, which reads as
                # self-contradictory to API consumers.
                action_directive.primary_reason = "hard_aml_safety_override"

            # --- Step 6: Stage 3 (conditional) - SAR narrative synthesis ---
            # Now that the action is truly final (post-override), decide
            # whether it's worth paying for the LLM call at all. RELEASE
            # transactions — the overwhelming majority — skip it entirely;
            # BLOCK/HOLD/MONITOR get a narrative synthesized (with the same
            # Ollama-unavailable -> template fallback as before).
            dossier.llm_sar_narrative = self._investigator.generate_sar_if_warranted(
                tx=enriched_tx,
                dossier=dossier,
                action=action_directive.action,
            )

            # --- Step 6.5: persist the finalized case record ---
            # Must happen after the hard AML override and SAR synthesis so
            # the stored record (and therefore GET /v1/cases/{id} and any
            # subsequent review-label update) reflects the true final
            # verdict, not a pre-override or pre-narrative snapshot. This
            # call only writes to CaseStore's in-memory dict — it does not
            # touch, re-run, or depend on any scoring/decisioning component.
            self.case_store.save_case(dossier, action_directive)

            # --- Step 7: map internal results into the gateway response ---
            return EvaluationResponse(
                transaction_id=action_directive.transaction_id,
                action=action_directive.action,
                fraud_score=det_result.fraud_score,
                primary_reason=action_directive.primary_reason,
                requires_manual_review=action_directive.requires_manual_review,
                sar_narrative=dossier.llm_sar_narrative,
            )

        except Exception as e:
            self.logger.error(
                "Pipeline execution failed for transaction_id=%s: %s",
                req.transaction_id,
                e,
                exc_info=True,
            )
            raise RuntimeError(
                f"Pipeline execution failed for transaction {req.transaction_id}"
            ) from e
