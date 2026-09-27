"""Gemini client with structured output, CLI backend, and dev/test stubbing."""

from __future__ import annotations

import base64
import json
import math
import platform
import re
import shutil
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from directorcut.config import Settings
from directorcut.exceptions import FFmpegError, GeminiError
from directorcut.ffmpeg import run_ffmpeg
from directorcut.log import get_logger, register_secrets
from directorcut.safety.ledger import active_ledger, estimate_text_tokens
from directorcut.token_budget import estimate_video_tokens
from directorcut.utils import temp_dir

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Transient failures worth retrying: rate limiting and server-side errors.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Auth/billing rejections are a property of the KEY, not the model - on these
# the failover plan skips straight to the next key instead of walking the
# model chain on a dead key. (402 = payment required, e.g. depleted credits.)
_KEY_FATAL_STATUS_CODES = {401, 402, 403}


class _ApiCallError(GeminiError):
    """One (key, model) pairing exhausted its retries or hit a fatal status.

    Carries the last HTTP status so the failover driver in `_call_api` can
    tell a dead key (401/402/403) from a hot model (persistent 429/5xx -
    sibling models have separate quota) from a genuine request bug (other
    non-retryable statuses abort the whole call).
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        key_invalid: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # Gemini reports a bad API key as HTTP 400 "API key not valid", not
        # 401 - flag it so the driver rotates keys instead of aborting.
        self.key_invalid = key_invalid


class _ChainExhaustedError(GeminiError):
    """Every (key, model) pairing in the failover plan was tried and failed.

    Distinct from a plain GeminiError so `call()` can tell "the whole API
    plan is dead" (worth a backend fallback) from "the request itself is
    malformed" (a different backend would fail the same way).
    """

# agy surfaces upstream failures as text inside the error envelope; retry on
# the transient markers (capacity, rate limiting) but not on auth/parse errors.
_AGY_TRANSIENT_MARKERS = (
    "503",
    "unavailable",
    "429",
    "resource_exhausted",
    "rate limit",
    "overloaded",
    "no capacity",
)


def _is_transient_agy_error(exc: GeminiError) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _AGY_TRANSIENT_MARKERS)


_AGY_CORRECTIVE_HINT = (
    "\n\nIMPORTANT: your previous response could not be validated against the "
    "schema. Return ONLY the JSON object, with every required field present "
    "and valid - enum values exactly as listed, time_end strictly after "
    "time_start, no extra keys."
)


def _looks_like_bad_response(exc: GeminiError) -> bool:
    """True when the agent answered but its output failed parsing/validation.

    Matches the phrasing _parse_agy_response raises ("response invalid",
    "no JSON found in response") - worth retrying with a corrective hint,
    unlike transport/binary errors which retrying can't fix.
    """
    message = str(exc).lower()
    return "response invalid" in message or "no json found in response" in message


def _fake_tag_narrative(nodes: list[Any]) -> dict[str, Any]:
    """Return a dev-mode tagging stub with exactly one entry per input node.

    Maps 1:1 by index so callers doing `zip(nodes, result, strict=True)` can
    never hit a length mismatch (a previous version derived the stub by
    re-splitting joined text, which could silently produce a different node
    count than the input and crash the zip). Tags are inferred from simple
    punctuation heuristics so the stub still exercises the CALLBACK_REQ /
    EMOTIONAL_SPIKE paths in tests.
    """
    entries = []
    for i, node in enumerate(nodes, start=1):
        text = getattr(node, "spoken_text", "")
        if text.rstrip().endswith("?"):
            tag = "CALLBACK_REQ"
        elif text.rstrip().endswith("!"):
            tag = "EMOTIONAL_SPIKE"
        else:
            tag = "EXPOSITION"
        entries.append(
            {
                "node_id": f"N_{i:03d}",
                "time_start": "00:00:00.000",
                "time_end": "00:00:05.000",
                "spoken_text": text,
                "thematic_tag": tag,
                "energy_level": "MEDIUM",
            }
        )
    return {"narrative_nodes": entries}


def _fake_visual(_frames: list[bytes]) -> dict[str, Any]:
    """Return a deterministic fake visual result for tests."""
    return {
        "visual_events": [
            {
                "event_id": "V_001",
                "time_start": "00:00:01.000",
                "time_end": "00:00:03.000",
                "classification": "STATE_ACTION",
                "visual_description": "Player runs through an open area.",
            }
        ]
    }


def _to_b64(blob: bytes) -> str:
    """Encode bytes as a URL-safe base64 string for Gemini inline data."""
    return base64.b64encode(blob).decode("ascii")


def _resolve_binary(name: str, override: str | None, env_var: str, install_hint: str) -> Path:
    """Locate a CLI binary, honouring an explicit path override."""
    if override:
        path = Path(override)
        if path.exists():
            return path
        raise GeminiError(f"{env_var} not found: {override}")
    found = shutil.which(name)
    if found:
        path = Path(found)
        # `which` may return the extensionless npm shim on Windows, which
        # CreateProcess can't execute - prefer the .cmd/.exe sibling.
        if platform.system() == "Windows" and path.suffix.lower() not in (
            ".exe",
            ".cmd",
            ".bat",
        ):
            for ext in (".cmd", ".exe", ".bat"):
                sibling = path.with_suffix(ext)
                if sibling.exists():
                    return sibling
        return path
    raise GeminiError(f"{name} CLI not found on PATH. {install_hint} or set {env_var}.")


def _find_cli_binary(settings: Settings) -> Path:
    """Locate the `gemini` CLI binary."""
    return _resolve_binary(
        "gemini",
        settings.gemini_cli_bin,
        "GEMINI_CLI_BIN",
        "Install it (npm i -g @google/gemini-cli)",
    )


def _find_agy_binary(settings: Settings) -> Path:
    """Locate the Antigravity `agy` CLI binary."""
    return _resolve_binary(
        "agy",
        settings.antigravity_cli_bin,
        "ANTIGRAVITY_CLI_BIN",
        "Install it (irm https://antigravity.google/cli/install.ps1 | iex)",
    )


def _timeout_seconds(spec: str) -> float:
    """Convert an agy `--print-timeout` spec like "15m" to seconds + a buffer."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", spec.strip().lower())
    if not match:
        return 960.0
    multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    return float(match.group(1)) * multiplier + 60.0


def _cli_command(binary: Path, args: list[str]) -> list[str]:
    # Windows can't exec .cmd/.bat shims directly - wrap them in cmd.exe.
    if platform.system() == "Windows" and binary.suffix.lower() in (".cmd", ".bat"):
        return ["cmd", "/c", str(binary), *args]
    return [str(binary), *args]


def _strip_code_fence(text: str) -> str:
    """Remove a markdown code fence around a JSON payload if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _close_json(text: str) -> tuple[str | None, str | None]:
    """Return (base, closers) for a possibly truncated JSON document.

    Tracks string state with escapes; `base` gets a closing quote appended
    when the text ends mid-string and `closers` closes every container left
    open. (None, None) signals corruption that isn't truncation - a stray
    closer mid-document - which repair shouldn't touch."""
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            else:
                return None, None
    return text + ('"' if in_str else ""), "".join(reversed(stack))


def _repair_truncated_json(text: str) -> Any | None:
    """Best-effort parse of a response cut off by the output-token cap.

    After closing an open string/containers the remaining tail can still be
    a partial token (dangling `key:` or `tru`), so this walks back to the
    last element boundary and retries, bounded to a few cuts. Consumers of
    list responses (tag/visual batches) already tolerate shortened lists,
    so a recovered prefix is strictly better than dropping the call."""
    base = text.strip()
    if not base:
        return None
    for _ in range(10):
        fixed, closers = _close_json(base)
        if fixed is None or not closers:
            return None
        try:
            return json.loads(fixed + closers)
        except json.JSONDecodeError:
            cut = max(
                base.rfind(","),
                base.rfind(":"),
                base.rfind("{"),
                base.rfind("["),
            )
            if cut <= 0:
                return None
            base = base[:cut].rstrip()
    return None


def _gemini_schema(schema: dict) -> dict:
    """Flatten a pydantic JSON Schema into the `response_schema` subset.

    The legacy `response_schema` field accepts only an OpenAPI-ish subset:
    no `$defs`/`$ref` (nested models must be inlined) and no `title`,
    `pattern`, `default`, or `additionalProperties` keywords. Only used as
    the fallback when the endpoint rejects `response_json_schema`.
    """
    defs = {
        **schema.get("definitions", {}),
        **schema.get("$defs", {}),
    }

    def _resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [_resolve(v) for v in node]
        if not isinstance(node, dict):
            return node
        node = dict(node)
        ref = node.pop("$ref", None)
        if ref:
            name = ref.rsplit("/", 1)[-1]
            resolved = _resolve(defs.get(name, {}))
            # Sibling keys next to a $ref extend the referenced schema.
            resolved.update({k: _resolve(v) for k, v in node.items()})
            node = resolved
        else:
            node = {k: _resolve(v) for k, v in node.items()}
        for key in (
            "title",
            "pattern",
            "default",
            "additionalProperties",
            "$defs",
            "definitions",
        ):
            node.pop(key, None)
        return node

    return _resolve(schema)


class GeminiClient:
    """Minimal Gemini client with structured output, retries, and dev fallback.

    Backend is chosen by `settings.gemini_backend`: "api" uses the HTTP
    generateContent endpoint; "cli" shells out to the `gemini` CLI and
    "antigravity" to the `agy` CLI, both in non-interactive mode
    (`-p` + `--output-format json`) using whatever auth the CLI has.
    """

    def __init__(
        self,
        settings: Settings,
        # Free-tier demand spikes arrive in minutes-long bursts - an
        # unattended run dies if the budget is only a few seconds.
        max_retries: int = 8,
        base_delay: float = 5.0,
    ) -> None:
        self.settings = settings
        self.max_retries = max_retries
        self.base_delay = base_delay
        # Pass 3 prompts are large and the model can think for minutes.
        self._http = httpx.Client(timeout=600.0)
        self._model_override_warned = False
        # httpx errors embed the request URL - and the generateContent URL
        # carries `?key=` - so register configured keys for log redaction
        # even when the caller never ran setup_logging().
        register_secrets(settings.configured_secrets())

    def call(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
        dev_mode_override: dict[str, Any] | None = None,
        model: str | None = None,
        task: str | None = None,
    ) -> T:
        """Call Gemini with a prompt and optional media, returning a validated model.

        `model` overrides `settings.gemini_model` for the HTTP API backend.
        `task` ("visual", "interpret", "narrative", "director", "audio", ...)
        switches the API backend onto the task's configured key + model
        failover chains (see Settings.key_chain/model_chain); it wins over
        `model` when both are given. The CLI/agy backends carry their own
        auth and model flags and ignore both.
        """
        ledger = active_ledger()
        if ledger is not None:
            # Pre-flight: a ledger already over cap (e.g. a previous attempt
            # on this project) aborts before ANY further paid call.
            ledger.check_cap()
        backend = getattr(self.settings, "gemini_backend", "api")
        if self.settings.dev_mode:
            logger.info("Dev/test mode: returning stub response for %s", response_model.__name__)
            stub = dev_mode_override or {}
            try:
                result = response_model.model_validate(stub)
            except ValidationError as exc:
                raise GeminiError(f"Stub response invalid: {exc}") from exc
            # Stubs are recorded at zero cost with their estimated token
            # counts - a dev run's costs.json reads as "what this would
            # have spent" without ever touching the cap.
            model_name = model or (
                self.settings.model_for(task) if task else self.settings.gemini_model
            )
            self._record_call(task, model_name, prompt, media, result, stub=True)
            return result

        if (model or task) and backend != "api" and not self._model_override_warned:
            logger.debug(
                "Per-call model/task routing (%r/%r) ignored by the %s backend",
                model,
                task,
                backend,
            )
            self._model_override_warned = True
        if backend == "cli":
            return self._call_cli(prompt, media, response_model, task=task)
        if backend == "antigravity":
            return self._call_agy(prompt, media, response_model, task=task)
        try:
            return self._call_api(prompt, media, response_model, model=model, task=task)
        except _ChainExhaustedError:
            # Every key x model pairing is dead. A configured backend
            # fallback (e.g. the agy CLI, which carries its own auth) is
            # the last resort before the run dies.
            fallback = getattr(self.settings, "backend_fallback", None)
            if fallback != "antigravity":
                raise
            logger.warning(
                "API key/model plan exhausted%s - falling back to the "
                "antigravity backend for this call",
                f" for task '{task}'" if task else "",
            )
            if model:
                logger.debug(
                    "Per-call model override %r ignored by the agy fallback "
                    "(agy uses ANTIGRAVITY_CLI_MODEL)",
                    model,
                )
            return self._call_agy(prompt, media, response_model, task=task)

    def _record_call(
        self,
        task: str | None,
        model: str,
        prompt: str,
        media: list[bytes] | None,
        result: BaseModel,
        stub: bool = False,
    ) -> None:
        """Meter one completed call into the active cost ledger (if any).

        Token counts are heuristic: len/4 for text, the token_budget frame
        rate for media. Only successful calls record - a retried call's
        failures are invisible to billing anyway (they returned nothing).
        """
        ledger = active_ledger()
        if ledger is None:
            return
        input_tokens = estimate_text_tokens(prompt)
        if media:
            input_tokens += estimate_video_tokens(len(media))
        output_tokens = estimate_text_tokens(result.model_dump_json())
        ledger.record_gemini(
            task=task or "unknown",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stub=stub,
        )

    def _call_cli(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
        task: str | None = None,
    ) -> T:
        """Run the prompt through the `gemini` CLI and validate the response."""
        binary = _find_cli_binary(self.settings)
        with temp_dir() as work_dir:
            full_prompt = prompt
            if media:
                refs = []
                for i, blob in enumerate(media):
                    frame = work_dir / f"media_{i:04d}.jpg"
                    frame.write_bytes(blob)
                    # @-mentions are expanded client-side by the CLI, so the
                    # model sees the files without any tool-use approval.
                    refs.append(f"@{frame.as_posix()}")
                full_prompt = (
                    f"{prompt}\n\nAttached media files:\n" + "\n".join(refs)
                )
            full_prompt += (
                "\n\nRespond with ONLY a JSON object matching the required schema."
            )

            args = ["--output-format", "json"]
            if self.settings.gemini_cli_model:
                args += ["-m", self.settings.gemini_cli_model]
            args += ["-p", full_prompt]

            cmd = _cli_command(binary, args)
            logger.info("Running Gemini CLI: %s", " ".join(cmd[:6]) + " ...")
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600, check=False
                )
            except subprocess.TimeoutExpired as exc:
                raise GeminiError("Gemini CLI timed out after 600s") from exc
            except OSError as exc:
                raise GeminiError(f"Could not run Gemini CLI: {exc}") from exc

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-500:]
            raise GeminiError(f"Gemini CLI exited {proc.returncode}: {tail}")

        # The CLI can print log lines ("Loaded cached credentials." etc.)
        # before the JSON envelope - decode the first complete JSON object.
        try:
            start = proc.stdout.index("{")
            envelope, _ = json.JSONDecoder().raw_decode(proc.stdout[start:])
        except (ValueError, json.JSONDecodeError) as exc:
            raise GeminiError(
                f"Gemini CLI produced no JSON output: {proc.stdout[-300:]}"
            ) from exc

        text = envelope.get("response", "")
        try:
            parsed = json.loads(_strip_code_fence(text))
            result = response_model.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("Gemini CLI response failed to parse: %s", exc)
            raise GeminiError(f"Gemini CLI response invalid: {exc}") from exc
        self._record_call(
            task=task,
            model=self.settings.gemini_cli_model or "gemini-cli",
            prompt=prompt,
            media=media,
            result=result,
        )
        return result

    def _call_agy(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
        task: str | None = None,
    ) -> T:
        """Run the prompt through the `agy` CLI headless mode with retries."""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                # Metered at this level (not inside _run_agy_once) so tests
                # patching _run_agy_once keep their signature - the record
                # still fires exactly once per successful call.
                result = self._run_agy_once(prompt, media, response_model)
                self._record_call(
                    task=task,
                    model=self.settings.antigravity_cli_model or "agy",
                    prompt=prompt,
                    media=media,
                    result=result,
                )
                return result
            except GeminiError as exc:
                last_exc = exc
                if attempt == self.max_retries - 1:
                    raise
                if _is_transient_agy_error(exc):
                    delay = self.base_delay * (2**attempt)
                    logger.warning(
                        "agy call failed (attempt %d/%d): %s - retrying in %.1fs",
                        attempt + 1,
                        self.max_retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                if _looks_like_bad_response(exc):
                    # The agent answered but its JSON didn't validate (e.g. it
                    # fabricated a time_end equal to time_start). Tell it what
                    # failed and let it self-correct - one bad reply should not
                    # kill a pipeline that already cost tens of minutes.
                    logger.warning(
                        "agy response failed validation (attempt %d/%d): %s - "
                        "retrying with corrective hint",
                        attempt + 1,
                        self.max_retries,
                        exc,
                    )
                    prompt += _AGY_CORRECTIVE_HINT
                    continue
                raise
        raise GeminiError(f"agy call failed after {self.max_retries} attempt(s)") from last_exc

    def _run_agy_once(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
    ) -> T:
        """Single agy headless invocation; raises GeminiError on any failure."""
        binary = _find_agy_binary(self.settings)
        with temp_dir() as work_dir:
            full_prompt = prompt
            if media:
                # agy has no @-attachment flag in headless mode; as an agent
                # it reads files inside its workspace (the subprocess cwd)
                # itself, with reads auto-allowed there.
                media_note = self._write_agy_media(media, work_dir)
                full_prompt += (
                    "\n\nMedia files are saved in the current directory:\n"
                    + media_note
                    + "\nRead them before answering."
                )

            # NOTE: `--json-schema` is intentionally NOT used - agy applies it
            # to whatever structured step finishes last (often an internal
            # tool call), not to the agent's final reply. The schema goes in
            # the prompt instead, and `response` is parsed for JSON.
            full_prompt += (
                "\n\nRespond with ONLY a JSON object matching this JSON schema:\n"
                + json.dumps(response_model.model_json_schema())
                + "\nNo markdown, no explanation, no extra text."
                # The agent's final turn is often a tool call, which leaves
                # `response` empty - a written result file survives that.
                + "\nAlso write the same JSON object to a file named "
                "result.json in the current directory."
            )

            timeout_spec = self.settings.antigravity_print_timeout
            args = [
                "--output-format",
                "json",
                "--print-timeout",
                timeout_spec,
            ]
            if self.settings.antigravity_cli_model:
                args += ["--model", self.settings.antigravity_cli_model]
            if self.settings.antigravity_cli_effort:
                args += ["--effort", self.settings.antigravity_cli_effort]
            if self.settings.antigravity_skip_permissions:
                # Headless mode soft-denies tools it can't prompt for (incl.
                # `command`, which media inspection needs) - leaving the run
                # "successful" but with an empty response.
                args.append("--dangerously-skip-permissions")

            # The prompt goes to a workspace file, not the command line:
            # Windows caps CreateProcess at ~32K chars and a long narrative
            # map (100+ unscripted nodes) sails past it (WinError 206). The
            # agent reads workspace files itself - one extra tool turn is
            # far cheaper than a prompt-size ceiling.
            (work_dir / "prompt.txt").write_text(full_prompt, encoding="utf-8")
            args += [
                "-p",
                "Read the file prompt.txt in the current directory - it "
                "contains your complete instructions for this task. Follow "
                "them exactly.",
            ]

            cmd = _cli_command(binary, args)
            logger.info(
                "Running agy CLI: %s (prompt %d chars via prompt.txt)",
                " ".join(cmd[:6]) + " ...",
                len(full_prompt),
            )
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=_timeout_seconds(timeout_spec),
                    cwd=work_dir,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise GeminiError(f"agy CLI timed out after {timeout_spec}") from exc
            except OSError as exc:
                raise GeminiError(f"Could not run agy CLI: {exc}") from exc

            # Read inside the temp workspace before it is cleaned up.
            result_path = work_dir / "result.json"
            result_text = result_path.read_text() if result_path.exists() else None

        envelope: dict[str, Any] = {}
        try:
            start = proc.stdout.index("{")
            envelope, _ = json.JSONDecoder().raw_decode(proc.stdout[start:])
        except (ValueError, json.JSONDecodeError):
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "")[-500:]
                raise GeminiError(f"agy CLI exited {proc.returncode}: {tail}") from None
            raise GeminiError(
                f"agy CLI produced no JSON output: {proc.stdout[-300:]}"
            ) from None

        status = envelope.get("status")
        if status != "SUCCESS":
            detail = envelope.get("error") or (proc.stderr or "")[-300:] or "unknown error"
            raise GeminiError(f"agy run status {status or 'ERROR'}: {detail}")

        if result_text is not None:
            try:
                return response_model.model_validate(json.loads(result_text))
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(
                    "agy result.json invalid (%s); falling back to response", exc
                )

        try:
            return self._parse_agy_response(envelope.get("response", ""), response_model)
        except GeminiError:
            denied = envelope.get("denied_actions")
            if denied:
                denied_names = ", ".join(
                    str(a.get("display_name") or a.get("action")) for a in denied
                )
                raise GeminiError(
                    f"agy produced no response - denied tool actions ({denied_names}). "
                    "Set ANTIGRAVITY_SKIP_PERMISSIONS=true or add allow-rules in "
                    "the agy settings.json."
                ) from None
            raise

    def _write_agy_media(self, media: list[bytes], work_dir: Path) -> str:
        """Write media blobs into the agy workspace; return the prompt listing.

        Every file the agent opens costs it a tool round-trip (minutes under
        load), so multi-frame sets are tiled into a single contact sheet it
        can read in one pass. Falls back to per-file listing if tiling fails.
        """
        cap = self.settings.antigravity_media_frame_cap
        sampling_note = ""
        if len(media) > cap:
            # Pass-2 chunks on long videos carry thousands of 1fps frames -
            # one sheet can't show them legibly and hundreds of tool reads
            # would take hours. Sample evenly and report the stride so the
            # agent's time estimates stay anchored to real seconds.
            total = len(media)
            stride = total / cap
            media = [media[int(i * stride)] for i in range(cap)]
            sampling_note = (
                f"NOTE: the {cap} frames below were sampled evenly from a "
                f"{total}-frame sequence - frame k covers "
                f"~{stride:.0f}s from the chunk start (frame 0 ~ 0s, frame "
                f"{cap - 1} ~ {(cap - 1) * stride:.0f}s).\n"
            )

        names = []
        for i, blob in enumerate(media):
            name = f"media_{i:04d}.jpg"
            (work_dir / name).write_bytes(blob)
            names.append(name)
        if len(media) > 1:
            cols = math.ceil(math.sqrt(len(media)))
            rows = math.ceil(len(media) / cols)
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-framerate",
                        "1",
                        "-i",
                        str(work_dir / "media_%04d.jpg"),
                        "-vf",
                        f"scale=480:-1,tile={cols}x{rows}",
                        "-frames:v",
                        "1",
                        str(work_dir / "media_sheet.jpg"),
                    ],
                    self.settings,
                )
                return sampling_note + (
                    f"- media_sheet.jpg (a {cols}x{rows} grid of the {len(media)} "
                    "media frames in order, left-to-right top-to-bottom)"
                )
            except FFmpegError:
                pass
        return sampling_note + "\n".join(f"- {name}" for name in names)

    @staticmethod
    def _parse_agy_response(text: str, response_model: type[T]) -> T:
        """Extract and validate the JSON object inside agy's `response` text.

        The agent may wrap the answer in prose or leave serialized tool-call
        JSON in the text, so candidates are the whole body plus every embedded
        top-level `{...}` object; the LAST candidate that validates wins (the
        final answer follows any tool-call artifacts).
        """
        candidates: list[Any] = []
        with suppress(json.JSONDecodeError):
            candidates.append(json.loads(_strip_code_fence(text)))
        decoder = json.JSONDecoder()
        idx = 0
        while True:
            brace = text.find("{", idx)
            if brace == -1:
                break
            try:
                obj, end = decoder.raw_decode(text[brace:])
            except json.JSONDecodeError:
                idx = brace + 1
                continue
            candidates.append(obj)
            idx = brace + end

        last_error: Exception | None = None
        for candidate in reversed(candidates):
            try:
                return response_model.model_validate(candidate)
            except ValidationError as exc:
                last_error = exc
        logger.error("agy CLI response failed validation: %s", last_error)
        raise GeminiError(
            f"agy CLI response invalid: {last_error or 'no JSON found in response'}"
        ) from last_error

    def _call_api(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
        model: str | None = None,
        task: str | None = None,
    ) -> T:
        """Call the HTTP endpoint, failing over across the key x model plan.

        Plan order is keys-outer, models-inner: quota (429) and capacity
        (5xx) failures are usually per-model, so sibling models are tried on
        the SAME key before burning the next key's allowance. Key-fatal
        statuses (401/402/403) skip straight to the next key; any other
        non-retryable status is a request bug and aborts the call outright.
        """
        keys = self.settings.key_chain(task or "")
        if not keys:
            raise GeminiError(
                "No Gemini API key configured - set GEMINI_PAID_KEY_MAIN, "
                "GEMINI_API_KEY/GEMINI_PAID_KEY, or GEMINI_FREE_KEY."
            )
        models = (
            self.settings.model_chain(task)
            if task
            else [model or self.settings.gemini_model]
        )
        where = f" for task '{task}'" if task else ""

        last_exc: Exception | None = None
        for key_i, api_key in enumerate(keys):
            for model_i, current_model in enumerate(models):
                try:
                    return self._call_api_once(
                        prompt,
                        media,
                        response_model,
                        api_key=api_key,
                        model=current_model,
                        task=task,
                    )
                except _ApiCallError as exc:
                    last_exc = exc
                    status = exc.status_code
                    if status in _KEY_FATAL_STATUS_CODES or exc.key_invalid:
                        logger.warning(
                            "Gemini key %d/%d rejected (HTTP %s)%s - "
                            "switching API key",
                            key_i + 1,
                            len(keys),
                            status,
                            where,
                        )
                        break
                    if status == 400:
                        # A genuine bad request - failover can't fix it.
                        raise GeminiError(str(exc)) from exc
                    if model_i < len(models) - 1:
                        logger.warning(
                            "Gemini model %s failed persistently (HTTP %s)%s - "
                            "falling back to %s",
                            current_model,
                            status or "transport",
                            where,
                            models[model_i + 1],
                        )
                        continue
                    logger.warning(
                        "Gemini model chain exhausted on key %d/%d%s - "
                        "switching API key",
                        key_i + 1,
                        len(keys),
                        where,
                    )

        logger.error(
            "Gemini call failed across %d key(s) x %d model(s)%s",
            len(keys),
            len(models),
            where,
        )
        raise _ChainExhaustedError(
            f"Gemini call failed across {len(keys)} key(s) x "
            f"{len(models)} model(s){where}: {last_exc}"
        ) from last_exc

    def _call_api_once(
        self,
        prompt: str,
        media: list[bytes] | None,
        response_model: type[T],
        api_key: str,
        model: str,
        task: str | None = None,
    ) -> T:
        """One (key, model) pairing with the usual transient-retry loop.

        Raises _ApiCallError (carrying the last HTTP status) when retries
        are exhausted or a non-retryable status arrives; response parse /
        validation failures keep surfacing as plain GeminiError because
        rotating keys or models can't fix a malformed response shape.
        """
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            f"?key={api_key}"
        )
        parts: list[dict[str, Any]] = [{"text": prompt}]
        if media:
            for blob in media:
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": _to_b64(blob),
                        }
                    }
                )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "response_mime_type": "application/json",
                # response_json_schema accepts real JSON Schema ($defs/$ref
                # included); the older response_schema field takes only a
                # sanitized OpenAPI-ish subset - see _gemini_schema.
                "response_json_schema": response_model.model_json_schema(),
            },
        }

        last_exc: Exception | None = None
        last_status: int | None = None
        key_invalid = False
        for attempt in range(self.max_retries):
            try:
                resp = self._http.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                result = response_model.model_validate(parsed)
                self._record_call(
                    task=task,
                    model=model,
                    prompt=prompt,
                    media=media,
                    result=result,
                )
                return result
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                last_status = exc.response.status_code
                body = exc.response.text.lower()
                gen_cfg = payload["generationConfig"]
                schema_rejected = (
                    exc.response.status_code == 400
                    and "response_json_schema" in gen_cfg
                    and "responsejsonschema" in body.replace("_", "")
                )
                if schema_rejected:
                    # Endpoint/model predates response_json_schema - fall
                    # back to the restricted-subset response_schema field.
                    logger.warning(
                        "API rejected response_json_schema; retrying with "
                        "sanitized response_schema"
                    )
                    payload["generationConfig"] = {
                        "response_mime_type": "application/json",
                        "response_schema": _gemini_schema(
                            response_model.model_json_schema()
                        ),
                    }
                    continue
                if exc.response.status_code == 400 and (
                    "api key not valid" in body or "api_key_invalid" in body
                ):
                    # Bad/disabled key - flag it so the driver rotates keys
                    # rather than aborting the call as a request bug.
                    key_invalid = True
                    break
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            except httpx.TransportError as exc:
                last_exc = exc
                last_status = None
            except (KeyError, json.JSONDecodeError, ValidationError) as exc:
                # Output-cap truncation is recoverable - close the cut-off
                # tail and validate the prefix before giving up on the call.
                if isinstance(exc, json.JSONDecodeError):
                    repaired = _repair_truncated_json(text)
                    if repaired is not None:
                        try:
                            result = response_model.model_validate(repaired)
                        except ValidationError:
                            repaired = None
                        else:
                            logger.warning(
                                "Recovered truncated JSON response via "
                                "boundary repair: %s",
                                exc,
                            )
                            self._record_call(
                                task=task,
                                model=model,
                                prompt=prompt,
                                media=media,
                                result=result,
                            )
                            return result
                # Unparseable output goes through the failover driver like a
                # call failure - sibling models may emit valid JSON - rather
                # than killing the run outright.
                logger.error("Gemini call failed: %s", exc)
                raise _ApiCallError(f"Gemini response unparseable: {exc}") from exc

            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt)
                logger.warning(
                    "Gemini call failed (attempt %d/%d): %s - retrying in %.1fs",
                    attempt + 1,
                    self.max_retries,
                    last_exc,
                    delay,
                )
                time.sleep(delay)

        raise _ApiCallError(
            f"Gemini call failed after {self.max_retries} attempt(s): {last_exc}",
            status_code=last_status,
            key_invalid=key_invalid,
        )
