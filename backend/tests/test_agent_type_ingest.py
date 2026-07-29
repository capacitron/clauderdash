"""_read_agent_type: sidecar lookup for subagent transcripts.

Detection is path-based ("subagents" anywhere in the parts) rather than a
fixed parent-index, because subagent files don't sit at one fixed depth — a
plain Agent-tool run is `<session>/subagents/agent-<id>.jsonl`, but a
Workflow-tool run nests further under `subagents/workflows/<wf-id>/`.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.ingest.jsonl import _read_agent_type


def test_reads_agent_type_from_direct_sidecar(tmp_path: Path) -> None:
    d = tmp_path / "session-abc" / "subagents"
    d.mkdir(parents=True)
    transcript = d / "agent-x1.jsonl"
    transcript.write_text("{}\n")
    (d / "agent-x1.meta.json").write_text(json.dumps({"agentType": "Explore"}))
    assert _read_agent_type(transcript) == "Explore"


def test_reads_agent_type_from_nested_workflow_sidecar(tmp_path: Path) -> None:
    d = tmp_path / "session-abc" / "subagents" / "workflows" / "wf-1"
    d.mkdir(parents=True)
    transcript = d / "agent-y2.jsonl"
    transcript.write_text("{}\n")
    (d / "agent-y2.meta.json").write_text(json.dumps({"agentType": "workflow-subagent"}))
    assert _read_agent_type(transcript) == "workflow-subagent"


def test_missing_sidecar_returns_none(tmp_path: Path) -> None:
    d = tmp_path / "session-abc" / "subagents"
    d.mkdir(parents=True)
    transcript = d / "agent-z3.jsonl"
    transcript.write_text("{}\n")
    assert _read_agent_type(transcript) is None


def test_malformed_sidecar_returns_none(tmp_path: Path) -> None:
    d = tmp_path / "session-abc" / "subagents"
    d.mkdir(parents=True)
    transcript = d / "agent-w4.jsonl"
    transcript.write_text("{}\n")
    (d / "agent-w4.meta.json").write_text("not json")
    assert _read_agent_type(transcript) is None


def test_non_subagent_file_returns_none(tmp_path: Path) -> None:
    """A top-level session transcript must never get an agent_type, even if a
    same-named .meta.json happens to sit beside it."""
    d = tmp_path / "session-abc"
    d.mkdir(parents=True)
    transcript = d / "main.jsonl"
    transcript.write_text("{}\n")
    (d / "main.meta.json").write_text(json.dumps({"agentType": "should-not-apply"}))
    assert _read_agent_type(transcript) is None
