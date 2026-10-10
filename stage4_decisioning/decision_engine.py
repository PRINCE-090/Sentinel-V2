"""
stage4_decisioning/decision_engine.py

Deterministic rule engine for Stage 4 of the Sentinel AML pipeline.

Consumes a Stage 3 InvestigationDossier and produces a hard operational
ActionDirective (BLOCK, HOLD, MONITOR, or RELEASE). All matched risk
factors are accumulated so compliance teams have a full audit trail of
why a given action was taken. The primary reason is selected based on 
an explicit severity hierarchy.

Risk-band score thresholds (risk_threshold_medium/high/critical) are
sourced from sentinel.core.config.CONFIG, the same single source of
truth Stage 3 (stage3_investigation.investigation_pipeline) uses to
assign investigation_priority. This guarantees every priority band
Stage 3 can produce — including MEDIUM — has a corresponding rule here,
rather than the two stages drifting out of sync with separately
hardcoded numbers.
"""

from sentinel.core import get_logger, CONFIG

from stage3_investigation.schemas import InvestigationDossier
from .schemas import ActionDirective, DecisionReason


logger = get_logger(__name__)

# Severity ordering used to pick the final action tier.
_SEVERITY_ORDER = {
    "BLOCK": 3,
    "HOLD": 2,
    "MONITOR": 1,
    "RELEASE": 0,
}

# Explicit priority weights for individual rules to determine the primary reason.
# This ensures deterministic behavior regardless of evaluation order.
_RULE_PRIORITIES = {
    # BLOCK Tier
    "mule_ring_detected": 100,
    "fraud_score_critical_threshold": 90,
    
    # HOLD Tier
    "transaction_cycle_detected": 80,
    "critical_investigation_priority": 70,
    "fraud_score_high_threshold": 60,
    
    # MONITOR Tier
    "high_investigation_priority": 50,
    "fraud_score_elevated_threshold": 40,
    "medium_risk_band_watchlist": 30,
    
    # Default
    "no_risk_factors_triggered": 0,
}


class DecisionEngine:
    """
    A deterministic, fully auditable evaluator that maps an
    InvestigationDossier to a final operational ActionDirective.
    """

    def evaluate(self, dossier: InvestigationDossier) -> ActionDirective:
        """
        Evaluate all decision rules against the dossier and return the
        resulting ActionDirective.

        Rules are evaluated across all severity tiers (not short-circuited)
        so that `all_reasons` reflects every risk factor that fired.
        The final action is the highest-severity tier with at least one
        matched rule; `primary_reason` is drawn from that tier's
        highest-weight match via _RULE_PRIORITIES.
        """
        det = dossier.detection_result
        graph = dossier.graph_summary

        matched_reasons: list[tuple[str, DecisionReason]] = []

        # --- BLOCK-tier rules -------------------------------------------------
        if graph.mule_ring_flag:
            matched_reasons.append((
                "BLOCK",
                DecisionReason(
                    rule_name="mule_ring_detected",
                    description=(
                        f"Transaction graph flagged sender "
                        f"{dossier.sender_account} as part of a mule ring "
                        f"(mule_ring_flag=True)."
                    ),
                ),
            ))
        if det.fraud_score >= 90:
            matched_reasons.append((
                "BLOCK",
                DecisionReason(
                    rule_name="fraud_score_critical_threshold",
                    description=(
                        f"Fraud score {det.fraud_score} meets or exceeds the "
                        f"BLOCK threshold of 90."
                    ),
                ),
            ))

        # --- HOLD-tier rules -------------------------------------------------
        if dossier.investigation_priority == "CRITICAL":
            matched_reasons.append((
                "HOLD",
                DecisionReason(
                    rule_name="critical_investigation_priority",
                    description=(
                        "Stage 3 investigation priority was rated CRITICAL."
                    ),
                ),
            ))
        if graph.cycle_detected:
            matched_reasons.append((
                "HOLD",
                DecisionReason(
                    rule_name="transaction_cycle_detected",
                    description=(
                        f"A transaction cycle was detected in the graph "
                        f"involving nodes: {graph.cycle_nodes}."
                    ),
                ),
            ))
        if det.fraud_score >= 75:
            matched_reasons.append((
                "HOLD",
                DecisionReason(
                    rule_name="fraud_score_high_threshold",
                    description=(
                        f"Fraud score {det.fraud_score} meets or exceeds the "
                        f"HOLD threshold of 75."
                    ),
                ),
            ))

        # --- MONITOR-tier rules -------------------------------------------------
        if dossier.investigation_priority == "HIGH":
            matched_reasons.append((
                "MONITOR",
                DecisionReason(
                    rule_name="high_investigation_priority",
                    description=(
                        "Stage 3 investigation priority was rated HIGH."
                    ),
                ),
            ))
        if det.fraud_score >= CONFIG.risk_threshold_high:
            matched_reasons.append((
                "MONITOR",
                DecisionReason(
                    rule_name="fraud_score_elevated_threshold",
                    description=(
                        f"Fraud score {det.fraud_score} meets or exceeds the "
                        f"MONITOR threshold of {CONFIG.risk_threshold_high}."
                    ),
                ),
            ))
        # Closes the bounded-context gap between Stage 3 and Stage 4: Stage 3
        # starts bucketing scores as MEDIUM priority at risk_threshold_medium
        # (30.0), but until this rule existed, Stage 4 had no rule that fired
        # below risk_threshold_high (60.0). A MEDIUM-priority transaction
        # (e.g. fraud_score=50.6) would match nothing above and silently
        # default to a clean RELEASE with no_risk_factors_triggered — even
        # though Stage 3 had explicitly flagged it as above baseline risk.
        # MEDIUM-band transactions now get a passive watch-list MONITOR
        # action instead of slipping through unflagged.
        if dossier.investigation_priority == "MEDIUM":
            matched_reasons.append((
                "MONITOR",
                DecisionReason(
                    rule_name="medium_risk_band_watchlist",
                    description=(
                        f"Fraud score {det.fraud_score} falls within the "
                        f"MEDIUM risk band ({CONFIG.risk_threshold_medium}-"
                        f"{CONFIG.risk_threshold_high}); flagged for passive "
                        f"watch-list monitoring rather than released clean."
                    ),
                ),
            ))

        # --- Determine final action -------------------------------------------------
        if matched_reasons:
            action = max(
                (tier for tier, _ in matched_reasons),
                key=lambda t: _SEVERITY_ORDER[t],
            )
        else:
            action = "RELEASE"

        all_reasons = [reason for _, reason in matched_reasons]

        if action == "RELEASE":
            # Nothing matched: record why RELEASE was the default outcome.
            all_reasons = [
                DecisionReason(
                    rule_name="no_risk_factors_triggered",
                    description=(
                        f"Fraud score {det.fraud_score}, investigation "
                        f"priority {dossier.investigation_priority}, and "
                        f"graph indicators did not meet any BLOCK, HOLD, or "
                        f"MONITOR threshold."
                    ),
                )
            ]
            primary_reason = all_reasons[0].rule_name
        else:
            # Primary reason is the highest-severity rule within the winning tier 
            # based on explicit RULE_PRIORITIES weights.
            tier_reasons = [r for tier, r in matched_reasons if tier == action]
            primary_reason = max(
                tier_reasons, 
                key=lambda r: _RULE_PRIORITIES.get(r.rule_name, 0)
            ).rule_name

        requires_manual_review = action in ("BLOCK", "HOLD")

        logger.info(
            "Decision for transaction_id=%s: action=%s primary_reason=%s "
            "requires_manual_review=%s (%d total reasons)",
            dossier.transaction_id,
            action,
            primary_reason,
            requires_manual_review,
            len(all_reasons),
        )

        return ActionDirective(
            transaction_id=dossier.transaction_id,
            action=action,
            primary_reason=primary_reason,
            all_reasons=all_reasons,
            requires_manual_review=requires_manual_review,
        )