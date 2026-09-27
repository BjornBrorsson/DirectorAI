"""Per-run cost accounting: the metered-call ledger behind costs.json.

Every Gemini/ElevenLabs call that happens inside `activate_cost_ledger()`
is recorded into `output/<project>/costs.json`: Gemini calls by task +
model with estimated input/output tokens, ElevenLabs calls by kind with
billed seconds. The file is rewritten atomically after each call so a
crash mid-run loses at most the in-flight call - and a resumed run keeps
accumulating into the same ledger instead of starting over.

Token counts are ESTIMATES: input text uses the len/4 chars-per-token
heuristic and media uses `token_budget.estimate_video_tokens` (the same
263-token/360p-frame rate the chunker budgets with). Dev-mode stub calls
are recorded with `"stub": true` and zero cost - they happened locally -
but still carry their estimated token counts so a dev run's costs.json
reads as "what this run would have spent".

`DIRECTORCUT_COST_CAP_USD` is enforced twice per metered call: BEFORE the
call (a ledger already over cap aborts immediately) and AFTER recording
(the call that pushes the total over still counts, then raises). v1
semantics are abort-with-clear-message via `BudgetExceeded`; a
pause-for-approval flow is a deliberate non-goal for unattended runs.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from directorcut.exceptions import BudgetExceeded
from directorcut.log import get_logger
from directorcut.safety.cost import PRICE_INPUT_PER_1M, PRICE_OUTPUT_PER_1M

logger = get_logger(__name__)

COSTS_FILE = "costs.json"

# Placeholder ElevenLabs rates (USD per billed second). Scribe STT is
# priced per audio hour (~$0.40/hr => ~$0.0067/min); generated music/SFX
# bill per rendered second. Treat these as order-of-magnitude estimates -
# real pricing depends on the plan.
ELEVENLABS_USD_PER_SECOND = {
    "stt": 0.40 / 3600.0,
    "music": 0.10 / 60.0,
    "sfx": 0.10 / 60.0,
}

# Roughly 4 characters per token for English text prompts.
_CHARS_PER_TOKEN = 4

_active: contextvars.ContextVar[CostLedger | None] = contextvars.ContextVar(
    "directorcut_cost_ledger", default=None
)


def estimate_text_tokens(text: str) -> int:
    """Tiktoken-free estimate: ~4 chars/token, same heuristic class as the
    dry-run estimator (safety.cost)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def active_ledger() -> CostLedger | None:
    """The ledger of the in-flight run on this thread, if any."""
    return _active.get()


class CostLedger:
    """Accumulates metered-call spend and persists it to costs.json.

    Thread-safe (the API runs one pipeline per thread and clients are
    recreated per stage). `load()` merges an existing costs.json so a
    resumed run - or a `cut patch` Gemini call after the fact - accrues
    onto the project's prior spend instead of hiding it.
    """

    def __init__(self, output_dir: Path, cap_usd: float | None = None) -> None:
        self.path = output_dir / COSTS_FILE
        self.cap_usd = cap_usd if cap_usd and cap_usd > 0 else None
        self._lock = threading.Lock()
        self._calls: list[dict[str, Any]] = []

    # -- persistence ----------------------------------------------------

    @classmethod
    def load(cls, output_dir: Path, cap_usd: float | None = None) -> CostLedger:
        """Open the project's ledger, replaying prior calls if present."""
        ledger = cls(output_dir, cap_usd=cap_usd)
        if ledger.path.exists():
            try:
                data = json.loads(ledger.path.read_text(encoding="utf-8"))
                calls = data.get("calls")
                if isinstance(calls, list):
                    ledger._calls = [c for c in calls if isinstance(c, dict)]
            except (OSError, json.JSONDecodeError) as exc:
                # A torn file (crash mid-write) starts a fresh ledger - the
                # alternative is silently losing the whole spend history.
                logger.warning("Ignoring unreadable %s: %s", ledger.path, exc)
        return ledger

    def flush(self) -> None:
        """Write-then-rename the ledger; never raises (accounting must not
        break the run it's metering)."""
        payload = {
            "version": 1,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "cap_usd": self.cap_usd,
            "totals": self.totals(),
            "calls": self._calls,
        }
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("Could not write %s: %s", self.path, exc)

    # -- recording --------------------------------------------------------

    def record_gemini(
        self,
        *,
        task: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        stub: bool = False,
    ) -> None:
        """Record one Gemini call. `stub` marks a dev-mode short-circuit -
        tokens still count toward the estimate, cost stays 0."""
        cost = (
            input_tokens * PRICE_INPUT_PER_1M / 1_000_000
            + output_tokens * PRICE_OUTPUT_PER_1M / 1_000_000
        )
        self._append(
            kind="gemini",
            task=task,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0 if stub else cost,
            stub=stub,
        )

    def record_elevenlabs(
        self,
        *,
        kind: str,
        seconds: float,
        stub: bool = False,
    ) -> None:
        """Record one ElevenLabs call. `kind` is stt|music|sfx; `seconds` is
        billed duration (audio length for STT, rendered length otherwise)."""
        rate = ELEVENLABS_USD_PER_SECOND.get(kind, 0.0)
        self._append(
            kind=f"elevenlabs_{kind}",
            seconds=round(seconds, 3),
            cost_usd=0.0 if stub else round(seconds * rate, 6),
            stub=stub,
        )

    def _append(self, **entry: Any) -> None:
        entry["ts"] = datetime.now(UTC).isoformat(timespec="seconds")
        # flush()/totals() take the lock themselves - append under the lock,
        # then release it before persisting (a plain Lock isn't reentrant).
        with self._lock:
            self._calls.append(entry)
        self.flush()
        # The call that pushed the ledger over the cap DID spend - record
        # it first, then abort loudly.
        if not entry.get("stub"):
            self.check_cap()

    # -- reporting / cap ---------------------------------------------------

    def totals(self) -> dict[str, Any]:
        """Aggregate view: per-task Gemini tokens, per-kind ElevenLabs
        seconds, and overall USD."""
        totals: dict[str, Any] = {
            "cost_usd": 0.0,
            "gemini_input_tokens": 0,
            "gemini_output_tokens": 0,
            "gemini_calls": 0,
            "elevenlabs_seconds": {},
            "by_task": {},
        }
        with self._lock:
            calls = list(self._calls)
        for c in calls:
            totals["cost_usd"] += c.get("cost_usd", 0.0)
            if c.get("kind") == "gemini":
                totals["gemini_calls"] += 1
                totals["gemini_input_tokens"] += c.get("input_tokens", 0)
                totals["gemini_output_tokens"] += c.get("output_tokens", 0)
                task = c.get("task") or "unknown"
                bucket = totals["by_task"].setdefault(
                    task,
                    {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                )
                bucket["calls"] += 1
                bucket["input_tokens"] += c.get("input_tokens", 0)
                bucket["output_tokens"] += c.get("output_tokens", 0)
                bucket["cost_usd"] += c.get("cost_usd", 0.0)
            elif str(c.get("kind", "")).startswith("elevenlabs_"):
                kind = str(c["kind"]).removeprefix("elevenlabs_")
                totals["elevenlabs_seconds"][kind] = (
                    totals["elevenlabs_seconds"].get(kind, 0.0)
                    + c.get("seconds", 0.0)
                )
        totals["cost_usd"] = round(totals["cost_usd"], 6)
        return totals

    @property
    def total_usd(self) -> float:
        return self.totals()["cost_usd"]

    def check_cap(self) -> None:
        """Raise BudgetExceeded when recorded spend is past the cap."""
        if self.cap_usd is None:
            return
        total = self.total_usd
        if total <= self.cap_usd:
            return
        raise BudgetExceeded(
            f"Cost cap exceeded: ${total:.4f} spent against a "
            f"${self.cap_usd:.4f} cap (DIRECTORCUT_COST_CAP_USD). "
            f"Run aborted before the next paid call - stage checkpoints "
            f"and costs.json are intact, so raising the cap and re-running "
            f"resumes where it stopped."
        )


@contextmanager
def activate_cost_ledger(output_dir: Path, cap_usd: float | None = None):
    """Bind a CostLedger to this context; both API clients consult
    `active_ledger()` so any metered call inside the block is recorded.

    Flushes on exit (even on failure) so costs.json always reflects every
    completed call. Nested activation is allowed - the innermost ledger
    wins and the outer one is restored afterwards.
    """
    ledger = CostLedger.load(output_dir, cap_usd=cap_usd)
    token = _active.set(ledger)
    try:
        yield ledger
    finally:
        ledger.flush()
        _active.reset(token)
