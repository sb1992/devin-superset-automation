"""Durable run state stored as a hidden, versioned JSON block inside the
controller-owned status comment on each remediation issue."""

from __future__ import annotations

import enum
import json
import re
from dataclasses import asdict, dataclass

MARKER_VERSION = 1


class MarkerParse(enum.Enum):
    ABSENT = "absent"
    INVALID = "invalid"
    VALID = "valid"
MARKER_OPEN = "<!-- devin-remediation-state"
MARKER_CLOSE = "-->"
_MARKER_RE = re.compile(
    re.escape(MARKER_OPEN) + r"\s*(\{.*?\})\s*" + re.escape(MARKER_CLOSE),
    re.DOTALL,
)


@dataclass
class Marker:
    session_id: str
    session_url: str
    dispatch_id: str
    issue_number: int
    dispatched_at: str
    state: str
    ci_feedback_sent: bool
    acus_consumed: float
    pr_url: str | None
    pr_number: int | None
    pr_opened_at: str | None = None
    ci_feedback_sent_at: str | None = None


def render_marker(marker: Marker) -> str:
    payload = {"version": MARKER_VERSION, **asdict(marker)}
    return f"{MARKER_OPEN}\n{json.dumps(payload, indent=2)}\n{MARKER_CLOSE}"


def scan_marker(body: str) -> MarkerParse:
    """Fail-closed classification: distinguish a missing marker from a marker
    block that exists but cannot be trusted (corruption, future version)."""
    if MARKER_OPEN not in (body or ""):
        return MarkerParse.ABSENT
    return MarkerParse.VALID if parse_marker(body) is not None else MarkerParse.INVALID


def parse_marker(body: str) -> Marker | None:
    match = _MARKER_RE.search(body or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if payload.pop("version", None) != MARKER_VERSION:
        return None
    try:
        return Marker(**payload)
    except TypeError:
        return None


def render_status_comment(
    marker: Marker,
    status_line: str,
    validation_lines: list[str] | None = None,
    current_action: str | None = None,
    resources: list[str] | None = None,
) -> str:
    lines = [
        "## Devin remediation",
        "",
        f"**Status:** {status_line}",
        f"**Started:** {marker.dispatched_at}",
        f"**ACU consumed:** {marker.acus_consumed}",
        "",
        f"- Devin session: [open session]({marker.session_url})",
    ]
    if marker.pr_url:
        lines.append(f"- Pull request: [#{marker.pr_number}]({marker.pr_url})")
    if resources:
        lines += ["", "### Policy resources", ""] + [f"- {r}" for r in resources]
    if validation_lines:
        lines += ["", "### Validation", ""] + [f"- {v}" for v in validation_lines]
    if current_action:
        lines += ["", f"**Current action:** {current_action}"]
    lines += ["", render_marker(marker)]
    return "\n".join(lines)
