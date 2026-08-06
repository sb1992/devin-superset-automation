"""Dispatch: turn one approved GitHub issue into exactly one bounded Devin session.

Steps (CODEX_ACTION.md section 11): refetch the issue, verify approval, refuse
duplicates via the durable comment marker, create the session, persist the
marker, swap labels.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .prompts import build_prompt
from .state import Marker, parse_marker, render_status_comment

READY_LABEL = "devin:ready"
RUNNING_LABEL = "devin:running"

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "remediation-result.json"


def load_result_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def find_marker_comment(gh, issue_number: int):
    """Return (comment, marker) for the controller-owned status comment, or (None, None)."""
    for comment in gh.list_comments(issue_number):
        marker = parse_marker(comment.get("body", ""))
        if marker is not None:
            return comment, marker
    return None, None


def run_dispatch(cfg, gh, devin, issue_number: int) -> dict:
    issue = gh.get_issue(issue_number)
    labels = {l["name"] for l in issue.get("labels", [])}

    if READY_LABEL not in labels:
        return {"dispatched": False, "reason": "not-approved", "issue": issue_number}

    _, existing = find_marker_comment(gh, issue_number)
    if existing is not None:
        return {
            "dispatched": False,
            "reason": "duplicate",
            "issue": issue_number,
            "session_id": existing.session_id,
        }

    prompt = build_prompt(
        issue_number=issue_number,
        issue_title=issue.get("title", ""),
        issue_body=issue.get("body", ""),
        repo=cfg.target_repo,
        base_branch=cfg.target_branch,
        skill_name=cfg.skill_name,
    )

    payload = {
        "title": f"Remediate Superset issue #{issue_number}",
        "prompt": prompt,
        "repos": [cfg.target_repo],
        "resumable": True,
        "max_acu_limit": cfg.max_acu_limit,
        "tags": [
            "source:github",
            "workflow:quarantine-to-green",
            f"repo:{cfg.target_repo}",
            f"issue:{issue_number}",
        ],
        "structured_output_required": True,
        "structured_output_schema": load_result_schema(),
    }
    if cfg.playbook_id:
        payload["playbook_id"] = cfg.playbook_id
    if cfg.knowledge_id:
        payload["knowledge_ids"] = [cfg.knowledge_id]

    session = devin.create_session(payload)

    marker = Marker(
        session_id=session["session_id"],
        session_url=session.get("url", ""),
        dispatch_id=f"issue-{issue_number}-v1",
        issue_number=issue_number,
        dispatched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        state="running",
        ci_feedback_sent=False,
        acus_consumed=0.0,
        pr_url=None,
        pr_number=None,
    )
    body = render_status_comment(
        marker,
        status_line="Session created — Devin is working",
        resources=_resource_lines(cfg),
        current_action="Waiting for Devin to investigate and open a PR",
    )
    gh.create_comment(issue_number, body)
    gh.add_labels(issue_number, [RUNNING_LABEL])
    gh.remove_label(issue_number, READY_LABEL)

    return {
        "dispatched": True,
        "issue": issue_number,
        "session_id": session["session_id"],
        "session_url": session.get("url", ""),
    }


def _resource_lines(cfg) -> list[str]:
    lines = [f"Skill: `{cfg.skill_name}`"]
    if cfg.playbook_id:
        lines.append(f"Playbook: `{cfg.playbook_id}`")
    if cfg.knowledge_id:
        lines.append(f"Knowledge: `{cfg.knowledge_id}`")
    return lines
