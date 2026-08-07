"""Timing metrics must come from GitHub's own records, not from the moment our
poller happened to notice — otherwise every duration silently includes polling
delay."""

from src.reconcile import run_reconcile
from src.state import parse_marker
from tests.test_reconcile import VALID_OUTPUT, add_pr, dispatched_world


def test_pr_and_green_timestamps_use_github_source_times():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    gh.pulls[23]["created_at"] = "2026-08-06T16:52:00Z"
    gh.check_times = {
        "abc123": {
            "unit-tests (3.11)": "2026-08-06T17:20:00Z",
            "pre-commit": "2026-08-06T17:30:00Z",
        }
    }
    devin.sessions[sid].update(status="exit", structured_output=VALID_OUTPUT)

    run_reconcile(cfg, gh, devin)
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.pr_opened_at == "2026-08-06T16:52:00Z"   # PR creation, not detection
    assert marker.green_at == "2026-08-06T17:30:00Z"       # last decisive check to finish


def test_falls_back_to_observation_time_when_github_omits_timestamps():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    devin.sessions[sid].update(status="exit", structured_output=VALID_OUTPUT)

    run_reconcile(cfg, gh, devin)
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.pr_opened_at is not None   # still recorded, just less precise
    assert marker.green_at is not None
