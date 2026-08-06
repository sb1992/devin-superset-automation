"""Dispatch: turn one approved GitHub issue into exactly one bounded Devin session.

Steps (CODEX_ACTION.md section 11): refetch the issue, verify approval, refuse
duplicates via the durable comment marker, create the session, persist the
marker, swap labels.

Durability model: the marker comment is written in a "dispatching" state BEFORE
the session is created, then updated with the session id. A crash between the
two leaves a marker that blocks duplicate spend (fail toward "no second
session", never toward "two sessions"). Markers are trusted only from
controller-authored comments; a marker block that exists but cannot be parsed
refuses dispatch entirely.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .prompts import build_prompt, redact, scrub_tokens
from .state import Marker, MarkerParse, parse_marker, render_status_comment, scan_marker

READY_LABEL = "devin:ready"
RUNNING_LABEL = "devin:running"

# Comment authors whose markers are trusted as controller state: the workflow
# token (github-actions[bot]) plus the current token's own identity, so
# operator-run dispatches outside Actions still produce trusted markers.
CONTROLLER_AUTHORS = {"github-actions[bot]"}


def _trusted_authors(gh) -> set[str]:
    trusted = set(CONTROLLER_AUTHORS)
    login = getattr(gh, "authenticated_login", lambda: None)()
    if login:
        trusted.add(login)
    return trusted


def _marker_is_self_attested(comment: dict) -> bool:
    """A marker that records its own author matches that comment's author.

    Dispatch may run under one identity (an operator token) and reconcile under
    another (the Actions token). The recorded `written_by` lets the later run
    trust the marker without widening trust to arbitrary commenters: the claim
    must match the actual comment author GitHub reports.
    """
    marker = parse_marker(comment.get("body", ""))
    if marker is None or not marker.written_by:
        return False
    return marker.written_by == _comment_author(comment)

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "remediation-result.json"


def load_result_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def _comment_author(comment: dict) -> str:
    return (comment.get("user") or {}).get("login", "")


def find_marker_comment(gh, issue_number: int):
    """Return (comment, marker) for the controller-owned status comment.

    Only controller-authored comments count. Returns (None, None) when no
    trusted marker exists. Raises _InvalidMarker if a controller-authored
    marker block exists but cannot be parsed (fail closed).
    """
    trusted = _trusted_authors(gh)
    for comment in gh.list_comments(issue_number):
        if _comment_author(comment) not in trusted and not _marker_is_self_attested(comment):
            continue
        verdict = scan_marker(comment.get("body", ""))
        if verdict is MarkerParse.VALID:
            return comment, parse_marker(comment["body"])
        if verdict is MarkerParse.INVALID:
            raise InvalidMarkerError(issue_number)
    return None, None


class InvalidMarkerError(Exception):
    """A controller-authored marker exists but cannot be parsed or is from an
    unknown version. Dispatching would risk a duplicate session."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_dispatch(cfg, gh, devin, issue_number: int) -> dict:
    issue = gh.get_issue(issue_number)

    if "pull_request" in issue:
        return {"dispatched": False, "reason": "is-pull-request", "issue": issue_number}

    labels = {l["name"] for l in issue.get("labels", [])}
    if READY_LABEL not in labels:
        return {"dispatched": False, "reason": "not-approved", "issue": issue_number}

    try:
        _, existing = find_marker_comment(gh, issue_number)
    except InvalidMarkerError:
        return {"dispatched": False, "reason": "invalid-marker", "issue": issue_number}

    if existing is not None:
        # Repair labels if an earlier partial failure lost the active label.
        if existing.state in ("dispatching", "running", "pr-opened"):
            gh.add_labels(issue_number, [RUNNING_LABEL if existing.state != "pr-opened" else "devin:pr-opened"])
            gh.remove_label(issue_number, READY_LABEL)
        return {
            "dispatched": False,
            "reason": "duplicate",
            "issue": issue_number,
            "session_id": existing.session_id,
        }

    clean_title = scrub_tokens(redact(issue.get("title", ""), cfg.secrets()))
    clean_body = scrub_tokens(redact(issue.get("body", ""), cfg.secrets()))
    prompt = build_prompt(
        issue_number=issue_number,
        issue_title=clean_title,
        issue_body=clean_body,
        repo=cfg.target_repo,
        base_branch=cfg.target_branch,
        skill_name=cfg.skill_name,
    )

    # Phase 1: durable "dispatching" marker before any spend.
    marker = Marker(
        session_id="",
        session_url="",
        dispatch_id=f"issue-{issue_number}-v1",
        issue_number=issue_number,
        dispatched_at=_utc_now(),
        state="dispatching",
        ci_feedback_sent=False,
        acus_consumed=0.0,
        pr_url=None,
        pr_number=None,
        written_by=getattr(gh, "authenticated_login", lambda: None)(),
    )
    comment = gh.create_comment(
        issue_number,
        render_status_comment(marker, status_line="Dispatching a Devin session"),
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

    # Phase 2: bind the session id into the durable marker.
    import dataclasses

    bound = dataclasses.replace(
        marker,
        session_id=session["session_id"],
        session_url=session.get("url", ""),
        state="running",
    )
    gh.update_comment(
        comment["id"],
        render_status_comment(
            bound,
            status_line="Session created — Devin is working",
            resources=_resource_lines(cfg),
            current_action="Waiting for Devin to investigate and open a PR",
        ),
    )
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
