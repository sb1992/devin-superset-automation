"""Success policy.

CI is the success authority: a run succeeds only when a verified PR exists,
structured output validates, and every allowlisted check is green. Devin's own
"finished" claim never marks success by itself.
"""

from __future__ import annotations

_WORKING_STATUSES = {"new", "claimed", "running", "resuming", "suspended"}
_SUCCESS_CONCLUSIONS = {"success", "neutral", "skipped"}


_FAILURE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required"}


def ci_verdict(check_conclusions: dict, allowlist: list[str]) -> str:
    """Classify CI from {check name: conclusion} against the allowlist.

    Allowlist entries match check names by prefix, because matrix jobs expand
    ("unit-tests" -> "unit-tests (3.11)"). Checks outside the allowlist never
    affect the verdict (a fork cannot run upstream-credentialed jobs, and that
    must not fail a remediation).
    """
    for entry in allowlist:
        matches = {n: c for n, c in check_conclusions.items() if n.startswith(entry)}
        if not matches:
            return "pending"
        if any(c in _FAILURE_CONCLUSIONS for c in matches.values()):
            return "red"
        if any(c not in _SUCCESS_CONCLUSIONS for c in matches.values()):
            return "pending"
    return "green"


def map_session_state(devin_status: str) -> str:
    """Map a Devin v3 session status to a controller state.

    Unknown future statuses map to "running" so a new API value degrades to
    continued polling rather than a crash or a false terminal state.
    """
    if devin_status == "error":
        return "failed"
    if devin_status == "exit":
        return "exited"
    return "running"


def evaluate_run(
    session_state: str,
    pr_exists: bool,
    output_valid: bool,
    ci: str,
    feedback_used: bool = False,
) -> str:
    """Classify a run into its issue-label state."""
    if session_state == "failed":
        return "failed"
    if session_state == "exited":
        if not pr_exists:
            return "failed"
        if ci == "green":
            return "succeeded" if output_valid else "blocked"
        if ci == "red":
            return "failed" if feedback_used else "pr-opened"
        return "pr-opened"
    # session still working
    return "pr-opened" if pr_exists else "running"


def should_send_ci_feedback(ci: str, feedback_sent: bool) -> bool:
    """One bounded CI repair message per session, only when CI is red."""
    return ci == "red" and not feedback_sent
