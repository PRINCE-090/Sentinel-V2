"""
sentinel.core.pipeline

Defines the *contract* every end-to-end Sentinel pipeline implementation
must satisfy, without depending on any concrete implementation.

IMPORTANT — architectural note:
The actual stage-wiring logic (Stage 1 -> 2 -> 3 -> 4) already lives in
`stage5_gateway.orchestrator.PipelineOrchestrator`, which itself depends
on `sentinel.core` for `CONFIG` / `get_logger`. If this module imported
`PipelineOrchestrator` to build a second "master pipeline" here, it would
either duplicate that wiring logic (two implementations to keep in sync,
guaranteed to drift) or invert the dependency direction of the codebase
(core -> gateway instead of gateway -> core).

Instead, `SentinelPipeline` below is a `typing.Protocol`: a structural
interface. `PipelineOrchestrator` satisfies it simply by having a
matching `process_transaction` method — no inheritance or import in
either direction is required. `core` stays dependency-free; `stage5_gateway`
stays the single source of truth for how the stages are actually wired
together.

If you need a genuinely separate pipeline implementation (e.g. a batch /
offline scorer that doesn't go through the FastAPI request path), build
it as its own class that also satisfies this Protocol — don't extend
`PipelineOrchestrator` for that, and don't put its logic here either;
`core` should stay free of pipeline business logic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SentinelPipeline(Protocol):
    """
    Structural contract for any class that runs a transaction through the
    full Sentinel pipeline (ingestion -> detection -> investigation ->
    decisioning) and returns a final evaluation result.

    `stage5_gateway.orchestrator.PipelineOrchestrator` is the current,
    canonical implementation of this contract. This Protocol exists so
    that:
      - FastAPI route handlers, tests, and any future alternate
        implementation can be typed against `SentinelPipeline` instead of
        importing the concrete `PipelineOrchestrator` class directly.
      - `sentinel.core` never needs to import from `stage5_gateway`.

    The request/response types are intentionally typed as `Any` here
    rather than importing `TransactionRequest` / `EvaluationResponse`
    from `stage5_gateway.schemas` — importing those would reintroduce the
    same upward dependency this Protocol exists to avoid. Concrete
    implementations should narrow these types in their own signatures.
    """

    def process_transaction(self, request: Any) -> Any:
        """
        Run a single transaction through the full pipeline and return the
        final evaluation result (action directive, SAR narrative, etc.).

        Implementations are expected to raise on unrecoverable failure
        rather than returning a partial/undefined result — see
        `PipelineOrchestrator.process_transaction` for the reference
        error-handling behavior (wrap-and-reraise as `RuntimeError`,
        logged with `exc_info=True` before re-raising).
        """
        ...


def is_sentinel_pipeline(obj: Any) -> bool:
    """
    Runtime check for whether `obj` structurally satisfies the
    `SentinelPipeline` contract. Useful in the FastAPI `lifespan` startup
    hook to assert the orchestrator was wired correctly before the app
    starts accepting traffic:

        if not is_sentinel_pipeline(orchestrator):
            raise TypeError("orchestrator does not satisfy SentinelPipeline")
    """
    return isinstance(obj, SentinelPipeline)
