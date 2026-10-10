"""
stage5_gateway/api.py

Main FastAPI application for the Sentinel Stage 5 API Gateway. Exposes
the Sentinel AML pipeline (Stages 1-4, wired together by
PipelineOrchestrator) to the core banking system over REST, plus
case-review endpoints (labeling/annotating, a filtered review queue,
and dashboard summary badges) for investigators — all of which read
from or write to the in-memory CaseStore only, and never touch the
underlying detection/decisioning pipeline.
"""

from contextlib import asynccontextmanager
import asyncio
from io import BytesIO
from time import monotonic
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from sentinel.core import get_logger

from .schemas import (
    TransactionRequest,
    EvaluationResponse,
    CaseReviewUpdateRequest,
    CaseDetailResponse,
    CaseListResponse,
    DashboardSummaryResponse,
)
from .orchestrator import PipelineOrchestrator
from .case_store import CaseRecord, ACTIONS, PRIORITIES, REVIEW_STATUSES
from .services.export_service import build_case_csv, build_case_pdf, safe_filename
from fastapi.middleware.cors import CORSMiddleware
from sentinel_budget_chatbot.router import router as budget_advisor_router

logger = get_logger(__name__)

# Populated during application startup (see `lifespan` below).
orchestrator: PipelineOrchestrator | None = None
MIN_EVALUATION_RESPONSE_SECONDS = 4.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    On startup, instantiates the single long-lived PipelineOrchestrator
    that owns the pipeline's stateful components (feature engineer,
    scorer, graph tracker, investigator, decision engine, case store —
    seeded with sample cases) for the life of the process. Using
    `lifespan` instead of the deprecated `@app.on_event("startup")`
    decorator per current FastAPI guidance.
    """
    global orchestrator

    orchestrator = PipelineOrchestrator()
    logger.info("Sentinel pipeline initialized successfully.")

    yield

    # No explicit teardown required today; reserved for future graceful
    # shutdown of pipeline resources (e.g. flushing graph state, closing
    # model handles) if/when those become necessary.


app = FastAPI(
    title="Sentinel AML Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows requests from any frontend port (e.g. 3000, 5173)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(budget_advisor_router)


def _require_orchestrator() -> PipelineOrchestrator:
    """Shared 503 guard for endpoints that need the orchestrator/case store."""
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sentinel pipeline is still initializing. Please try again shortly.",
        )
    return orchestrator


def _to_case_detail(record: CaseRecord) -> CaseDetailResponse:
    """Map a stored CaseRecord (dossier + decision) into the API response shape."""
    return CaseDetailResponse(
        transaction_id=record.dossier.transaction_id,
        action=record.action_directive.action,
        fraud_score=record.dossier.detection_result.fraud_score,
        primary_reason=record.action_directive.primary_reason,
        requires_manual_review=record.action_directive.requires_manual_review,
        sar_narrative=record.dossier.llm_sar_narrative,
        investigation_priority=record.dossier.investigation_priority,
        review_status=record.dossier.review_status,
        investigator_note=record.dossier.investigator_note,
        updated_at=record.dossier.updated_at,
    )


@app.get("/health")
async def health_check():
    """Basic liveness check for the Sentinel AML Gateway."""
    return {"status": "ok", "service": "Sentinel AML Gateway"}


@app.post("/v1/transactions/evaluate", response_model=EvaluationResponse)
async def evaluate_transaction(request: TransactionRequest) -> EvaluationResponse:
    """
    Evaluate a single transaction through the full Sentinel pipeline
    (Stages 1-4) and return the resulting operational decision.
    """
    orch = _require_orchestrator()

    started_at = monotonic()
    try:
        result = orch.process_transaction(request)
        remaining_delay = MIN_EVALUATION_RESPONSE_SECONDS - (monotonic() - started_at)
        if remaining_delay > 0:
            await asyncio.sleep(remaining_delay)
        return result
    except RuntimeError as e:
        logger.error(
            "Transaction evaluation failed for transaction_id=%s: %s",
            request.transaction_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@app.get("/v1/cases", response_model=CaseListResponse)
async def list_cases(
    review_status: Optional[str] = Query(
        default=None,
        description=f"Filter by review status. One of: {', '.join(REVIEW_STATUSES)}.",
    ),
    action: Optional[str] = Query(
        default=None,
        description=f"Filter by decision action. One of: {', '.join(ACTIONS)}.",
    ),
    priority: Optional[str] = Query(
        default=None,
        description=f"Filter by investigation priority. One of: {', '.join(PRIORITIES)}.",
    ),
    min_fraud_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=100.0,
        description="Only include cases with fraud_score >= this value.",
    ),
    max_fraud_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=100.0,
        description="Only include cases with fraud_score <= this value.",
    ),
    limit: int = Query(default=50, ge=1, le=500, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Number of matching cases to skip."),
) -> CaseListResponse:
    """
    Filtered, paginated review queue over stored cases.

    Read-only: builds its response entirely from CaseStore.list_cases(),
    which only filters/sorts/slices already-stored records. This
    endpoint never calls orchestrator.process_transaction() and never
    touches detection_result, graph_summary, or the stored
    ActionDirective — browsing the queue cannot alter any case.
    """
    orch = _require_orchestrator()

    if review_status is not None and review_status not in REVIEW_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid review_status. Must be one of: {', '.join(REVIEW_STATUSES)}.",
        )
    if action is not None and action not in ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action. Must be one of: {', '.join(ACTIONS)}.",
        )
    if priority is not None and priority not in PRIORITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid priority. Must be one of: {', '.join(PRIORITIES)}.",
        )
    if (
        min_fraud_score is not None
        and max_fraud_score is not None
        and min_fraud_score > max_fraud_score
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_fraud_score cannot be greater than max_fraud_score.",
        )

    records, total = orch.case_store.list_cases(
        review_status=review_status,
        action=action,
        priority=priority,
        min_fraud_score=min_fraud_score,
        max_fraud_score=max_fraud_score,
        limit=limit,
        offset=offset,
    )

    return CaseListResponse(
        items=[_to_case_detail(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/v1/dashboard/summary", response_model=DashboardSummaryResponse)
async def dashboard_summary() -> DashboardSummaryResponse:
    """
    Aggregated counts for the review-dashboard badges: total cases, and
    breakdowns by review_status / action / investigation_priority, plus
    a count of high-priority cases still awaiting review.

    Read-only aggregation over CaseStore.get_summary() — no interaction
    with the detection/decisioning pipeline.
    """
    orch = _require_orchestrator()
    summary = orch.case_store.get_summary()
    return DashboardSummaryResponse(**summary)


@app.get("/v1/cases/{transaction_id}", response_model=CaseDetailResponse)
async def get_case(transaction_id: str) -> CaseDetailResponse:
    """
    Fetch a case's stored verdict (action, fraud_score, SAR narrative,
    etc. as recorded at evaluation time) plus its current investigator
    review_status and investigator_note.
    """
    orch = _require_orchestrator()

    record = orch.case_store.get_case(transaction_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No case found for transaction_id={transaction_id}",
        )
    return _to_case_detail(record)


@app.patch("/v1/cases/{transaction_id}/review", response_model=CaseDetailResponse)
async def update_case_review(
    transaction_id: str, request: CaseReviewUpdateRequest
) -> CaseDetailResponse:
    """
    Update a case's review_status and/or investigator_note.

    IMPORTANT: this endpoint only writes to the in-memory CaseStore via
    orchestrator.case_store.update_review(). It never calls
    orchestrator.process_transaction() and never touches
    detection_result, graph_summary, reason codes, or the stored
    ActionDirective — labeling or annotating a case cannot alter or
    re-trigger scoring, graph analysis, SAR synthesis, or Stage 4
    decisioning.
    """
    orch = _require_orchestrator()

    if not request.has_updates():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of review_status or investigator_note must be provided.",
        )

    record = orch.case_store.update_review(
        transaction_id,
        review_status=request.review_status,
        investigator_note=request.investigator_note,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No case found for transaction_id={transaction_id}",
        )
    return _to_case_detail(record)


@app.get("/v1/cases/{transaction_id}/export")
async def export_case(
    transaction_id: str,
    format: Literal["pdf", "csv"] = Query(description="Report format to download."),
) -> StreamingResponse:
    """Download a finalized case report, including human review metadata."""
    orch = _require_orchestrator()
    record = orch.case_store.get_case(transaction_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No case found for transaction_id={transaction_id}",
        )

    case = _to_case_detail(record)
    if format == "csv":
        content = build_case_csv(case)
        media_type = "text/csv"
    else:
        try:
            content = build_case_pdf(case)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        media_type = "application/pdf"

    return StreamingResponse(
        BytesIO(content),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename(transaction_id, format)}"',
        },
    )
