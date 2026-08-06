"""Behaviors added after adversarial review: fail-closed parsing, ownership,
PR binding, feedback ordering/grace, allowlist guards, PR-as-issue refusal."""

import dataclasses

import pytest

from src.dispatch import run_dispatch
from src.policy import ci_verdict, evaluate_run
from src.prompts import scrub_tokens
from src.reconcile import run_reconcile
from src.state import parse_marker, MarkerParse, scan_marker
from tests.fakes import FakeDevin, FakeGitHub
from tests.test_dispatch import make_config, make_world
from tests.test_reconcile import VALID_OUTPUT, add_pr, dispatched_world


# --- fail-closed marker handling -------------------------------------------

def test_scan_distinguishes_absent_from_invalid_marker():
    assert scan_marker("no marker here") == MarkerParse.ABSENT
    assert scan_marker("<!-- devin-remediation-state\n{broken\n-->") == MarkerParse.INVALID
    body = "<!-- devin-remediation-state\n" + '{"version": 99}' + "\n-->"
    assert scan_marker(body) == MarkerParse.INVALID


def test_dispatch_refuses_when_marker_block_is_unparseable():
    gh, devin, cfg = make_world()
    gh.create_comment(22, "<!-- devin-remediation-state\n{corrupted\n-->")
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    assert result["dispatched"] is False
    assert result["reason"] == "invalid-marker"
    assert devin.created == []


# --- marker ownership -------------------------------------------------------

def test_marker_from_non_controller_author_is_ignored_for_state_but_blocks_nothing():
    gh, devin, cfg = make_world()
    # forged marker posted by an arbitrary user
    fake = dataclasses.replace  # noqa: F841  (readability only)
    gh.create_comment(22, "<!-- devin-remediation-state\n"
                          '{"version": 1, "session_id": "devin-forged", "session_url": "x",'
                          '"dispatch_id": "d", "issue_number": 22, "dispatched_at": "t",'
                          '"state": "running", "ci_feedback_sent": false, "acus_consumed": 0,'
                          '"pr_url": null, "pr_number": null, "pr_opened_at": null}\n-->',
                      author="mallory")
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    # forged marker is not trusted as dedup state; dispatch proceeds
    assert result["dispatched"] is True
    assert len(devin.created) == 1


# --- PRs cannot be dispatched as issues -------------------------------------

def test_dispatch_refuses_pull_request_objects():
    gh, devin, cfg = make_world()
    gh.issues[22]["pull_request"] = {"url": "https://api.github.com/..."}
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    assert result["dispatched"] is False
    assert result["reason"] == "is-pull-request"
    assert devin.created == []


# --- allowlist guards -------------------------------------------------------

def test_empty_allowlist_is_never_green():
    assert ci_verdict({"anything": "success"}, []) == "pending"


def test_stale_conclusion_is_red():
    assert ci_verdict({"unit-tests (3.11)": "stale"}, ["unit-tests"]) == "red"


# --- PR binding -------------------------------------------------------------

def test_pr_from_foreign_repo_is_not_bound():
    gh, devin, cfg, sid = dispatched_world()
    devin.sessions[sid]["pull_requests"] = [
        {"pr_url": "https://github.com/other/repo/pull/23", "pr_state": "open"}
    ]
    gh.pulls[23] = {"number": 23, "html_url": "https://github.com/sb1992/superset/pull/23",
                    "head": {"sha": "s"}, "base": {"ref": "master"}, "state": "open"}
    run_reconcile(cfg, gh, devin)
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.pr_number is None


def test_pr_with_wrong_base_branch_is_not_bound():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={})
    gh.pulls[23]["base"] = {"ref": "some-feature"}
    run_reconcile(cfg, gh, devin)
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.pr_number is None


# --- feedback ordering + grace ----------------------------------------------

def test_feedback_flag_is_persisted_before_message_send():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "failure", "pre-commit": "success"})
    devin.sessions[sid].update(status="exit", structured_output=VALID_OUTPUT)

    sent_states = []
    original = devin.send_message

    def spy(session_id, message):
        marker = parse_marker(gh.list_comments(22)[0]["body"])
        sent_states.append(marker.ci_feedback_sent)
        return original(session_id, message)

    devin.send_message = spy
    run_reconcile(cfg, gh, devin)
    assert sent_states == [True]  # flag durable before the send happens


def test_red_ci_after_feedback_wins_grace_period_before_failing():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "failure", "pre-commit": "success"})
    devin.sessions[sid].update(status="exit", structured_output=VALID_OUTPUT)

    run_reconcile(cfg, gh, devin)   # sends the one feedback message
    run_reconcile(cfg, gh, devin)   # immediately after: still inside grace
    assert "devin:failed" not in gh.labels[22]
    assert "devin:pr-opened" in gh.labels[22]

    # age the feedback timestamp past the grace window
    comment = gh.list_comments(22)[0]
    marker = parse_marker(comment["body"])
    aged = dataclasses.replace(marker, ci_feedback_sent_at="2020-01-01T00:00:00Z")
    from src.state import render_status_comment
    gh.update_comment(comment["id"], render_status_comment(aged, status_line="x"))

    run_reconcile(cfg, gh, devin)
    assert "devin:failed" in gh.labels[22]


# --- per-issue error boundary ------------------------------------------------

def test_one_broken_session_does_not_abort_other_issues():
    gh, devin, cfg = make_world()
    gh.issues[30] = {"number": 30, "title": "second", "body": "b",
                     "labels": [{"name": "devin:ready"}],
                     "html_url": "https://github.com/sb1992/superset/issues/30"}
    gh.labels[30] = {"devin:ready"}
    gh.comments[30] = []
    r1 = run_dispatch(cfg, gh, devin, issue_number=22)
    run_dispatch(cfg, gh, devin, issue_number=30)
    del devin.sessions[r1["session_id"]]  # session 22 now raises on get_session

    summary = run_reconcile(cfg, gh, devin)
    issues_processed = {r["issue"] for r in summary["runs"]}
    assert 30 in issues_processed          # issue 30 still reconciled
    assert 22 not in issues_processed      # issue 22 skipped, not failed
    assert "devin:running" in gh.labels[22]  # state untouched for retry


# --- token scrubbing ---------------------------------------------------------

def test_scrub_tokens_removes_common_credential_shapes():
    text = "here ghp_AbC123def is a token and cog_deadbeef1234 and github_pat_XYZ"
    out = scrub_tokens(text)
    assert "ghp_" not in out
    assert "cog_" not in out
    assert "github_pat_" not in out


# --- label repair on duplicate dispatch --------------------------------------

def test_duplicate_dispatch_repairs_missing_running_label():
    gh, devin, cfg, sid = dispatched_world()
    gh.remove_label(22, "devin:running")  # simulate partial failure earlier
    gh.add_labels(22, ["devin:ready"])
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    assert result["reason"] == "duplicate"
    assert "devin:running" in gh.labels[22]


def test_pr_with_missing_base_metadata_is_not_bound():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={})
    del gh.pulls[23]["base"]
    run_reconcile(cfg, gh, devin)
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.pr_number is None


def test_output_with_non_string_test_entries_is_invalid():
    from src.reconcile import output_is_valid
    bad = dict(VALID_OUTPUT)
    bad["tests"] = [123]
    assert output_is_valid(bad) is False
    good = dict(VALID_OUTPUT)
    assert output_is_valid(good) is True


def test_escape_cell_neutralizes_backslash_pipe():
    from src.report import _escape_cell
    out = _escape_cell("x\\|y")
    assert "\\\\" in out  # backslash escaped before the pipe
    assert "\\|" in out
