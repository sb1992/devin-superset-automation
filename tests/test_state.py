"""State marker: the hidden JSON block in the status comment that acts as durable state."""

from src.state import Marker, parse_marker, render_marker, render_status_comment


def make_marker(**overrides):
    base = dict(
        session_id="devin-abc123",
        session_url="https://app.devin.ai/sessions/abc123",
        dispatch_id="issue-22-v1",
        issue_number=22,
        dispatched_at="2026-08-07T04:14:00Z",
        state="running",
        ci_feedback_sent=False,
        acus_consumed=0.0,
        pr_url=None,
        pr_number=None,
        pr_opened_at=None,
    )
    base.update(overrides)
    return Marker(**base)


def test_marker_round_trips_through_comment_body():
    marker = make_marker()
    body = render_status_comment(marker, status_line="Session running")
    parsed = parse_marker(body)
    assert parsed == marker


def test_parse_returns_none_when_no_marker_present():
    assert parse_marker("just a human comment") is None


def test_parse_survives_surrounding_prose_and_other_html_comments():
    marker = make_marker(state="pr-opened", pr_url="https://github.com/x/y/pull/9", pr_number=9)
    body = (
        "<!-- unrelated comment -->\n## Devin remediation\nSome text\n"
        + render_marker(marker)
        + "\ntrailing text"
    )
    parsed = parse_marker(body)
    assert parsed is not None
    assert parsed.pr_number == 9
    assert parsed.state == "pr-opened"


def test_parse_rejects_unknown_future_version():
    marker = make_marker()
    body = render_marker(marker).replace('"version": 1', '"version": 99')
    assert parse_marker(body) is None


def test_parse_rejects_malformed_json_marker():
    body = "<!-- devin-remediation-state\n{not json}\n-->"
    assert parse_marker(body) is None


def test_render_status_comment_shows_human_fields():
    marker = make_marker(acus_consumed=1.8, pr_url="https://github.com/x/y/pull/23", pr_number=23)
    body = render_status_comment(
        marker,
        status_line="PR opened — CI running",
        validation_lines=["Focused test: passed", "Python Unit Tests: running"],
    )
    assert "PR opened — CI running" in body
    assert "1.8" in body
    assert "pull/23" in body
    assert "Focused test: passed" in body
