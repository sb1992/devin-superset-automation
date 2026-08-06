"""Reconcile: session -> GitHub truth -> labels/comments/terminal states."""

from src.dispatch import run_dispatch
from src.reconcile import run_reconcile
from src.state import parse_marker
from tests.fakes import FakeDevin, FakeGitHub
from tests.test_dispatch import make_config, make_world

VALID_OUTPUT = {
    "outcome": "pr_opened",
    "root_cause": "hardcoded id",
    "tests": ["pytest tests/x.py"],
    "pull_request_url": "https://github.com/sb1992/superset/pull/23",
}


def dispatched_world():
    gh, devin, cfg = make_world()
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    sid = result["session_id"]
    return gh, devin, cfg, sid


def add_pr(gh, devin, sid, number=23, sha="abc123", checks=None):
    devin.sessions[sid]["pull_requests"] = [
        {"url": f"https://github.com/sb1992/superset/pull/{number}", "state": "open"}
    ]
    gh.pulls[number] = {
        "number": number,
        "html_url": f"https://github.com/sb1992/superset/pull/{number}",
        "head": {"sha": sha},
        "base": {"ref": "master"},
        "state": "open",
    }
    if checks is not None:
        gh.checks[sha] = checks


def test_running_session_updates_acu_and_stays_running():
    gh, devin, cfg, sid = dispatched_world()
    devin.sessions[sid].update(status="running", acus_consumed=1.4)

    summary = run_reconcile(cfg, gh, devin)

    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.acus_consumed == 1.4
    assert marker.state == "running"
    assert "devin:running" in gh.labels[22]
    assert summary["runs"][0]["state"] == "running"


def test_pr_discovered_moves_to_pr_opened_and_verifies_via_github():
    gh, devin, cfg, sid = dispatched_world()
    devin.sessions[sid].update(status="running", acus_consumed=2.0)
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": None, "pre-commit": None})

    run_reconcile(cfg, gh, devin)

    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.state == "pr-opened"
    assert marker.pr_number == 23
    assert "devin:pr-opened" in gh.labels[22]
    assert "devin:running" not in gh.labels[22]


def test_exit_with_green_ci_and_valid_output_succeeds():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    devin.sessions[sid].update(
        status="exit", acus_consumed=2.1, structured_output=VALID_OUTPUT
    )

    summary = run_reconcile(cfg, gh, devin)

    assert "devin:succeeded" in gh.labels[22]
    assert "devin:pr-opened" not in gh.labels[22]
    assert summary["runs"][0]["state"] == "succeeded"


def test_red_ci_sends_exactly_one_feedback_message():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "failure", "pre-commit": "success"})
    devin.sessions[sid].update(status="exit", structured_output=VALID_OUTPUT)

    run_reconcile(cfg, gh, devin)
    assert len(devin.messages) == 1
    assert "unit-tests (3.11)" in devin.messages[0][1]
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.ci_feedback_sent is True

    # second reconcile with CI still red: no second message; stays open inside
    # the repair grace window (failure-after-grace covered in test_review_fixes)
    run_reconcile(cfg, gh, devin)
    assert len(devin.messages) == 1
    assert "devin:failed" not in gh.labels[22]
    assert "devin:pr-opened" in gh.labels[22]


def test_exit_without_pr_fails():
    gh, devin, cfg, sid = dispatched_world()
    devin.sessions[sid].update(status="exit", structured_output=None)

    run_reconcile(cfg, gh, devin)
    assert "devin:failed" in gh.labels[22]


def test_green_ci_but_invalid_output_is_blocked_for_human():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    devin.sessions[sid].update(status="exit", structured_output={"outcome": "pr_opened"})

    run_reconcile(cfg, gh, devin)
    assert "devin:blocked" in gh.labels[22]


def test_no_active_issues_is_a_clean_noop():
    gh = FakeGitHub(issues={})
    devin = FakeDevin()
    summary = run_reconcile(make_config(), gh, devin)
    assert summary["runs"] == []


def test_terminal_issues_are_not_reprocessed():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    devin.sessions[sid].update(status="exit", acus_consumed=2.1, structured_output=VALID_OUTPUT)
    run_reconcile(cfg, gh, devin)
    calls_before = len(devin.messages)

    summary = run_reconcile(cfg, gh, devin)
    # succeeded issue no longer carries an active label, so nothing to do
    assert summary["runs"] == []
    assert len(devin.messages) == calls_before


def test_pr_opened_at_is_stamped_once_when_pr_first_seen():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={})
    run_reconcile(cfg, gh, devin)
    marker1 = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker1.pr_opened_at is not None

    run_reconcile(cfg, gh, devin)
    marker2 = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker2.pr_opened_at == marker1.pr_opened_at
