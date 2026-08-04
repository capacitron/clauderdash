"""Write endpoints for the fix queue: accept/snooze/fixed state, and the
short-leash auto-run fix. Everything here sits behind require_fix_token —
see fixauth.py for why that gate exists.

APPLY HANDLERS ARE HARDCODED PYTHON, NOT `subprocess.run(fix_text)`.

This is a stronger promise than the findings.auto_run CHECK constraint makes
on its own. The constraint only proves the fix_text came from detector source
rather than an LLM; it says nothing about whether running that text verbatim
is safe. bash_bloat's fix_text is human-readable instructional prose ("Add to
~/.zshrc or ~/.bashrc: export ..."), not a runnable command — piping it to a
shell would either no-op or do something unintended. Each auto-run pattern
gets its own small function that does the specific, auditable thing, and
_HANDLERS is the only place new patterns can enter the auto-run set.

Auto-run additionally requires a REVERSIBLE handler, enforced at import by
_check_autorun_invariant(). unused_mcp originally shipped as auto_run=1 with a
handler that returned undo_command=None; that combination is what the check
now refuses. Widening the leash means writing a handler that can undo itself,
not flipping a flag.
"""
from __future__ import annotations

import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connect
from ..ingest.findings import DETECTOR_MAP
from ..ingest.optimize import _BASH_RECOMMENDED
from .fixauth import require_fix_token

router = APIRouter(prefix="/api/fix", dependencies=[Depends(require_fix_token)])

_HOME = Path.home()


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- state --

class StateBody(BaseModel):
    status: str  # open | snoozed | accepted | fixed
    note: str | None = None
    snooze_until: str | None = None  # required when status == "snoozed"


@router.post("/state/{key:path}")
def set_finding_state(key: str, body: StateBody) -> dict[str, Any]:
    """Record the operator's decision on a finding.

    'accepted' is the one that matters most: it means "this is deliberate,
    stop counting it against the grade" without pretending the finding isn't
    real — 835 unused skills stays true, it just stops being penalized once
    the operator has looked at it and decided not to act.
    """
    if body.status not in ("open", "snoozed", "accepted", "fixed"):
        raise HTTPException(422, f"bad status: {body.status}")
    if body.status == "snoozed" and not body.snooze_until:
        raise HTTPException(422, "snoozed requires snooze_until")

    conn = connect()
    try:
        exists = conn.execute("SELECT 1 FROM findings WHERE key = ?", (key,)).fetchone()
        if not exists:
            raise HTTPException(404, f"no such finding: {key}")
        conn.execute(
            """INSERT INTO finding_state(key, status, note, snooze_until, changed_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 status = excluded.status, note = excluded.note,
                 snooze_until = excluded.snooze_until, changed_at = excluded.changed_at""",
            (key, body.status, body.note, body.snooze_until, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"key": key, "status": body.status}


# ---------------------------------------------------------------- apply --

class FixResult(NamedTuple):
    ok: bool
    output: str
    undo_command: str | None  # None = nothing to undo (no-op, or failed)


def _delete_line_cmd(line: str, path: Path) -> str:
    """Shell command removing exactly `line` from `path`, no regex involved.

    The first version built a sed address out of Python's re.escape(), which
    means a Python-regex escape string interpreted as a sed BRE — two dialects
    that agreed on this particular input by luck, not by design. grep -x
    (whole line) -F (fixed string, no metacharacters at all) removes the
    dialect question entirely.
    """
    q_line, q_path = shlex.quote(line), shlex.quote(str(path))
    return f"grep -vxF {q_line} {q_path} > {q_path}.undo.tmp && mv {q_path}.undo.tmp {q_path}"


def _apply_bash_bloat(finding: dict) -> FixResult:
    line = f"export BASH_MAX_OUTPUT_LENGTH={_BASH_RECOMMENDED}"
    profile = _HOME / ".bashrc"
    content = profile.read_text() if profile.exists() else ""
    if line in content.splitlines():
        # Already set: nothing was written, so there is nothing to reverse.
        # Distinct from "this fix is irreversible", which is Handler.reversible.
        return FixResult(True, f"{line} already present in {profile}", None)
    # Exactly one line added, so the undo's line-delete restores the file
    # byte for byte. Writing "\n{line}\n" (the first version) left a blank
    # line behind after undo, and repeated apply/undo cycles accumulated one
    # each time — an undo that does not fully undo is the whole point of the
    # log. The separator newline is only added if the file lacks a trailing
    # one, which would otherwise splice this onto the last line.
    if content and not content.endswith("\n"):
        with profile.open("a") as f:
            f.write("\n")
    with profile.open("a") as f:
        f.write(f"{line}\n")
    return FixResult(True, f"appended to {profile}", _delete_line_cmd(line, profile))


class Handler(NamedTuple):
    fn: Callable[[dict], FixResult]
    # True only if the handler can hand back a command that fully reverses a
    # change it actually made. Cross-checked against findings.DETECTOR_MAP at
    # import: a pattern cannot be auto_run without a reversible handler.
    reversible: bool


# pattern -> handler. Only patterns here may auto-run, independent of what the
# findings.auto_run column says — belt and braces.
#
# unused_mcp is deliberately absent. It was registered here with auto_run=1
# while its handler returned undo_command=None, which is the contradiction the
# import-time check below now makes unshippable. Removing an MCP server stays a
# copy-the-command fix.
_HANDLERS: dict[str, Handler] = {
    "bash_bloat": Handler(_apply_bash_bloat, reversible=True),
}


def _check_autorun_invariant() -> None:
    """Every auto_run pattern must have a registered, reversible handler.

    Runs at import so a bad registration takes the service down at startup
    instead of surfacing when someone presses the button on a fix that cannot
    be undone. The findings table's CHECK constraint only proves a fix came
    from detector source — it says nothing about reversibility, which is the
    property the short-leash + undo-log design actually depends on.
    """
    for pattern, (_, _, auto_run) in DETECTOR_MAP.items():
        if not auto_run:
            continue
        handler = _HANDLERS.get(pattern)
        if handler is None:
            raise RuntimeError(
                f"{pattern} is auto_run in DETECTOR_MAP but has no handler in fixactions._HANDLERS"
            )
        if not handler.reversible:
            raise RuntimeError(
                f"{pattern} is auto_run but its handler is not reversible; "
                "auto-run fixes must be undoable (short leash + undo log)"
            )


_check_autorun_invariant()


@router.post("/run/{key:path}")
def run_fix(key: str) -> dict[str, Any]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT key, source, gauge, fix_kind, fix_text, auto_run FROM findings WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"no such finding: {key}")
        finding = dict(row)
        if not finding["auto_run"]:
            raise HTTPException(403, f"{key} is not on the auto-run allowlist")
        pattern = key.removeprefix("detector:")
        handler = _HANDLERS.get(pattern)
        if handler is None:
            # A finding claims auto_run=1 but has no matching handler here —
            # DETECTOR_MAP and this dispatch table have drifted apart. Refuse
            # rather than guess.
            raise HTTPException(500, f"no apply handler registered for {pattern}")

        try:
            result = handler.fn(finding)
        except Exception as e:
            conn.execute(
                """INSERT INTO fix_actions(finding_key, fix_kind, command, undo_command,
                                           status, output, exit_code, ran_at)
                   VALUES(?, ?, ?, NULL, 'failed', ?, NULL, ?)""",
                (key, finding["fix_kind"], pattern, str(e), _now()),
            )
            conn.commit()
            raise HTTPException(500, f"fix failed: {e}") from e

        conn.execute(
            """INSERT INTO fix_actions(finding_key, fix_kind, command, undo_command,
                                       status, output, exit_code, ran_at)
               VALUES(?, ?, ?, ?, ?, ?, 0, ?)""",
            (key, finding["fix_kind"], pattern, result.undo_command,
             "ok" if result.ok else "failed", result.output, _now()),
        )
        action_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return {
            "ok": result.ok,
            "output": result.output,
            "undoable": result.undo_command is not None,
            "action_id": action_id,
        }
    finally:
        conn.close()


@router.post("/undo/{action_id}")
def undo_fix(action_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, undo_command, status FROM fix_actions WHERE id = ?", (action_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"no such action: {action_id}")
        action = dict(row)
        if action["status"] != "ok":
            raise HTTPException(409, f"action is {action['status']}, nothing to undo")
        if not action["undo_command"]:
            raise HTTPException(409, "this action has no recorded undo command")

        proc = subprocess.run(
            action["undo_command"], shell=True, capture_output=True, text=True, timeout=10
        )
        conn.execute(
            "UPDATE fix_actions SET status='undone', undone_at=? WHERE id=?",
            (_now(), action_id),
        )
        conn.commit()
        return {"ok": proc.returncode == 0, "output": (proc.stdout + proc.stderr)[:2000]}
    finally:
        conn.close()


if __name__ == "__main__":  # python -m app.api.fixactions
    # Apply handlers run against a temporary HOME, never the real one. The two
    # bugs found on 2026-08-04 — a handler hardcoding ok=True regardless of
    # exit status, and CLAUDE_BIN not resolving under systemd — were both
    # caught by firing a real fix at real config, which also mutated the
    # operator's MCP setup as a side effect. A synthetic finding would have
    # caught both for free.
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    _real_home, _HOME = _HOME, tmp  # noqa: F811 — handlers read the global
    globals()["_HOME"] = tmp
    try:
        profile = tmp / ".bashrc"
        line = f"export BASH_MAX_OUTPUT_LENGTH={_BASH_RECOMMENDED}"
        synthetic = {"key": "detector:bash_bloat", "fix_kind": "shell", "fix_text": None}

        # Both trailing-newline shapes: a file without one must not get this
        # spliced onto its last line, and neither may drift after a round trip.
        for original in ("alias ll='ls -la'\nexport EDITOR=vim\n", "export EDITOR=vim"):
            profile.write_text(original)

            applied = _apply_bash_bloat(synthetic)
            assert applied.ok and applied.undo_command, applied
            assert line in profile.read_text().splitlines(), "fix did not apply"

            # Re-applying is a no-op and must not offer an undo for work it skipped.
            again = _apply_bash_bloat(synthetic)
            assert again.ok and again.undo_command is None, again
            assert profile.read_text().count(line) == 1, "fix applied twice"

            proc = subprocess.run(applied.undo_command, shell=True, capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr
            after = profile.read_text()
            assert "alias ll" in after or "EDITOR" in after, "undo ate unrelated lines"
            # Byte-exact, not merely "the line is gone". The first version
            # appended "\n{line}\n" and left a blank line behind on undo, so
            # repeated cycles grew the file one line at a time.
            expected = original if original.endswith("\n") else original + "\n"
            assert after == expected, f"undo not byte-exact:\n{after!r}\n{expected!r}"

        # The invariant itself: an auto_run pattern with an irreversible
        # handler must refuse to load. This is the check that would have
        # blocked unused_mcp from ever reaching the auto-run path.
        _saved = _HANDLERS.copy()
        try:
            _HANDLERS["bash_bloat"] = Handler(_apply_bash_bloat, reversible=False)
            try:
                _check_autorun_invariant()
                raise AssertionError("irreversible auto_run handler must be rejected")
            except RuntimeError:
                pass
            del _HANDLERS["bash_bloat"]
            try:
                _check_autorun_invariant()
                raise AssertionError("missing auto_run handler must be rejected")
            except RuntimeError:
                pass
        finally:
            _HANDLERS.clear()
            _HANDLERS.update(_saved)
        _check_autorun_invariant()

        auto = [p for p, (_, _, a) in DETECTOR_MAP.items() if a]
        assert auto == ["bash_bloat"], f"auto-run set drifted: {auto}"
        print("fixactions self-check: 10/10 (temp HOME, real HOME untouched)")
    finally:
        globals()["_HOME"] = _real_home
