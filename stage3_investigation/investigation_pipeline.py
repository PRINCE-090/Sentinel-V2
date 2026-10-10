"""
stage3_investigation/investigation_pipeline.py

Master orchestrator for Stage 3 of the Sentinel AML pipeline.

Given an enriched transaction and its Stage 2 detection result, this module:
  1. Ingests the transaction into the transaction graph.
  2. Runs SHAP explainability against the fraud-scoring model.
  3. Pulls a graph summary and traces proceeds through the network.
  4. Matches the transaction's behavioral fingerprint against known FATF typologies.
  5. Derives an investigation priority and SAR-filing recommendation.
  6. Returns a fully populated InvestigationDossier with `llm_sar_narrative`
     left unset.

NOTE: SAR narrative synthesis (LLM-backed, with a deterministic template
fallback) is deliberately NOT performed as part of `investigate()`. It is a
comparatively expensive, high-latency step, and the large majority of
transactions flowing through the pipeline are ultimately RELEASE'd by Stage 4
and never need a narrative at all. Running it unconditionally here meant every
transaction — including benign ones — paid the full LLM latency/cost.

Instead, narrative synthesis is exposed as a separate, explicit step —
`generate_sar_if_warranted()` — that callers (see
`stage5_gateway/orchestrator.py`) invoke only after Stage 4's decision (and
any downstream deterministic overrides) is known, and only for actions that
actually require compliance review (BLOCK / HOLD / and optionally MONITOR).
"""

from typing import List, Literal

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - environment without `requests`
    requests = None
    _HAS_REQUESTS = False

from sentinel.core import get_logger, CONFIG

from stage1_ingestion.schemas import EnrichedTransaction
from stage2_detection.schemas import DetectionResult
from stage2_detection.lightgbm_scorer import LightGBMScorer

from .schemas import (
    ReasonCode,
    TypologyMatch,
    GraphSummary,
    InvestigationDossier,
)
from .graph_tracker import GraphTracker
from .shap_explainer import SHAPExplainer
from .typology_dna import TypologyDNA


logger = get_logger(__name__)

Priority = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]

# Actions (as produced by stage4_decisioning.DecisionEngine /
# PipelineOrchestrator's hard AML override) that warrant paying for an
# LLM-synthesized SAR narrative. RELEASE is deliberately excluded — that's
# the whole point of this refactor. MONITOR is included by default since
# monitored transactions still get a compliance-visible record, but callers
# can opt out via `include_monitor=False`.
_SAR_TRIGGER_ACTIONS = frozenset({"BLOCK", "HOLD", "MONITOR"})

_NOT_REQUIRED_MESSAGE = "Not required for released transactions."


class InvestigationPipeline:
    """
    Orchestrates the full Stage 3 investigation workflow for a single
    transaction: graph ingestion, explainability, typology matching, and
    priority scoring. SAR narrative synthesis is a separate, opt-in step —
    see `generate_sar_if_warranted()`.
    """

    def __init__(self, scorer: LightGBMScorer, graph_tracker: GraphTracker):
        self._shap = SHAPExplainer(scorer)
        self._graph_tracker = graph_tracker
        self._typology = TypologyDNA()

        # NOTE: We deliberately do NOT cache Ollama's availability state here.
        # Availability is resolved dynamically on every narrative request so
        # that a late-booting (or transiently restarting) LLM service does
        # not result in a permanent fallback lockout for the lifetime of
        # this pipeline instance.

    # ------------------------------------------------------------------
    # Core entry point
    # ------------------------------------------------------------------
    def investigate(
        self, tx: EnrichedTransaction, det: DetectionResult
    ) -> InvestigationDossier:
        """
        Run the data-gathering portion of the Stage 3 investigation for a
        single transaction and return a populated InvestigationDossier.

        `llm_sar_narrative` is intentionally left as `None` here — no LLM
        call is made as part of this method. Call `generate_sar_if_warranted()`
        afterwards (once Stage 4's decision is known) to populate it.
        """
        # NOTE: Graph ingestion for this transaction now happens upstream,
        # in PipelineOrchestrator.process_transaction(), *before* Stage 2
        # score fusion runs — this lets fuse_scores() use real ring/
        # centrality signals instead of neutral defaults. Do NOT re-ingest
        # here: this is a MultiDiGraph, so a second add_transaction() call
        # would add a duplicate edge and inflate fan-in/fan-out, cycle
        # detection, and centrality for every transaction processed
        # through the orchestrator.
        #
        # This method remains callable directly (e.g. in tests) against a
        # GraphTracker that hasn't had this transaction ingested yet; in
        # that case graph_summary below will simply reflect the graph's
        # state at call time.

        logger.info(
            "Starting investigation for transaction_id=%s sender=%s receiver=%s",
            det.transaction_id,
            tx.raw.sender_account,
            tx.raw.receiver_account,
        )

        # --- Explainability -------------------------------------------------
        reason_codes: List[ReasonCode] = self._shap.explain(tx, top_k=5)

        # --- Graph analysis ---------------------------------------------------
        graph_summary: GraphSummary = self._graph_tracker.get_graph_summary(
            tx.raw.sender_account
        )
        proceeds_trace = self._graph_tracker.trace_proceeds(
            tx.raw.sender_account, max_hops=3
        )

        # --- Typology matching -------------------------------------------------
        behavior_summary = self._typology.build_behavior_summary(tx, det)
        typology_matches: List[TypologyMatch] = self._typology.match(behavior_summary)

        # --- Priority determination -------------------------------------------------
        priority = self._determine_priority(det, graph_summary)
        sar_suggested = priority in ("CRITICAL", "HIGH")

        dossier = InvestigationDossier(
            transaction_id=det.transaction_id,
            timestamp=str(tx.raw.timestamp),
            sender_account=tx.raw.sender_account,
            receiver_account=tx.raw.receiver_account,
            amount=tx.raw.amount,
            detection_result=det,
            investigation_priority=priority,
            sar_suggested=sar_suggested,
            top_reason_codes=reason_codes,
            graph_summary=graph_summary,
            proceeds_trail=proceeds_trace,
            typology_matches=typology_matches,
            llm_sar_narrative=None,
        )

        logger.info(
            "Completed investigation for transaction_id=%s priority=%s sar_suggested=%s "
            "(SAR narrative deferred pending Stage 4 decision)",
            det.transaction_id,
            priority,
            sar_suggested,
        )

        return dossier

    # ------------------------------------------------------------------
    # Conditional SAR narrative synthesis
    # ------------------------------------------------------------------
    def generate_sar_if_warranted(
        self,
        tx: EnrichedTransaction,
        dossier: InvestigationDossier,
        action: str,
        include_monitor: bool = True,
    ) -> str:
        """
        Synthesize the LLM-backed SAR narrative, but ONLY if `action`
        (Stage 4's final decision — including any downstream deterministic
        overrides applied by the orchestrator) actually warrants compliance
        review.

        This is the single call site that should trigger the expensive
        Ollama round-trip. Callers should invoke this *after* Stage 4's
        DecisionEngine (and any hard safety overrides) have produced a
        final action, not before.

        Args:
            tx: The enriched transaction (needed for fields like `channel`
                that live on the raw transaction rather than the dossier).
            dossier: The Stage 3 InvestigationDossier produced by `investigate()`.
            action: The final action directive string (e.g. "BLOCK", "HOLD",
                "MONITOR", "RELEASE") as decided by Stage 4.
            include_monitor: Whether MONITOR-tier actions should also trigger
                narrative synthesis. Defaults to True; pass False if MONITOR
                transactions don't need a compliance-visible SAR draft.

        Returns:
            The synthesized (or template-fallback) narrative string if
            warranted, otherwise a fixed "not required" message. Never
            raises — LLM failures degrade to the deterministic template,
            exactly as before.
        """
        trigger_actions = (
            _SAR_TRIGGER_ACTIONS
            if include_monitor
            else (_SAR_TRIGGER_ACTIONS - {"MONITOR"})
        )

        if action not in trigger_actions:
            logger.info(
                "Skipping SAR narrative synthesis for transaction_id=%s "
                "action=%s (no compliance review required)",
                dossier.transaction_id,
                action,
            )
            return _NOT_REQUIRED_MESSAGE

        logger.info(
            "Action=%s warrants compliance review; synthesizing SAR "
            "narrative for transaction_id=%s",
            action,
            dossier.transaction_id,
        )

        return self._generate_sar_narrative(
            tx=tx,
            det=dossier.detection_result,
            reason_codes=dossier.top_reason_codes,
            graph_summary=dossier.graph_summary,
            proceeds_trace=dossier.proceeds_trail,
            typology_matches=dossier.typology_matches,
            priority=dossier.investigation_priority,
        )

    # ------------------------------------------------------------------
    # Priority logic
    # ------------------------------------------------------------------
    def _determine_priority(
        self, det: DetectionResult, graph_summary: GraphSummary
    ) -> Priority:
        # Thresholds are sourced from CONFIG (sentinel.core.config) rather
        # than hardcoded here, since Stage 4's DecisionEngine gates its own
        # action tiers off these same band boundaries. Keeping them in one
        # place is what guarantees every priority band Stage 3 can produce
        # has a corresponding rule in Stage 4 — see the note on
        # `risk_threshold_medium` in config.py for the bug this fixes.
        if (
            det.fraud_score >= CONFIG.risk_threshold_critical
            or getattr(graph_summary, "mule_ring_flag", False)
        ):
            return "CRITICAL"
        if det.fraud_score >= CONFIG.risk_threshold_high:
            return "HIGH"
        if det.fraud_score >= CONFIG.risk_threshold_medium:
            return "MEDIUM"
        return "LOW"

    # ------------------------------------------------------------------
    # SAR narrative generation (invoked only via generate_sar_if_warranted)
    # ------------------------------------------------------------------
    def _generate_sar_narrative(
        self,
        tx: EnrichedTransaction,
        det: DetectionResult,
        reason_codes: List[ReasonCode],
        graph_summary: GraphSummary,
        proceeds_trace,
        typology_matches: List[TypologyMatch],
        priority: Priority,
    ) -> str:
        """
        Attempt to synthesize a SAR narrative via the local Ollama LLM.
        Falls back to a deterministic, audit-safe template on any failure
        (missing `requests`, timeout, connection error, non-2xx response,
        or empty response body).
        """
        prompt = self._build_prompt(
            tx=tx,
            det=det,
            reason_codes=reason_codes,
            graph_summary=graph_summary,
            proceeds_trace=proceeds_trace,
            typology_matches=typology_matches,
            priority=priority,
        )

        if not _HAS_REQUESTS:
            logger.warning(
                "`requests` library unavailable; falling back to template "
                "narrative for transaction_id=%s",
                det.transaction_id,
            )
            return self._template_narrative(
                tx, det, reason_codes, graph_summary, typology_matches, priority
            )

        try:
            response = requests.post(
                f"{CONFIG.ollama_base_url}/api/generate",
                json={
                    "model": CONFIG.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=CONFIG.ollama_timeout_seconds,
            )
            response.raise_for_status()

            payload = response.json()
            narrative_text = payload.get("response", "").strip()

            if narrative_text:
                return narrative_text

            logger.warning(
                "Ollama returned an empty narrative for transaction_id=%s; "
                "falling back to template",
                det.transaction_id,
            )

        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # failure mode of the LLM call (connection error, timeout, bad
            # JSON, HTTP error) must degrade gracefully to the template
            # rather than breaking the investigation pipeline.
            logger.warning(
                "Ollama narrative generation failed for transaction_id=%s (%s); "
                "falling back to template",
                det.transaction_id,
                exc,
            )

        return self._template_narrative(
            tx, det, reason_codes, graph_summary, typology_matches, priority
        )

    def _build_prompt(
        self,
        tx: EnrichedTransaction,
        det: DetectionResult,
        reason_codes: List[ReasonCode],
        graph_summary: GraphSummary,
        proceeds_trace,
        typology_matches: List[TypologyMatch],
        priority: Priority,
    ) -> str:
        """
        Build the LLM prompt for SAR narrative synthesis.

        CRITICAL FIX 2: only reference fields that actually exist on
        DetectionResult (`fraud_score`, `confidence_band`), per the frozen
        Stage 2 contract in `stage2_detection/schemas.py`. There is no
        `risk_tier` attribute on this schema.
        """
        reason_code_lines = "\n".join(
            f"  - {rc}" for rc in reason_codes
        ) or "  - (no reason codes returned)"

        typology_lines = "\n".join(
            f"  - {tm}" for tm in typology_matches
        ) or "  - (no typology matches)"

        prompt = f"""You are an AML compliance analyst assistant. Draft a concise,
factual Suspicious Activity Report (SAR) narrative for the following transaction.
Write in formal compliance-report language. Do not speculate beyond the data provided.

Transaction ID: {det.transaction_id}
Sender Account: {tx.raw.sender_account}
Receiver Account: {tx.raw.receiver_account}
Amount: {tx.raw.amount}
Channel: {tx.raw.channel}
Timestamp: {tx.raw.timestamp}

Detection Summary:
  Fraud Score: {det.fraud_score}
  Confidence Band: {det.confidence_band}
  Ring Flag: {det.ring_flag}
  Investigation Priority: {priority}

Top Contributing Reason Codes (SHAP):
{reason_code_lines}

Graph Summary:
  {graph_summary}

Proceeds Trace (up to 3 hops):
  {proceeds_trace}

Matched FATF Typologies:
{typology_lines}

Write a 2-4 paragraph SAR narrative summarizing why this transaction warrants
review, referencing the fraud score, confidence band, and the specific reason
codes and typology matches above."""
        return prompt

    def _template_narrative(
        self,
        tx: EnrichedTransaction,
        det: DetectionResult,
        reason_codes: List[ReasonCode],
        graph_summary: GraphSummary,
        typology_matches: List[TypologyMatch],
        priority: Priority,
    ) -> str:
        """
        Deterministic, rule-based fallback narrative used when the LLM
        service is unavailable. References only fields that exist on
        DetectionResult (fraud_score, confidence_band) — no `risk_tier`.
        """
        reason_summary = (
            ", ".join(str(rc) for rc in reason_codes)
            if reason_codes
            else "no significant contributing factors identified"
        )
        typology_summary = (
            ", ".join(str(tm) for tm in typology_matches)
            if typology_matches
            else "no FATF typology matches identified"
        )
        ring_flag_note = (
            "This transaction is associated with a flagged mule ring."
            if getattr(graph_summary, "mule_ring_flag", False)
            else "No mule ring association was flagged in the transaction graph."
        )

        narrative = (
            f"On {tx.raw.timestamp}, a transaction of amount {tx.raw.amount} was "
            f"initiated from account {tx.raw.sender_account} to account "
            f"{tx.raw.receiver_account} via the {tx.raw.channel} channel. "
            f"The Sentinel detection model assigned this transaction a fraud "
            f"score of {det.fraud_score} (confidence: {det.confidence_band}), resulting "
            f"in an investigation priority of {priority}.\n\n"
            f"Key contributing factors identified by the model include: "
            f"{reason_summary}. {ring_flag_note}\n\n"
            f"Behavioral pattern analysis matched the following typologies: "
            f"{typology_summary}.\n\n"
            f"This narrative was generated by rule-based template synthesis "
            f"(LLM narrative service unavailable) and should be reviewed by "
            f"a compliance analyst before filing."
        )
        return narrative
