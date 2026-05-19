"""Session activity classifier. Deterministic, no LLM calls.

Port of codeburn's classifier.ts — classifies each session into one of 13
categories by inspecting tool_names used + last user message text.

Classification runs at session close (outcome != 'unfinished').
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter

# ─── Tool sets ────────────────────────────────────────────────────────────────
_EDIT_TOOLS = frozenset(["Edit", "Write", "FileEditTool", "FileWriteTool", "NotebookEdit"])
_READ_TOOLS  = frozenset(["Read", "Grep", "Glob", "FileReadTool", "GrepTool", "GlobTool"])
_BASH_TOOLS  = frozenset(["Bash", "BashTool", "PowerShellTool"])
_TASK_TOOLS  = frozenset(["TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TodoWrite"])
_SEARCH_TOOLS = frozenset(["WebSearch", "WebFetch", "ToolSearch"])

# ─── Regex patterns ───────────────────────────────────────────────────────────
_TEST_RE      = re.compile(r"\b(test|pytest|vitest|jest|mocha|spec|coverage|npm\s+test|npx\s+vitest|npx\s+jest)\b", re.I)
_GIT_RE       = re.compile(r"\bgit\s+(push|pull|commit|merge|rebase|checkout|branch|stash|log|diff|status|add|reset|cherry-pick|tag)\b", re.I)
_BUILD_RE     = re.compile(r"\b(npm\s+run\s+build|npm\s+publish|pip\s+install|docker|deploy|make\s+build|npm\s+run\s+dev|npm\s+start|pm2|systemctl|cargo\s+build)\b", re.I)
_INSTALL_RE   = re.compile(r"\b(npm\s+install|pip\s+install|brew\s+install|apt\s+install|cargo\s+add|yay|pacman)\b", re.I)

_DEBUG_RE     = re.compile(r"\b(fix|bug|error|broken|failing|crash|issue|debug|traceback|exception|stack\s*trace|not\s+working|wrong|unexpected|status\s+code|404|500|401|403)\b", re.I)
_FEATURE_RE   = re.compile(r"\b(add|create|implement|new|build|feature|introduce|set\s*up|scaffold|generate|make\s+(?:a|me|the)|write\s+(?:a|me|the))\b", re.I)
_REFACTOR_RE  = re.compile(r"\b(refactor|clean\s*up|rename|reorganize|simplify|extract|restructure|move|migrate|split)\b", re.I)
_BRAINSTORM_RE= re.compile(r"\b(brainstorm|idea|what\s+if|explore|think\s+about|approach|strategy|design|consider|how\s+should|what\s+would|opinion|suggest|recommend)\b", re.I)
_RESEARCH_RE  = re.compile(r"\b(research|investigate|look\s+into|find\s+out|check|search|analyze|review|understand|explain|how\s+does|what\s+is|show\s+me|list|compare)\b", re.I)

ACTIVITY_TYPES = (
    "debugging", "feature", "refactoring", "testing", "git", "build_deploy",
    "exploration", "planning", "brainstorming", "delegation", "coding",
    "general", "conversation", "other",
)


def _tool_flags(tool_names: list[str]) -> dict[str, bool]:
    has_edit   = any(t in _EDIT_TOOLS   for t in tool_names)
    has_read   = any(t in _READ_TOOLS   for t in tool_names)
    has_bash   = any(t in _BASH_TOOLS   for t in tool_names)
    has_task   = any(t in _TASK_TOOLS   for t in tool_names)
    has_search = any(t in _SEARCH_TOOLS for t in tool_names)
    has_mcp    = any(t.startswith("mcp__") for t in tool_names)
    has_skill  = "Skill" in tool_names
    has_agent  = "Agent" in tool_names
    has_plan   = "EnterPlanMode" in tool_names or "ExitPlanMode" in tool_names
    return dict(
        edit=has_edit, read=has_read, bash=has_bash, task=has_task,
        search=has_search, mcp=has_mcp, skill=has_skill, agent=has_agent, plan=has_plan,
    )


def _classify_by_tools(flags: dict[str, bool], user_msg: str) -> str | None:
    if flags["plan"]:
        return "planning"
    if flags["agent"]:
        return "delegation"

    if flags["bash"] and not flags["edit"]:
        if _TEST_RE.search(user_msg):    return "testing"
        if _GIT_RE.search(user_msg):     return "git"
        if _BUILD_RE.search(user_msg):   return "build_deploy"
        if _INSTALL_RE.search(user_msg): return "build_deploy"

    if flags["edit"]:
        return "coding"

    if flags["bash"] and flags["read"]: return "exploration"
    if flags["bash"]:                   return "coding"
    if flags["search"] or flags["mcp"]: return "exploration"
    if flags["read"] and not flags["edit"]: return "exploration"
    if flags["task"] and not flags["edit"]: return "planning"
    if flags["skill"]:                  return "general"
    return None


def _refine(base: str, user_msg: str) -> str:
    if base == "coding":
        if _DEBUG_RE.search(user_msg):   return "debugging"
        if _REFACTOR_RE.search(user_msg): return "refactoring"
        if _FEATURE_RE.search(user_msg): return "feature"
        return "coding"
    if base == "exploration":
        if _DEBUG_RE.search(user_msg):   return "debugging"
        return "exploration"
    return base


def _classify_from_message(user_msg: str) -> str:
    if _BRAINSTORM_RE.search(user_msg): return "brainstorming"
    if _RESEARCH_RE.search(user_msg):   return "exploration"
    if _DEBUG_RE.search(user_msg):      return "debugging"
    if _FEATURE_RE.search(user_msg):    return "feature"
    return "conversation"


def classify_session(tool_names: list[str], last_user_message: str | None) -> str:
    """Return activity_type string for a session."""
    msg = (last_user_message or "").lower()
    flags = _tool_flags(tool_names)
    has_any_tool = bool(tool_names)

    if not has_any_tool:
        return _classify_from_message(msg)

    base = _classify_by_tools(flags, msg)
    if base:
        return _refine(base, msg)
    return _classify_from_message(msg)


def count_retries(tool_names: list[str]) -> int:
    """Count edit→bash→edit patterns (proxy for one-shot failure)."""
    retries = 0
    saw_edit = False
    saw_bash_after_edit = False
    for t in tool_names:
        if t in _EDIT_TOOLS:
            if saw_bash_after_edit:
                retries += 1
            saw_edit = True
            saw_bash_after_edit = False
        elif t in _BASH_TOOLS and saw_edit:
            saw_bash_after_edit = True
    return retries


def classify_all_sessions(conn: sqlite3.Connection) -> int:
    """Classify all unclassified (or 'other') finished sessions. Returns count updated."""
    rows = conn.execute(
        """
        SELECT id, last_user_message
          FROM sessions
         WHERE outcome IN ('ok', 'errored')
           AND (activity_type IS NULL OR activity_type = 'other')
        """
    ).fetchall()

    updated = 0
    for row in rows:
        session_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        user_msg   = row["last_user_message"] if isinstance(row, sqlite3.Row) else row[1]

        tool_rows = conn.execute(
            "SELECT tool_name FROM tool_calls WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        ).fetchall()
        tool_names = [r["tool_name"] if isinstance(r, sqlite3.Row) else r[0] for r in tool_rows]

        activity = classify_session(tool_names, user_msg)
        retries  = count_retries(tool_names)
        one_shot = 1 if (retries == 0 and any(t in _EDIT_TOOLS for t in tool_names)) else 0

        conn.execute(
            "UPDATE sessions SET activity_type = ?, one_shot = ? WHERE id = ?",
            (activity, one_shot, session_id),
        )
        updated += 1

    if updated:
        conn.commit()
    return updated
