"""Reconcile: read Devin + GitHub truth for every active run and update
labels, the sticky status comment, and terminal states.

GitHub CI is the success authority. Devin's structured output is a claim that
gets verified against the PR and its checks (CODEX_ACTION.md section 14).

Durability rules:
- The one-repair-message flag is persisted BEFORE the message is sent, so a
  crash can lose the message but never send two.
- After feedback, a grace window must elapse before a still-red run fails, so
  Devin has time to act on the repair request.
- Each issue reconciles inside its own error boundary; one broken session
  never blocks the others, and transient API errors skip the issue instead of
  producing a false terminal state.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone

from .dispatch import InvalidMarkerError, find_marker_comment
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

FEEDBACK_GRACE_MINUTES = 30

REQUIRED_OUTPUT_FIELDS = ("outcome", "root_cause", "tests", "pull_request_url")
_VALID_OUTCOMES = {"pr_opened", "blocked", "not_reproducible", "needs_human"}

_STATUS_LINES = {
    "running": "Session running — Devin is working",
    "pr-opened": "PR opened — waiting for CI",
    "succeeded": "Succeeded — PR verified and applicable CI is green",
    "failed": "Failed — see session and CI for details",
    "blocked": "Blocked — needs human review",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def output_is_valid(structured_output) -> bool:
    """Validate the structured output against the contract (types + enum),
    not just key presence."""
    if not isinstance(structured_output, dict):
        return False
    if any(f not in structured_output for f in REQUIRED_OUTPUT_FIELDS):
        return False
    if structured_output["outcome"] not in _VALID_OUTCOMES:
        return False
    if not isinstance(structured_output["root_cause"], str) or not structured_output["root_cause"].strip():
        return False
    tests = structured_output["tests"]
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        return False
    pr_url = structured_output["pull_request_url"]
    return pr_url is None or isinstance(pr_url, str)


def _pr_ref_for_repo(url: str, repo: str) -> int | None:
    """Extract the PR number only when the URL belongs to the configured repo."""
    match = re.search(rf"github\.com/{re.escape(repo)}/pull/(\d+)", url or "")
    return int(match.group(1)) if match else None


def _verify_pr(gh, session: dict, cfg) -> dict | None:
    """Verify the session's reported PR against GitHub. The PR must live in the
    configured repository and target the configured base branch. 404/missing is
    "no PR"; transient errors propagate to the per-issue boundary."""
    for pr_ref in session.get("pull_requests") or []:
        # The live v3 API uses pr_url; accept url as well for robustness.
        ref_url = pr_ref.get("pr_url") or pr_ref.get("url") or ""
        number = _pr_ref_for_repo(ref_url, cfg.target_repo)
        if number is None:
            continue
        try:
            pr = gh.get_pull(number)
        except KeyError:
            continue
        except Exception as exc:  # requests.HTTPError and friends
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                continue
            raise
        base = (pr.get("base") or {}).get("ref")
        if base != cfg.target_branch:  # absent metadata fails closed too
            continue
        return pr
    return None


def classify_checks(checks: dict, allowlist: list[str]) -> tuple[dict, dict]:
    """Split checks into the ones that decide success and the ones that don't.

    A fork cannot run every upstream job; showing a failing informational check
    beside "Succeeded" reads as a contradiction unless the two are labeled.
    """
    decisive, informational = {}, {}
    for name, conclusion in checks.items():
        target = decisive if any(name.startswith(e) for e in allowlist) else informational
        target[name] = conclusion
    return decisive, informational


def describe_gates(pr_exists: bool, output_valid: bool, ci: str) -> list[str]:
    """State each success condition and whether it is met, so a reader never has
    to infer why a run did or did not succeed."""
    return [
        f"Pull request verified on GitHub: {'yes' if pr_exists else 'no'}",
        f"Structured output valid: {'yes' if output_valid else 'no — not provided or invalid'}",
        f"Applicable CI checks: {ci}",
    ]


def blocked_reason(session_detail: str | None, output_valid: bool) -> str | None:
    """Human-readable reason a run needs attention, or None when nothing blocks."""
    if output_valid:
        return None
    if session_detail == "usage_limit_exceeded":
        return "session stopped at its usage limit before reporting results"
    return "session ended without valid structured output"


def _ci_feedback_message(failing: list[str]) -> str:
    names = ", ".join(failing[:5])
    return (
        "CI on your pull request is failing. Failing required checks: "
        f"{names}. Read the failing check output on the PR, fix the underlying "
        "problem without weakening any assertions, push to the same branch, and "
        "confirm the checks re-run. This is the only automated follow-up you "
        "will receive; if you cannot fix it, say so in structured output."
    )


def _grace_expired(sent_at: str | None) -> bool:
    if not sent_at:
        return True
    try:
        sent = datetime.strptime(sent_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age_minutes = (datetime.now(timezone.utc) - sent).total_seconds() / 60
    return age_minutes >= FEEDBACK_GRACE_MINUTES


def run_reconcile(cfg, gh, devin) -> dict:
    runs = []
    errors = []
    for issue in gh.list_issues_with_labels(ACTIVE_LABELS):
        number = issue["number"]
        try:
            run = _reconcile_issue(cfg, gh, devin, issue)
        except InvalidMarkerError:
            errors.append({"issue": number, "error": "invalid marker"})
            continue
        except Exception as exc:
            errors.append({"issue": number, "error": str(exc)[:200]})
            continue
        if run is not None:
            runs.append(run)
    return {"runs": runs, "errors": errors}


def _reconcile_issue(cfg, gh, devin, issue) -> dict | None:
    number = issue["number"]
    comment, marker = find_marker_comment(gh, number)
    if marker is None or not marker.session_id:
        return None

    session = devin.get_session(marker.session_id)
    session_state = map_session_state(session.get("status", "running"))
    acus = float(session.get("acus_consumed") or 0)

    pr = _verify_pr(gh, session, cfg)
    checks = gh.check_runs_for_ref(pr["head"]["sha"]) if pr else {}
    ci = ci_verdict(checks, cfg.ci_allowlist) if pr else "pending"
    valid = output_is_valid(session.get("structured_output"))

    feedback_sent = marker.ci_feedback_sent
    feedback_sent_at = marker.ci_feedback_sent_at
    pending_message = None

    if pr and should_send_ci_feedback(ci, feedback_sent):
        failing = sorted(
            n for n, c in checks.items()
            if any(n.startswith(e) for e in cfg.ci_allowlist)
            and c in ("failure", "cancelled", "timed_out", "action_required", "stale")
        )
        pending_message = _ci_feedback_message(failing)
        feedback_sent = True
        feedback_sent_at = _utc_now()
        state = "pr-opened"
    else:
        feedback_exhausted = feedback_sent and _grace_expired(feedback_sent_at)
        state = evaluate_run(
            session_state=session_state,
            pr_exists=pr is not None,
            output_valid=valid,
            ci=ci,
            feedback_used=feedback_exhausted,
        )
        if state == "failed" and ci == "red" and feedback_sent and not feedback_exhausted:
            state = "pr-opened"

    pr_opened_at = marker.pr_opened_at
    if pr and pr_opened_at is None:
        pr_opened_at = _utc_now()
    green_at = marker.green_at
    if ci == "green" and green_at is None:
        green_at = _utc_now()
    # First-pass = reached green without ever needing an automated repair message.
    first_pass_ci = marker.first_pass_ci
    if ci == "green" and first_pass_ci is None:
        first_pass_ci = not marker.ci_feedback_sent
    new_marker = dataclasses.replace(
        marker,
        state=state,
        acus_consumed=acus,
        ci_feedback_sent=feedback_sent,
        ci_feedback_sent_at=feedback_sent_at,
        pr_url=pr["html_url"] if pr else marker.pr_url,
        pr_number=pr["number"] if pr else marker.pr_number,
        pr_opened_at=pr_opened_at,
        green_at=green_at,
        first_pass_ci=first_pass_ci,
    )
    decisive, informational = classify_checks(checks, cfg.ci_allowlist)
    reason = blocked_reason(session.get("status_detail"), valid) if state == "blocked" else None
    validation = describe_gates(pr is not None, valid, ci)
    if decisive:
        validation.append("Required checks (decide success):")
        validation += [f"  - {n}: {c or 'running'}" for n, c in sorted(decisive.items())]
    if informational:
        validation.append("Informational checks (not used for success):")
        validation += [f"  - {n}: {c or 'running'}" for n, c in sorted(informational.items())]
    output = session.get("structured_output")
    if isinstance(output, dict) and output.get("outcome"):
        validation.insert(
            0,
            f"Devin-reported outcome: `{output['outcome']}` (independently verified against PR and CI)",
        )
    body = render_status_comment(
        new_marker,
        status_line=_STATUS_LINES.get(state, state),
        validation_lines=validation or None,
        current_action=_current_action(state, ci, reason),
    )
    # Persist state BEFORE any side effect that must not repeat (the repair
    # message): a crash after this update loses at most the message itself.
    gh.update_comment(comment["id"], body)
    if pending_message:
        devin.send_message(marker.session_id, pending_message)
    _set_state_label(gh, number, state)

    return {
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


def _current_action(state: str, ci: str, reason: str | None = None) -> str:
    if state == "running":
        return "Waiting for Devin to open a PR"
    if state == "pr-opened":
        return "Waiting for applicable CI checks" if ci != "red" else "CI failed — repair requested from Devin"
    if state == "blocked":
        detail = f" ({reason})" if reason else ""
        return f"Needs a human to review the PR and decide whether to accept it{detail}"
    if state == "failed":
        return "Needs a human: no verified remediation was produced"
    return "None — remediation verified"


def _set_state_label(gh, issue_number: int, state: str) -> None:
    target = f"devin:{state}"
    gh.add_labels(issue_number, [target])
    for label in _ALL_STATE_LABELS:
        if label != target:
            gh.remove_label(issue_number, label)
