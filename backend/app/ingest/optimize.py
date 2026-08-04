"""Waste pattern detection. Port of codeburn's optimize.ts detectors.

Runs as background task every 30 minutes. Scans recent sessions (7d) for
patterns that waste tokens/cost. Results written to the unified `findings`
table (see ingest/findings.py) — this module owns detection, not storage.

Detectors:
  junk_reads         — reads from node_modules, .git, dist, __pycache__, etc.
  duplicate_reads    — same file read multiple times per session
  unused_mcp         — MCP servers configured but never called
  bloated_claude_md  — CLAUDE.md + @-imports > 200 lines
  low_read_edit_ratio— too many edits relative to reads
  bash_bloat         — BASH_MAX_OUTPUT_LENGTH above recommended
  ghost_agents       — ~/.claude/agents/ entries never invoked
  ghost_skills       — ~/.claude/skills/ entries never invoked
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import findings as findings_db

log = logging.getLogger(__name__)

_RUN_INTERVAL_S = 30 * 60       # 30 minutes
_SCAN_DAYS      = 7

# Number of checks a clean setup passes. The hygiene score is "share of checks
# passing", so it needs the denominator, not just the list of failures.
DETECTOR_COUNT = 8
SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}
_HOME = Path.home()

_JUNK_DIRS = frozenset([
    "node_modules", ".git", "dist", "build", "__pycache__", ".next",
    ".nuxt", ".output", "coverage", ".cache", ".venv", "venv",
])
_JUNK_PATTERN = re.compile(
    r"/(?:node_modules|\.git|dist|build|__pycache__|\.next|\.nuxt|\.output|coverage|\.cache|\.venv|venv)/"
)

_READ_TOOLS = frozenset(["Read", "FileReadTool"])
_EDIT_TOOLS = frozenset(["Edit", "Write", "FileEditTool", "FileWriteTool", "NotebookEdit"])
_ALL_READ   = frozenset(["Read", "Grep", "Glob", "FileReadTool", "GrepTool", "GlobTool"])
_ALL_EDIT   = frozenset(["Edit", "Write", "FileEditTool", "FileWriteTool", "NotebookEdit"])
# Edits to a file that already exists — the only ones a prior read applies to.
_MODIFY_TOOLS = frozenset(["Edit", "FileEditTool", "NotebookEdit"])

_MIN_JUNK_READS  = 3
_MIN_DUP_READS   = 5
_MIN_EDITS       = 10
_HEALTHY_RATIO   = 4.0
_CLAUDEMD_LIMIT  = 200
_CLAUDEMD_TOKENS_PER_LINE = 13
_IMPORT_RE       = re.compile(r"^@(\.\.?/[^\s]+|/[^\s]+)", re.M)
_BASH_RECOMMENDED = 15_000
# "- name\n" wrapping around each entry in the prompt's skill/agent listing.
_LIST_ENTRY_OVERHEAD_CHARS = 3
_CHARS_PER_TOKEN = 4
# Only for a re-read whose file has since been deleted, so its size is unknowable.
_FALLBACK_READ_TOKENS = 600
# Fraction of Agent calls that must yield a readable subagent_type before the
# ghost-agent list is trustworthy enough to report.
_MIN_AGENT_PARSE_RATIO = 0.5


# ─── helpers ──────────────────────────────────────────────────────────────────

def _since_iso(days: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
    return dt.isoformat()


def _read_file(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except Exception:
        return None


def _expand_claude_md(path: Path, seen: set[str], depth: int = 0) -> int:
    if depth > 5 or str(path) in seen:
        return 0
    seen.add(str(path))
    content = _read_file(path)
    if not content:
        return 0
    total = len(content.splitlines())
    base_dir = path.parent
    for m in _IMPORT_RE.finditer(content):
        raw = m.group(1)
        resolved = Path(raw) if raw.startswith("/") else (base_dir / raw).resolve()
        if resolved.exists():
            total += _expand_claude_md(resolved, seen, depth + 1)
    return total


_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.M)


def _agent_listing_tokens(name: str) -> int:
    """Prompt cost of one agent's entry in the Task tool's agent listing.

    Measured from the agent's own frontmatter description rather than the old
    flat 80, which happened to be near-right for agents but was pure guess.
    """
    text = _read_file(_HOME / ".claude" / "agents" / f"{name}.md") or ""
    m = _DESCRIPTION_RE.search(text)
    desc_len = len(m.group(1)) if m else 0
    return (len(name) + desc_len + _LIST_ENTRY_OVERHEAD_CHARS) // _CHARS_PER_TOKEN


def _file_tokens(path: str, _cache: dict[str, int] = {}) -> int:
    """Approximate token cost of reading `path`, from its size on disk.

    Falls back to _FALLBACK_READ_TOKENS when the file has since been deleted or
    moved, which is the only case where a guess is unavoidable. Cached because
    one scan prices the same handful of hot files repeatedly.
    """
    if path in _cache:
        return _cache[path]
    try:
        tokens = Path(path).stat().st_size // _CHARS_PER_TOKEN
    except OSError:
        tokens = _FALLBACK_READ_TOKENS
    _cache[path] = tokens
    return tokens


def _load_mcp_servers(project_cwds: list[str]) -> set[str]:
    servers: set[str] = set()
    configs = [
        _HOME / ".claude" / "settings.json",
        _HOME / ".claude" / "settings.local.json",
    ]
    for cwd in project_cwds:
        configs += [
            Path(cwd) / ".mcp.json",
            Path(cwd) / ".claude" / "settings.json",
            Path(cwd) / ".claude" / "settings.local.json",
        ]
    for p in configs:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            for name in (data.get("mcpServers") or {}).keys():
                servers.add(name.replace(":", "_"))
        except Exception:
            continue
    return servers


# ─── detectors ────────────────────────────────────────────────────────────────

def detect_junk_reads(tool_calls: list[dict]) -> dict | None:
    counts: dict[str, int] = {}
    total = 0
    for tc in tool_calls:
        if tc["tool_name"] not in _READ_TOOLS:
            continue
        fp = tc.get("input_fp", "")
        if not fp or not _JUNK_PATTERN.search(fp):
            continue
        total += 1
        for d in _JUNK_DIRS:
            if f"/{d}/" in fp:
                counts[d] = counts.get(d, 0) + 1
                break
    if total < _MIN_JUNK_READS:
        return None
    top = sorted(counts.items(), key=lambda x: -x[1])[:3]
    dirs = ", ".join(f"{d}/ ({n}x)" for d, n in top)
    tokens_saved = total * 600
    return {
        "pattern":     "junk_reads",
        "severity":    "high" if total > 20 else ("medium" if total > 5 else "low"),
        "detail":      f"Claude read into {dirs} ({total} reads). Add to CLAUDE.md: Do not read generated/dependency directories.",
        "tokens_saved": tokens_saved,
        "fix_label":   "Append to CLAUDE.md:",
        "fix_text":    f"Do not read or search files under these directories unless I explicitly ask: {', '.join(d for d, _ in top)}.",
    }


def detect_duplicate_reads(tool_calls: list[dict]) -> dict | None:
    session_files: dict[str, dict[str, int]] = {}
    for tc in tool_calls:
        if tc["tool_name"] not in _READ_TOOLS:
            continue
        fp = tc.get("input_fp", "")
        if not fp or _JUNK_PATTERN.search(fp):
            continue
        key = tc["session_id"]
        if key not in session_files:
            session_files[key] = {}
        session_files[key][fp] = session_files[key].get(fp, 0) + 1

    total_dupes = 0
    tokens_wasted = 0
    file_counts: dict[str, int] = {}
    for fm in session_files.values():
        for fp, cnt in fm.items():
            if cnt > 1:
                extra = cnt - 1
                total_dupes += extra
                name = Path(fp).name
                file_counts[name] = file_counts.get(name, 0) + extra
                # Price each re-read at the file's actual size. The old flat
                # 600 tokens/read was uniform across a 40-line config and a
                # 4000-line deck.html, which put the estimate off by more than
                # an order of magnitude in both directions.
                tokens_wasted += extra * _file_tokens(fp)

    if total_dupes < _MIN_DUP_READS:
        return None
    worst = sorted(file_counts.items(), key=lambda x: -x[1])[:3]
    worst_str = ", ".join(f"{n} ({c+1}x)" for n, c in worst)
    return {
        "pattern":     "duplicate_reads",
        "severity":    "high" if total_dupes > 30 else ("medium" if total_dupes > 10 else "low"),
        "detail":      f"{total_dupes} redundant re-reads. Top: {worst_str}. Point Claude at exact line numbers in your prompt.",
        "tokens_saved": tokens_wasted,
        "fix_label":   "In your prompt:",
        "fix_text":    "In <file> lines <start>-<end>, look at the <function> function.",
    }


def detect_low_read_edit_ratio(tool_calls: list[dict]) -> dict | None:
    """Behavioural check: edits landing without a read to justify them.

    tokens_saved is deliberately 0. The old value was
    `(edits * 4 - reads) * 600` — the number of reads NOT performed, priced at
    600 tokens each. That is the cost of taking the advice, reported as the
    benefit of taking it, and at 1.7M it sat at the top of the queue at ~9x the
    next item with its sign reversed. The genuine saving here is retries
    avoided, and nothing in this schema records a retry, so there is no honest
    figure to put in its place.

    Modify-only tools count; Write does not. Creating a new file requires no
    prior read, and 161 of 1054 "edits" in the 7d window were Writes, which
    dragged the ratio down for correct behaviour.
    """
    reads = sum(1 for tc in tool_calls if tc["tool_name"] in _ALL_READ)
    edits = sum(1 for tc in tool_calls if tc["tool_name"] in _MODIFY_TOOLS)
    if edits < _MIN_EDITS:
        return None
    ratio = reads / edits
    if ratio >= _HEALTHY_RATIO:
        return None
    severity = "high" if ratio < 2 else ("medium" if ratio < 3 else "low")
    return {
        "pattern":     "low_read_edit_ratio",
        "severity":    severity,
        "detail":      f"Claude made {reads} reads and {edits} modifications (ratio {ratio:.1f}:1). Healthy >= {_HEALTHY_RATIO:.0f}:1. Editing without reading first causes retries.",
        "tokens_saved": None,
        "fix_label":   "Add to CLAUDE.md:",
        "fix_text":    "Before editing any file, read it first. Before modifying a function, grep for all callers.",
    }


def detect_unused_mcp(tool_calls: list[dict], project_cwds: list[str]) -> dict | None:
    configured = _load_mcp_servers(project_cwds)
    if not configured:
        return None
    called: set[str] = set()
    for tc in tool_calls:
        if tc["tool_name"].startswith("mcp__"):
            parts = tc["tool_name"].split("__")
            if len(parts) >= 2:
                called.add(parts[1])
    unused = [s for s in configured if s not in called]
    if not unused:
        return None
    return {
        "pattern":     "unused_mcp",
        "severity":    "high" if len(unused) >= 3 else "medium",
        # "not called in the scan window", NOT "never called" — the old wording
        # said never while the query only ever looked at _SCAN_DAYS, and
        # jcodemunch has four tools with calls on record outside it.
        #
        # tokens_saved is 0: the old 5 tools x 400 assumed a fixed server size,
        # but a server's prompt cost depends on its tool count and whether the
        # harness defers its schemas. jcodemunch alone exposes ~100 deferred
        # tools, so the estimate was off by ~50x and in the optimistic
        # direction. The real figure needs a tool-count probe this scan
        # does not have.
        "detail":      f"{len(unused)} MCP server(s) configured but not called in {_SCAN_DAYS}d: {', '.join(unused[:5])}. Each server's tool list loads into every session prompt.",
        "tokens_saved": None,
        "fix_label":   "Remove unused servers:",
        "fix_text":    "\n".join(f"claude mcp remove {s}" for s in unused[:10]),
    }


def detect_bloated_claude_md(project_cwds: list[str]) -> dict | None:
    """Oversized instruction files, counting @-imports.

    The global files are checked first and matter most: ~/.claude/CLAUDE.md and
    ~/CLAUDE.md load into EVERY session regardless of directory, while a
    project file only costs you in that project. The old version scanned
    project cwds exclusively, so a 261-line ~/CLAUDE.md — over the limit, and
    paid for on every single session — was invisible to it.
    """
    candidates = [_HOME / ".claude" / "CLAUDE.md", _HOME / "CLAUDE.md"]
    for cwd in set(project_cwds):
        candidates += [Path(cwd) / "CLAUDE.md", Path(cwd) / ".claude" / "CLAUDE.md"]

    bloated = []
    for full in candidates:
        if not full.exists():
            continue
        lines = _expand_claude_md(full, set())
        if lines > _CLAUDEMD_LIMIT:
            bloated.append((str(full), lines, full.parent in (_HOME, _HOME / ".claude")))

    if not bloated:
        return None
    # Global files sort first at equal size: they are charged on every session.
    bloated.sort(key=lambda x: (not x[2], -x[1]))
    worst_path, worst_lines, worst_global = bloated[0]
    total_extra = sum(max(0, l - _CLAUDEMD_LIMIT) for _, l, _ in bloated)
    scope = "every session" if worst_global else "every session in that project"
    return {
        "pattern":     "bloated_claude_md",
        "severity":    "high" if worst_lines > 400 else "medium",
        # Per session, not per call: the file is part of the cached prompt
        # prefix, so it is paid once at cache-write and read cheaply after.
        "detail":      f"{worst_path} has {worst_lines} lines expanded (limit {_CLAUDEMD_LIMIT}), loaded on {scope}. {len(bloated)} file(s) over, ~{total_extra * _CLAUDEMD_TOKENS_PER_LINE} extra tokens/session.",
        "tokens_saved": total_extra * _CLAUDEMD_TOKENS_PER_LINE,
        "fix_label":   "Ask Claude to trim it:",
        "fix_text":    f"Review {worst_path} and all @-imported files. Cut to under {_CLAUDEMD_LIMIT} lines. Remove anything derivable from code.",
    }


def detect_bash_bloat() -> dict | None:
    limit = None
    for profile in [".zshrc", ".bashrc", ".bash_profile", ".profile"]:
        content = _read_file(_HOME / profile)
        if not content:
            continue
        m = re.search(r"export\s+BASH_MAX_OUTPUT_LENGTH\s*=\s*['\"]?(\d+)", content)
        if m:
            limit = int(m.group(1))
            break
    env_val = os.environ.get("BASH_MAX_OUTPUT_LENGTH")
    if limit is None and env_val:
        limit = int(env_val)
    if limit is not None and limit <= _BASH_RECOMMENDED:
        return None
    actual = limit or 30_000
    extra_chars = actual - _BASH_RECOMMENDED
    return {
        "pattern":     "bash_bloat",
        "severity":    "medium",
        # tokens_saved deliberately 0. The old value was (cap - recommended)/4
        # per call, which assumes every Bash call fills the buffer — most return
        # a few lines. tool_calls stores no result size, so the real figure is
        # not derivable from this data at all, and a fabricated number here
        # would outrank every genuinely-measured finding once the queue sorts
        # by cost. Config check, no price tag.
        "detail":      f"Bash output cap is {actual//1000}K chars ({'configured' if limit else 'default'}). Recommend {_BASH_RECOMMENDED//1000}K — caps the worst case at {extra_chars//4} fewer tokens on a call that overflows.",
        "tokens_saved": None,
        "fix_label":   "Add to ~/.zshrc or ~/.bashrc:",
        "fix_text":    f"export BASH_MAX_OUTPUT_LENGTH={_BASH_RECOMMENDED}",
    }


def detect_ghost_agents(tool_calls: list[dict]) -> dict | None:
    agents_dir = _HOME / ".claude" / "agents"
    if not agents_dir.exists():
        return None
    defined = [p.stem for p in agents_dir.glob("*.md")]
    if not defined:
        return None
    invoked: set[str] = set()
    agent_calls = 0
    for tc in tool_calls:
        if tc["tool_name"] in ("Agent", "Task"):
            agent_calls += 1
            try:
                inp = json.loads(tc.get("input_preview", "{}"))
                st = inp.get("subagent_type")
                if st:
                    invoked.add(st)
            except Exception:
                pass
    # There is no agent_invocations table to consult (unlike skills), and
    # input_preview is truncated, so most Agent calls never surface a readable
    # subagent_type — 65 of 200 in the audited window. Below half, "never
    # invoked" is indistinguishable from "invocation not recorded", and naming
    # a used agent as a ghost is worse than staying quiet.
    if not agent_calls or len(invoked) * 2 < _MIN_AGENT_PARSE_RATIO * agent_calls:
        return None
    ghosts = [n for n in defined if n not in invoked]
    if not ghosts:
        return None
    return {
        "pattern":     "ghost_agents",
        "severity":    "high" if len(ghosts) >= 5 else ("medium" if len(ghosts) >= 2 else "low"),
        "detail":      f"{len(ghosts)} of {len(defined)} agent(s) in ~/.claude/agents/ never invoked: {', '.join(ghosts[:5])}. Each adds its description to the Task schema every session.",
        "tokens_saved": sum(_agent_listing_tokens(n) for n in ghosts),
        "fix_label":   "Archive unused agents:",
        "fix_text":    "\n".join(f"mv ~/.claude/agents/{n}.md ~/.claude/agents/.archived/" for n in ghosts[:10]),
    }


def detect_ghost_skills(invoked: set[str]) -> dict | None:
    """Skills present in every session's prompt that have never been used.

    `invoked` comes from the skill_invocations table, not from re-parsing
    tool_calls.input_preview. The old version did the latter and found 9 used
    skills where the dedicated table records 52 — a Skill call does not always
    leave a parseable `skill` key in the preview, so it over-reported ghosts by
    ~43 and inflated the finding's severity along with it.
    """
    skills_dir = _HOME / ".claude" / "skills"
    if not skills_dir.exists():
        return None
    defined = [p.name for p in skills_dir.iterdir() if (p / "SKILL.md").exists()]
    if not defined:
        return None
    ghosts = [n for n in defined if n not in invoked]
    if not ghosts:
        return None
    # User skills render in the prompt as a bare "- name" line, so the cost is
    # measurable from the names themselves. The old flat 80 tokens/skill was
    # ~10x over: it priced every entry as though it carried a full description.
    tokens = sum(len(n) + _LIST_ENTRY_OVERHEAD_CHARS for n in ghosts) // _CHARS_PER_TOKEN
    return {
        "pattern":     "ghost_skills",
        "severity":    "high" if len(ghosts) >= 10 else ("medium" if len(ghosts) >= 5 else "low"),
        "detail":      f"{len(ghosts)} of {len(defined)} skill(s) never invoked: {', '.join(ghosts[:5])}. ~{tokens} tokens of listing in every session prompt.",
        "tokens_saved": tokens,
        "fix_label":   "Archive unused skills:",
        "fix_text":    "\n".join(f"mv ~/.claude/skills/{n} ~/.claude/skills/.archived/" for n in ghosts[:10]),
    }


# ─── main scanner ─────────────────────────────────────────────────────────────

def _scan_tool_calls(conn: sqlite3.Connection, since: str) -> tuple[list[dict], list[str]]:
    rows = conn.execute(
        """
        SELECT tc.tool_name, tc.session_id, tc.input_preview,
               s.cwd
          FROM tool_calls tc
          JOIN sessions s ON s.id = tc.session_id
         WHERE tc.started_at >= ?
        """,
        (since,),
    ).fetchall()
    tool_calls = []
    cwds: list[str] = []
    for r in rows:
        inp = r["input_preview"] or "{}"
        fp = None
        try:
            parsed = json.loads(inp)
            fp = parsed.get("file_path") or parsed.get("path")
        except Exception:
            pass
        tool_calls.append({
            "tool_name":  r["tool_name"],
            "session_id": r["session_id"],
            "input_fp":   fp or "",
            "input_preview": inp,
        })
        if r["cwd"]:
            cwds.append(r["cwd"])
    return tool_calls, cwds


def _invoked_skills(conn: sqlite3.Connection) -> set[str]:
    """Every skill ever invoked, from the table that records exactly that.

    Not windowed to _SCAN_DAYS: a skill used once six months ago is one you
    kept on purpose, and telling you to archive it is noise.
    """
    return {r[0] for r in conn.execute("SELECT DISTINCT skill_name FROM skill_invocations")}


def run_optimize_scan(conn: sqlite3.Connection) -> int:
    """Run all detectors and upsert results into the unified `findings` table.

    A detector that stops firing must read as the problem being gone, not as
    the row vanishing — findings.upsert()/resolve_missing() carry that state
    across scans instead of the old wholesale-rewrite-every-30-minutes
    approach, which is what made the table balloon to 1169 duplicate rows for
    7 real findings before this cutover.
    """
    since = _since_iso(_SCAN_DAYS)
    tool_calls, cwds = _scan_tool_calls(conn, since)
    project_cwds = list(set(cwds))
    invoked_skills = _invoked_skills(conn)

    detectors = [
        lambda: detect_junk_reads(tool_calls),
        lambda: detect_duplicate_reads(tool_calls),
        lambda: detect_low_read_edit_ratio(tool_calls),
        lambda: detect_unused_mcp(tool_calls, project_cwds),
        lambda: detect_bloated_claude_md(project_cwds),
        lambda: detect_bash_bloat(),
        lambda: detect_ghost_agents(tool_calls),
        lambda: detect_ghost_skills(invoked_skills),
    ]
    assert len(detectors) == DETECTOR_COUNT, "DETECTOR_COUNT out of sync"

    rows = []
    for detect in detectors:
        try:
            finding = detect()
        except Exception:
            log.exception("optimize detector failed")
            finding = None
        if not finding:
            continue
        pattern = finding["pattern"]
        gauge, fix_kind, auto_run = findings_db.DETECTOR_MAP[pattern]
        rows.append({
            "key":          f"detector:{pattern}",
            "source":       "detector",
            "gauge":        gauge,
            "severity":     finding["severity"],
            "title":        pattern,
            "detail":       finding.get("detail"),
            "est_tokens":   finding.get("tokens_saved"),
            "est_usd":      None,
            "evidence_sql": None,
            "fix_kind":     fix_kind,
            "fix_label":    finding.get("fix_label"),
            "fix_text":     finding.get("fix_text"),
            "auto_run":     auto_run,
        })

    scan_id = findings_db.new_scan_id()
    findings_db.upsert(conn, scan_id, rows)
    findings_db.resolve_missing(conn, scan_id, ["detector"])
    conn.commit()
    return len(rows)


def _connect_and_scan() -> int:
    # sqlite3 connections are single-thread-affine: opening one on the event
    # loop's thread and handing it to run_in_executor's worker thread crashes
    # with "SQLite objects created in a thread can only be used in that same
    # thread" on every tick. connect() must happen on the SAME thread that
    # runs the query, so both live inside the executor call.
    from ..db import connect

    conn = connect()
    try:
        return run_optimize_scan(conn)
    finally:
        conn.close()


async def run_optimize_detector() -> None:
    """Background task: run optimize scan every 30 minutes."""
    loop = asyncio.get_event_loop()
    await asyncio.sleep(60)  # warm up before first scan
    while True:
        try:
            n = await loop.run_in_executor(None, _connect_and_scan)
            if n:
                log.info("optimize: %d findings", n)
        except Exception:
            log.exception("optimize scan failed")
        await asyncio.sleep(_RUN_INTERVAL_S)
