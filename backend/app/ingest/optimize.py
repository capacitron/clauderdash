"""Waste pattern detection. Port of codeburn's optimize.ts detectors.

Runs as background task every 30 minutes. Scans recent sessions (7d) for
patterns that waste tokens/cost. Results written to waste_findings table.

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

log = logging.getLogger(__name__)

_RUN_INTERVAL_S = 30 * 60       # 30 minutes
_SCAN_DAYS      = 7
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

_MIN_JUNK_READS  = 3
_MIN_DUP_READS   = 5
_MIN_EDITS       = 10
_HEALTHY_RATIO   = 4.0
_CLAUDEMD_LIMIT  = 200
_IMPORT_RE       = re.compile(r"^@(\.\.?/[^\s]+|/[^\s]+)", re.M)
_BASH_RECOMMENDED = 15_000


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
    file_counts: dict[str, int] = {}
    for fm in session_files.values():
        for fp, cnt in fm.items():
            if cnt > 1:
                extra = cnt - 1
                total_dupes += extra
                name = Path(fp).name
                file_counts[name] = file_counts.get(name, 0) + extra

    if total_dupes < _MIN_DUP_READS:
        return None
    worst = sorted(file_counts.items(), key=lambda x: -x[1])[:3]
    worst_str = ", ".join(f"{n} ({c+1}x)" for n, c in worst)
    return {
        "pattern":     "duplicate_reads",
        "severity":    "high" if total_dupes > 30 else ("medium" if total_dupes > 10 else "low"),
        "detail":      f"{total_dupes} redundant re-reads. Top: {worst_str}. Point Claude at exact line numbers in your prompt.",
        "tokens_saved": total_dupes * 600,
        "fix_label":   "In your prompt:",
        "fix_text":    "In <file> lines <start>-<end>, look at the <function> function.",
    }


def detect_low_read_edit_ratio(tool_calls: list[dict]) -> dict | None:
    reads = sum(1 for tc in tool_calls if tc["tool_name"] in _ALL_READ)
    edits = sum(1 for tc in tool_calls if tc["tool_name"] in _ALL_EDIT)
    if edits < _MIN_EDITS:
        return None
    ratio = reads / edits
    if ratio >= _HEALTHY_RATIO:
        return None
    severity = "high" if ratio < 2 else ("medium" if ratio < 3 else "low")
    return {
        "pattern":     "low_read_edit_ratio",
        "severity":    severity,
        "detail":      f"Claude made {reads} reads and {edits} edits (ratio {ratio:.1f}:1). Healthy >= {_HEALTHY_RATIO}:1. Editing without reading leads to retries.",
        "tokens_saved": max(0, int(edits * _HEALTHY_RATIO) - reads) * 600,
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
    tokens_per_session = len(unused) * 5 * 400
    return {
        "pattern":     "unused_mcp",
        "severity":    "high" if len(unused) >= 3 else "medium",
        "detail":      f"{len(unused)} MCP server(s) configured but never called: {', '.join(unused[:5])}. Each loads ~{5*400} tokens/session.",
        "tokens_saved": tokens_per_session,
        "fix_label":   "Remove unused servers:",
        "fix_text":    "\n".join(f"claude mcp remove {s}" for s in unused[:10]),
    }


def detect_bloated_claude_md(project_cwds: list[str]) -> dict | None:
    bloated = []
    for cwd in set(project_cwds):
        for name in ["CLAUDE.md", ".claude/CLAUDE.md"]:
            full = Path(cwd) / name
            if not full.exists():
                continue
            lines = _expand_claude_md(full, set())
            if lines > _CLAUDEMD_LIMIT:
                bloated.append((str(full), lines))

    if not bloated:
        return None
    bloated.sort(key=lambda x: -x[1])
    worst_path, worst_lines = bloated[0]
    total_extra = sum(max(0, l - _CLAUDEMD_LIMIT) for _, l in bloated)
    return {
        "pattern":     "bloated_claude_md",
        "severity":    "high" if worst_lines > 400 else "medium",
        "detail":      f"{worst_path} has {worst_lines} lines (expanded). Limit: {_CLAUDEMD_LIMIT}. ~{total_extra * 13} extra tokens/call.",
        "tokens_saved": total_extra * 13,
        "fix_label":   "Ask Claude to trim it:",
        "fix_text":    f"Review CLAUDE.md and all @-imported files. Cut to under {_CLAUDEMD_LIMIT} lines. Remove anything derivable from code.",
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
        "detail":      f"Bash output cap is {actual//1000}K chars ({'configured' if limit else 'default'}). Recommend {_BASH_RECOMMENDED//1000}K. ~{extra_chars // 4} wasted tokens/bash call.",
        "tokens_saved": extra_chars // 4,
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
    for tc in tool_calls:
        if tc["tool_name"] in ("Agent", "Task"):
            try:
                inp = json.loads(tc.get("input_preview", "{}"))
                st = inp.get("subagent_type")
                if st:
                    invoked.add(st)
            except Exception:
                pass
    ghosts = [n for n in defined if n not in invoked]
    if not ghosts:
        return None
    return {
        "pattern":     "ghost_agents",
        "severity":    "high" if len(ghosts) >= 5 else ("medium" if len(ghosts) >= 2 else "low"),
        "detail":      f"{len(ghosts)} agent(s) in ~/.claude/agents/ never invoked: {', '.join(ghosts[:5])}. Each adds ~80 tokens to Task schema/session.",
        "tokens_saved": len(ghosts) * 80,
        "fix_label":   "Archive unused agents:",
        "fix_text":    "\n".join(f"mv ~/.claude/agents/{n}.md ~/.claude/agents/.archived/" for n in ghosts[:10]),
    }


def detect_ghost_skills(tool_calls: list[dict]) -> dict | None:
    skills_dir = _HOME / ".claude" / "skills"
    if not skills_dir.exists():
        return None
    defined = [p.name for p in skills_dir.iterdir() if (p / "SKILL.md").exists()]
    if not defined:
        return None
    invoked: set[str] = set()
    for tc in tool_calls:
        if tc["tool_name"] == "Skill":
            try:
                inp = json.loads(tc.get("input_preview", "{}"))
                sk = inp.get("skill") or inp.get("name")
                if sk:
                    invoked.add(sk)
            except Exception:
                pass
    ghosts = [n for n in defined if n not in invoked]
    if not ghosts:
        return None
    return {
        "pattern":     "ghost_skills",
        "severity":    "high" if len(ghosts) >= 10 else ("medium" if len(ghosts) >= 5 else "low"),
        "detail":      f"{len(ghosts)} skill(s) never invoked: {', '.join(ghosts[:5])}. Each adds ~80 tokens of metadata/session.",
        "tokens_saved": len(ghosts) * 80,
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


def run_optimize_scan(conn: sqlite3.Connection) -> int:
    """Run all detectors and write findings to waste_findings. Returns count."""
    since = _since_iso(_SCAN_DAYS)
    tool_calls, cwds = _scan_tool_calls(conn, since)
    project_cwds = list(set(cwds))
    now = datetime.now(tz=timezone.utc).isoformat()

    # Clear old findings (replace on each run)
    conn.execute("DELETE FROM waste_findings WHERE detected_at < ?",
                 (_since_iso(_SCAN_DAYS + 1),))

    detectors = [
        lambda: detect_junk_reads(tool_calls),
        lambda: detect_duplicate_reads(tool_calls),
        lambda: detect_low_read_edit_ratio(tool_calls),
        lambda: detect_unused_mcp(tool_calls, project_cwds),
        lambda: detect_bloated_claude_md(project_cwds),
        lambda: detect_bash_bloat(),
        lambda: detect_ghost_agents(tool_calls),
        lambda: detect_ghost_skills(tool_calls),
    ]

    inserted = 0
    for detect in detectors:
        try:
            finding = detect()
        except Exception:
            log.exception("optimize detector failed")
            finding = None
        if not finding:
            continue
        conn.execute(
            """
            INSERT INTO waste_findings(pattern, severity, detail, tokens_saved, fix_label, fix_text, detected_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding["pattern"],
                finding["severity"],
                finding.get("detail"),
                finding.get("tokens_saved", 0),
                finding.get("fix_label"),
                finding.get("fix_text"),
                now,
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


async def run_optimize_detector() -> None:
    """Background task: run optimize scan every 30 minutes."""
    from ..db import connect

    loop = asyncio.get_event_loop()
    await asyncio.sleep(60)  # warm up before first scan
    while True:
        try:
            conn = connect()
            n = await loop.run_in_executor(None, run_optimize_scan, conn)
            conn.close()
            if n:
                log.info("optimize: %d findings", n)
        except Exception:
            log.exception("optimize scan failed")
        await asyncio.sleep(_RUN_INTERVAL_S)
