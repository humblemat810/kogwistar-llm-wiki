"""Read-only Codex CLI adapter for grounded workbench questions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from collections import deque
from typing import Mapping, Protocol

from pydantic import ValidationError

from .semantic_lens import SemanticLensRequest, SemanticLensSnapshot
from .workbench_cockpit import CockpitAction, CockpitObservation
from .workbench_background import ProgressCallback

LineSink = Callable[[str], None]


class CodexRunner(Protocol):
    def run(
        self,
        *,
        settings: "CodexCliSettings",
        prompt: str,
        progress: ProgressCallback,
        trace_line: LineSink | None = None,
        output_schema: Mapping[str, object] | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class CodexCliSettings:
    executable: str | None = None
    model: str | None = None
    profile: str | None = None
    timeout_seconds: int = 300


class CodexProcessRunner:
    """Run Codex non-interactively and convert JSONL activity into progress."""

    def run(
        self,
        *,
        settings: CodexCliSettings,
        prompt: str,
        progress: ProgressCallback,
        trace_line: LineSink | None = None,
        output_schema: Mapping[str, object] | None = None,
    ) -> str:
        executable = _resolve_codex_executable(settings.executable)
        with tempfile.TemporaryDirectory(prefix="llm-wiki-codex-") as directory:
            output_path = Path(directory) / "answer.txt"
            schema_path = Path(directory) / "output-schema.json"
            codex_args = [
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--config",
                "approval_policy=never",
                "--color",
                "never",
                "--json",
                "-o",
                str(output_path),
            ]
            if output_schema is not None:
                schema_path.write_text(json.dumps(output_schema, sort_keys=True), encoding="utf-8")
                codex_args.extend(["--output-schema", str(schema_path)])
            if settings.model:
                codex_args.extend(["--model", settings.model])
            if settings.profile:
                codex_args.extend(["--profile", settings.profile])
            codex_args.append("-")
            command = _command_for_executable(executable, codex_args)
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdin is not None
            try:
                process.stdin.write(prompt)
                process.stdin.close()
                lines: queue.Queue[str | None] = queue.Queue()

                def read_output() -> None:
                    assert process.stdout is not None
                    for line in process.stdout:
                        lines.put(line.rstrip())
                    lines.put(None)

                reader = threading.Thread(target=read_output, name="llm-wiki-codex-output", daemon=True)
                reader.start()
                deadline = time.monotonic() + max(1, int(settings.timeout_seconds))
                output_finished = False
                trace_tail: deque[str] = deque(maxlen=8)
                while not output_finished:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Codex workbench turn exceeded {settings.timeout_seconds}s")
                    try:
                        line = lines.get(timeout=0.5)
                    except queue.Empty:
                        if process.poll() is not None and not reader.is_alive():
                            break
                        continue
                    if line is None:
                        output_finished = True
                    elif line:
                        trace_tail.append(line)
                        progress()
                        if trace_line is not None:
                            trace_line(line)
                return_code = process.wait(timeout=5)
                reader.join(timeout=2)
                if return_code != 0:
                    detail = " | ".join(trace_tail)
                    raise RuntimeError(f"Codex CLI exited with status {return_code}: {detail}")
                if not output_path.exists():
                    raise RuntimeError("Codex CLI completed without a final response")
                answer = output_path.read_text(encoding="utf-8").strip()
                if not answer:
                    raise RuntimeError("Codex CLI returned an empty final response")
                return answer
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()


class CodexCliResponder:
    """Answer from one bounded lens without granting graph or filesystem writes."""

    def __init__(
        self,
        settings: CodexCliSettings | None = None,
        *,
        runner: CodexRunner | None = None,
        trace_line: LineSink | None = None,
    ) -> None:
        self.settings = settings or CodexCliSettings()
        self.runner = runner or CodexProcessRunner()
        self.trace_line = trace_line

    def __call__(
        self,
        request: SemanticLensRequest,
        snapshot: SemanticLensSnapshot,
        progress: ProgressCallback | None = None,
    ) -> str:
        callback = progress or (lambda: None)
        callback()
        return self.runner.run(
            settings=self.settings,
            prompt=_build_prompt(request, snapshot),
            progress=callback,
            trace_line=self.trace_line,
        )


class CodexCliCockpitResponder:
    """Ask read-only Codex for one typed cockpit action at a time.

    The host, rather than Codex, executes every requested read and validates
    every proposed patch.  This adapter only converts a model response into a
    Pydantic action.
    """

    def __init__(
        self,
        settings: CodexCliSettings | None = None,
        *,
        runner: CodexRunner | None = None,
        trace_line: LineSink | None = None,
    ) -> None:
        self.settings = settings or CodexCliSettings()
        self.runner = runner or CodexProcessRunner()
        self.trace_line = trace_line

    def __call__(
        self,
        request: SemanticLensRequest,
        snapshot: SemanticLensSnapshot,
        observations: tuple[CockpitObservation, ...],
        progress: ProgressCallback | None = None,
    ) -> CockpitAction:
        callback = progress or (lambda: None)
        callback()
        run_kwargs = {
            "settings": self.settings,
            "prompt": _build_cockpit_prompt(request, snapshot, observations),
            "progress": callback,
            "trace_line": self.trace_line,
        }
        validation_error: Exception | None = None
        for attempt in range(2):
            if attempt:
                run_kwargs["prompt"] = (
                    str(run_kwargs["prompt"])
                    + "\n\nYour previous action was invalid: "
                    + str(validation_error)
                    + ". Return one corrected action that conforms to action_schema."
                )
            if isinstance(self.runner, CodexProcessRunner):
                raw = self.runner.run(**run_kwargs, output_schema=CockpitAction.model_json_schema())
            else:
                raw = self.runner.run(**run_kwargs)
            try:
                return CockpitAction.model_validate_json(_json_object(raw))
            except (ValidationError, ValueError) as exc:
                validation_error = exc
                if self.trace_line is not None:
                    self.trace_line(f"cockpit_action_validation_failed attempt={attempt + 1} error={exc}")
        raise RuntimeError(f"Codex returned an invalid cockpit action after repair: {validation_error}") from validation_error


def _resolve_codex_executable(explicit: str | None) -> str:
    configured = explicit or os.environ.get("KOGWISTAR_CODEX_EXECUTABLE")
    executable = configured or shutil.which("codex")
    if not executable:
        raise FileNotFoundError(
            "Codex CLI was not found; set KOGWISTAR_CODEX_EXECUTABLE or add codex to PATH"
        )
    return str(Path(executable).expanduser())


def _command_for_executable(executable: str, args: Sequence[str]) -> list[str]:
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        shell = os.environ.get("COMSPEC", "cmd.exe")
        return [shell, "/d", "/s", "/c", subprocess.list2cmdline([executable, *args])]
    return [executable, *args]


def _build_prompt(request: SemanticLensRequest, snapshot: SemanticLensSnapshot) -> str:
    context = {
        "question": request.query_text,
        "workspace_id": request.workspace_id,
        "lens": snapshot.to_dict(),
    }
    return (
        "You are the central reasoning worker for the Kogwistar llm-wiki workbench.\n"
        "Answer only from the bounded grounded lens JSON below. Cite entity IDs in square brackets. "
        "Do not infer omitted graph facts. If evidence is insufficient, say so plainly. "
        "Graph edits are optional; do not modify files or the graph. If no graph change is warranted, "
        "state `no_change`. Any future edit must remain a proposal requiring host validation and confirmation.\n\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    )


def _build_cockpit_prompt(
    request: SemanticLensRequest,
    snapshot: SemanticLensSnapshot,
    observations: tuple[CockpitObservation, ...],
) -> str:
    context = {
        "question": request.query_text,
        "workspace_id": request.workspace_id,
        "lens": snapshot.to_dict(),
        "observations": [observation.model_dump(mode="json") for observation in observations],
        "action_schema": CockpitAction.model_json_schema(),
    }
    return (
        "You are the read-only central reasoning worker for the Kogwistar llm-wiki cockpit. "
        "Return exactly one JSON object conforming to action_schema, with no Markdown. "
        "Choose answer, no_change, or request_clarification when no more inspection is needed. "
        "You may request resolve_lens, inspect_evidence, or query_history; the host will execute only bounded reads. "
        "A propose_patch action is optional, non-authoritative, and must be grounded in visible source evidence. "
        "Never claim an edit has already been applied. Do not use external knowledge or files.\n\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    )


def _json_object(raw: str) -> str:
    """Accept a bare JSON object or the object accidentally wrapped in prose."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("response did not contain a JSON object")
    return text[start : end + 1]


__all__ = ["CodexCliCockpitResponder", "CodexCliResponder", "CodexCliSettings", "CodexProcessRunner"]
