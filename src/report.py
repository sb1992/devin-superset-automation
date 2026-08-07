"""Dashboard generation.

The dashboard is one generated GitHub issue body: leadership metrics on top,
a per-run table underneath. Every ratio shows numerator and denominator —
with a handful of runs, percentages would imply precision that does not exist.
"""

from __future__ import annotations

import statistics
from datetime import datetime

from .dispatch import find_marker_comment

_STATE_LABELS = [
    "devin:running",
    "devin:pr-opened",
    "devin:succeeded",
    "devin:failed",
    "devin:blocked",
]

_STATE_DISPLAY = {
    "running": "🔄 Running",
    "pr-opened": "🔍 PR opened",
    "succeeded": "✅ Succeeded",
    "failed": "❌ Failed",
    "blocked": "⚠️ Blocked",
}


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def median_minutes(pairs: list[tuple[str, str]]) -> int | None:
    """Median whole minutes between (start, end) ISO-8601 UTC timestamp pairs."""
    deltas = [
        delta
        for start, end in pairs
        if (delta := (_parse_ts(end) - _parse_ts(start)).total_seconds() / 60) >= 0
    ]
    if not deltas:
        return None
    return round(statistics.median(deltas))


def _escape_cell(text: str) -> str:
    """Keep untrusted titles from corrupting the Markdown table.

    Backslashes are escaped first so a pre-existing backslash cannot turn our
    pipe escape back into a live table delimiter.
    """
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def collect_runs(gh) -> list[dict]:
    """Rebuild the run list from GitHub state alone (markers on labeled issues).

    Uses state="all" so closing a finished issue never erases history or ACU
    totals from the dashboard.
    """
    runs = []
    for issue in gh.list_issues_with_labels(_STATE_LABELS, state="all"):
        try:
            _, marker = find_marker_comment(gh, issue["number"])
        except Exception:
            continue
        if marker is None:
            continue
        runs.append(
            {
                "issue": issue["number"],
                "title": issue.get("title", ""),
                "state": marker.state,
                "session_url": marker.session_url,
                "pr_url": marker.pr_url,
                "acus_consumed": marker.acus_consumed,
                "dispatched_at": marker.dispatched_at,
                "pr_opened_at": marker.pr_opened_at,
                "green_at": marker.green_at,
                "first_pass_ci": marker.first_pass_ci,
                "blocked_reason": marker.blocked_reason,
            }
        )
    return sorted(runs, key=lambda r: r["issue"])


def build_health(errors: list[dict], generated_at: str, runs: list[dict]) -> str:
    """Controller health. A degraded observer must never render as healthy:
    anything the reconciler skipped is named here, because a silently partial
    dashboard is worse than an obviously broken one."""
    if errors:
        lines = [
            f"**Controller status: DEGRADED** — last sync {generated_at}; "
            f"{len(errors)} run(s) skipped this cycle and their rows below may be stale:",
        ]
        lines += [f"- #{e['issue']}: {e['error']}" for e in errors]
        return "\n".join(lines)
    return (
        f"**Controller status: healthy** — last successful sync {generated_at}; "
        f"{len(runs)} tracked run(s), none skipped."
    )


def build_dashboard(runs: list[dict], generated_at: str, errors: list[dict] | None = None) -> str:
    total = len(runs)
    by_state: dict[str, int] = {}
    for run in runs:
        by_state[run["state"]] = by_state.get(run["state"], 0) + 1

    succeeded = by_state.get("succeeded", 0)
    active = by_state.get("running", 0) + by_state.get("pr-opened", 0)
    total_acu = round(sum(r.get("acus_consumed") or 0 for r in runs), 1)
    pr_pairs = [
        (r["dispatched_at"], r["pr_opened_at"])
        for r in runs
        if r.get("dispatched_at") and r.get("pr_opened_at")
    ]
    median_to_pr = median_minutes(pr_pairs)

    green_pairs = [
        (r["dispatched_at"], r["green_at"])
        for r in runs
        if r.get("dispatched_at") and r.get("green_at")
    ]
    median_to_green = median_minutes(green_pairs)
    ci_pairs = [
        (r["pr_opened_at"], r["green_at"])
        for r in runs
        if r.get("pr_opened_at") and r.get("green_at")
    ]
    median_ci = median_minutes(ci_pairs)
    verified = [r for r in runs if r.get("green_at")]
    first_pass = [r for r in verified if r.get("first_pass_ci")]
    needs_human = [r for r in runs if r["state"] in ("blocked", "failed")]

    lines = [
        "# Devin Remediation Dashboard",
        "",
        f"Last updated: {generated_at}",
        "",
        build_health(errors or [], generated_at, runs),
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Selected issues | {total} |",
        f"| Active sessions | {active} |",
        f"| Successful remediations | {succeeded} of {total} |",
        f"| Failed | {by_state.get('failed', 0)} of {total} |",
        f"| Blocked — needs a human | {by_state.get('blocked', 0)} of {total} |",
        f"| PRs opened | {sum(1 for r in runs if r.get('pr_url'))} of {total} |",
        f"| Reached green CI first pass (no repair needed) | {len(first_pass)} of {len(verified)} |",
        f"| Runs needing human attention | {len(needs_human)} of {total} |",
        f"| Median dispatch-to-PR (agent working time) | {f'{median_to_pr} minutes' if median_to_pr is not None else 'n/a'} |",
        f"| Median PR-to-green (CI queue + run) | {f'{median_ci} minutes' if median_ci is not None else 'n/a'} |",
        f"| Median dispatch-to-green (end to end) | {f'{median_to_green} minutes' if median_to_green is not None else 'n/a'} |",
        f"| Cost per remediation | unavailable — this org is credit-metered, not ACU-metered;"
        " the consumption ledger returns an empty result. Per-session usage caps are"
        " configured and enforcement was verified by probe. |",
        "",
        "## Runs",
        "",
        "| Issue | State | Devin | PR | To-PR | To-green | Dispatched |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        session = f"[session]({run['session_url']})" if run.get("session_url") else "—"
        pr = f"[PR]({run['pr_url']})" if run.get("pr_url") else "—"
        to_pr = "—"
        if run.get("dispatched_at") and run.get("pr_opened_at"):
            minutes = median_minutes([(run["dispatched_at"], run["pr_opened_at"])])
            if minutes is not None:
                to_pr = f"{minutes}m"
        to_green = "—"
        if run.get("dispatched_at") and run.get("green_at"):
            mins = median_minutes([(run["dispatched_at"], run["green_at"])])
            if mins is not None:
                to_green = f"{mins}m"
        lines.append(
            f"| #{run['issue']} {_escape_cell(run['title'][:60])} "
            f"| {_STATE_DISPLAY.get(run['state'], run['state'])} "
            f"| {session} | {pr} | {to_pr} | {to_green} "
            f"| {run.get('dispatched_at', '—')} |"
        )
        if run.get("blocked_reason"):
            lines.append(
                f"| ↳ needs a human: {_escape_cell(run['blocked_reason'])} | | | | | | |"
            )
    if not runs:
        lines.append("| — | — | — | — | — | — | — |")
    lines += [
        "",
        "_Generated by the remediation controller. Success requires a verified PR,"
        " valid structured output, and green applicable CI — never Devin's own claim."
        " Counts are shown with denominators: this is a pilot cohort, not a rate."
        " Timestamps come from GitHub (PR creation, check completion), not from when"
        " this controller polled._",
    ]
    return "\n".join(lines)
