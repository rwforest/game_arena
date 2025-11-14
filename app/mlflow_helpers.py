from __future__ import annotations
from typing import Any, Mapping
import json
from game_arena.harness import tournament_util
try:
    from mlflow.tracing import start_trace, start_span, SpanType  # type: ignore
    _HAS_TRACING = True
except Exception:
    _HAS_TRACING = False
    start_trace = start_span = None
    SpanType = None  # type: ignore
# Safe span type fallbacks if enum is unavailable
SPAN_TYPE_LLM = getattr(SpanType, "LLM", "llm") if SpanType is not None else "llm"
SPAN_TYPE_TOOL = getattr(SpanType, "TOOL", "tool") if SpanType is not None else "tool"
SPAN_TYPE_CHAIN = getattr(SpanType, "CHAIN", "chain") if SpanType is not None else "chain"
SPAN_TYPE_RETRIEVAL = getattr(SpanType, "RAG_RETRIEVAL", "retrieval") if SpanType is not None else "retrieval"

def disable_provider_autolog() -> None:
    """Disable MLflow GenAI provider autologging for manual tracing.
    Best effort: no-op if integrations are missing.
    """
    try:
        import mlflow.openai as _mloai  # type: ignore
        try:
            _mloai.autolog(disable=True)
        except Exception:
            pass
    except Exception:
        pass
    try:
        import mlflow.anthropic as _mlanth  # type: ignore
        try:
            _mlanth.autolog(disable=True)
        except Exception:
            pass
    except Exception:
        pass
    try:
        import mlflow.genai as _mlgenai  # umbrella in some versions
        try:
            _mlgenai.autolog(disable=True)
        except Exception:
            pass
    except Exception:
        pass

def log_span_outputs(span, ret: tournament_util.GenerateReturn, include_thoughts: bool = False) -> None:
    try:
        outputs = {
            "text": ret.main_response,
            "prompt_tokens": ret.prompt_tokens,
            "generation_tokens": ret.generation_tokens,
            "reasoning_tokens": getattr(ret, "reasoning_tokens", None),
            "request": ret.request_for_logging,
            "response": ret.response_for_logging,
        }
        if include_thoughts:
            outputs["chain_of_thought"] = ret.main_response_and_thoughts
        if hasattr(span, "set_outputs"):
            span.set_outputs(outputs)
    except Exception:
        pass
