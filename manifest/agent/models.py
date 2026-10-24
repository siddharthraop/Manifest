"""The three ways the agent gets a model.

    live      Gemini through the Gemini API (the key comes from GOOGLE_API_KEY or GEMINI_API_KEY in
              the environment; MANIFEST never stores it). Every response is recorded to JSONL.
    replay    a recording, played back in order: the public demo and CI, with no key and no cost.
    scripted  a list of steps written in a test.

Replay and scripted runs use the same agent, tools and checks as live runs. Only the model's
responses are fixed; every tool result is computed fresh, so the checks still decide.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

DEFAULT_MODEL = "gemini-3.8-flash"  # design doc §5; stable since 2026-09-02 (ADR 0007)
FALLBACK_MODEL = "gemini-3.5-flash"  # stable, no shutdown before 2027-05-19


class ModelError(RuntimeError):
    pass


class ScriptedLlm(BaseLlm):
    """Steps: {"call": tool_name, "args": {...}} or {"text": "..."}."""

    model: str = "scripted"
    script: list[dict[str, Any]] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if not self.script:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="(script ended)")]))
            return
        step = self.script.pop(0)
        if "call" in step:
            part = types.Part(function_call=types.FunctionCall(name=step["call"], args=step.get("args", {})))
        else:
            part = types.Part(text=step["text"])
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class ReplayLlm(BaseLlm):
    """Plays a recording back, one response per model call."""

    model: str = "replay"
    responses: list[dict[str, Any]] = []

    @classmethod
    def from_file(cls, path: Path) -> ReplayLlm:
        if not path.exists():
            raise ModelError(f"recording not found: {path}")
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return cls(responses=[json.loads(ln)["response"] for ln in lines])

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if not self.responses:
            raise ModelError("the recording has no more responses: the run diverged from the recorded one")
        yield LlmResponse.model_validate(self.responses.pop(0))


class Recorder:
    """An ADK after_model_callback that appends every model response to a JSONL file."""

    def __init__(self, path: Path, model: str) -> None:
        self.path, self.model, self.calls = path, model, 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def __call__(self, callback_context: Any, llm_response: LlmResponse) -> None:
        self.calls += 1
        line = {"n": self.calls, "model": self.model,
                "response": llm_response.model_dump(mode="json", exclude_none=True)}  # fmt: skip
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")


def make_model(
    mode: str,
    model_id: str = DEFAULT_MODEL,
    recording: Path | None = None,
    script: list[dict[str, Any]] | None = None,
) -> tuple[BaseLlm | str, Callable[..., None] | None]:
    """(model for LlmAgent, after_model_callback or None)."""
    if mode == "scripted":
        return ScriptedLlm(script=list(script or [])), (
            Recorder(recording, "scripted") if recording else None
        )
    if mode == "replay":
        if recording is None:
            raise ModelError("replay needs --recording")
        return ReplayLlm.from_file(recording), None
    if mode == "live":
        if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
            raise ModelError(
                "live mode needs a Gemini API key in GOOGLE_API_KEY or GEMINI_API_KEY, for example:\n"
                '  GOOGLE_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key)" \\\n'
                "  manifest fix ..."
            )
        return model_id, (Recorder(recording, model_id) if recording else None)
    raise ModelError(f"unknown mode {mode!r}: use live, replay or scripted")
