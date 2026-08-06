"""Reconcile: read Devin + GitHub truth for every active run and update
labels, the sticky status comment, and terminal states.

GitHub CI is the success authority. Devin's structured output is a claim that
gets verified against the PR and its checks (CODEX_ACTION.md section 14).
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

from .dispatch import find_marker_comment
from .policy import ci_verdict, evaluate_run, map_session_state, should_send_ci_feedback
from .state import render_status_comment

ACTIVE_LABELS = ["devin:running", "devin:pr-opened"]
_ALL_STATE_LABELS = [
    "devin:running",
    "devin:pr-opened",
    "devin:succeeded",
    "devin:failed",
    "devin:blocked",
]
_PR_URL_RE = re.compile(r"/pull/(\d+)")

REQUIRED_OUTPUT_FIELDS = ("outcome", "root_cause", "tests", "pull_request_url")

_STATUS_LINES = {
    "running": "Session running — Devin is working",
    "pr-opened": "PR opened — waiting for CI",
    "succeeded": "Succeeded — PR verified and applicable CI is green",
    "failed": "Failed — see session and CI for details",
    "blocked": "Blocked — needs human review",
}


def output_is_valid(structured_output) -> bool:
    if not isinstance(structured_output, dict):
        return False
    return all(f in structured_output for f in REQUIRED_OUTPUT_FIELDS)


def _pr_number_from_url(url: str) -> int | None:
    match = _PR_URL_RE.search(url or "")
    return int(match.group(1)) if match else None


def _verify_pr(gh, session: dict) -> dict | None:
    """Verify the session's reported PR against GitHub; return the PR or None."""
    for pr_ref in session.get("pull_requests") or []:
        number = _pr_number_from_url(pr_ref.get("url", ""))
        if number is None:
            continue
        try:
            return gh.get_pull(number)
        except KeyError:
            continue
        except Exception:
            continue
    return None


def _ci_feedback_message(failing: list[str]) -> str:
    names = ", ".join(failing[:5])
    return (
        "CI on your pull request is failing. Failing required checks: "
        f"{names}. Read the failing check output on the PR, fix the underlying "
        "problem without weakening any assertions, push to the same branch, and "
        "confirm the checks re-run. This is the only automated follow-up you "
        "will receive; if you cannot fix it, say so in structured output."
    )


def run_reconcile(cfg, gh, devin) -> dict:
    runs = []
    for issue in gh.list_issues_with_labels(ACTIVE_LABELS):
        number = issue["number"]
        comment, marker = find_marker_comment(gh, number)
        if marker is None:
            continue

        session = devin.get_session(marker.session_id)
        session_state = map_session_state(session.get("status", "running"))
        acus = float(session.get("acus_consumed") or 0)

        pr = _verify_pr(gh, session)
        checks = gh.check_runs_for_ref(pr["head"]["sha"]) if pr else {}
        ci = ci_verdict(checks, cfg.ci_allowlist) if pr else "pending"
        valid = output_is_valid(session.get("structured_output"))

        feedback_sent = marker.ci_feedback_sent
        if pr and should_send_ci_feedback(ci, feedback_sent):
            failing = sorted(
                n for n, c in checks.items()
                if any(n.startswith(e) for e in cfg.ci_allowlist)
                and c in ("failure", "cancelled", "timed_out", "action_required")
            )
            devin.send_message(marker.session_id, _ci_feedback_message(failing))
            feedback_sent = True
            state = "pr-opened"
        else:
            state = evaluate_run(
                session_state=session_state,
                pr_exists=pr is not None,
                output_valid=valid,
                ci=ci,
                feedback_used=feedback_sent,
            )

        pr_opened_at = marker.pr_opened_at
        if pr and pr_opened_at is None:
            pr_opened_at = _utc_now()
        new_marker = dataclasses.replace(
            marker,
            state=state,
            acus_consumed=acus,
            ci_feedback_sent=feedback_sent,
            pr_url=pr["html_url"] if pr else marker.pr_url,
            pr_number=pr["number"] if pr else marker.pr_number,
            pr_opened_at=pr_opened_at,
        )
        validation = [f"{name}: {conclusion or 'running'}" for name, conclusion in sorted(checks.items())]
        body = render_status_comment(
            new_marker,
            status_line=_STATUS_LINES.get(state, state),
            validation_lines=validation or None,
            current_action=_current_action(state, ci),
        )
        gh.update_comment(comment["id"], body)
        _set_state_label(gh, number, state)

        runs.append(
            {
                "issue": number,
                "title": issue.get("title", ""),
                "state": state,
                "session_id": marker.session_id,
                "session_url": marker.session_url,
                "pr_url": new_marker.pr_url,
                "ci": ci,
                "acus_consumed": acus,
                "dispatched_at": marker.dispatched_at,
            }
        )

    return {"runs": runs}


def _current_action(state: str, ci: str) -> str:
    if state == "running":
        return "Waiting for Devin to open a PR"
    if state == "pr-opened":
        return "Waiting for applicable CI checks" if ci != "red" else "CI failed — repair requested from Devin"
    return "None — terminal state"


def _set_state_label(gh, issue_number: int, state: str) -> None:
    target = f"devin:{state}"
    for label in _ALL_STATE_LABELS:
        if label != target:
            gh.remove_label(issue_number, label)
    gh.add_labels(issue_number, [target])
