from __future__ import annotations
from typing import Any, Mapping
import json
from game_arena.harness import tournament_util
try:
    from mlflow.tracing import start_trace, start_span
    _HAS_TRACING = True
except Exception:
    _HAS_TRACING = False
    start_trace = start_span = None
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