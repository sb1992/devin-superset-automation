"""Fixes from the external audit: outcome gating, dispatch recovery, strict gates."""

import pytest

from src.policy import ci_verdict, evaluate_run
from src.reconcile import output_is_valid, run_reconcile
from src.dispatch import run_dispatch
from src.state import parse_marker
from tests.test_dispatch import make_world
from tests.test_reconcile import VALID_OUTPUT, add_pr, dispatched_world


# --- a self-declared blocked/needs-human outcome can never be a success ------

def test_agent_declaring_blocked_is_not_a_success_even_with_green_ci():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    out = dict(VALID_OUTPUT, outcome="blocked")
    devin.sessions[sid].update(status="exit", structured_output=out)

    run_reconcile(cfg, gh, devin)
    assert "devin:blocked" in gh.labels[22]
    assert "devin:succeeded" not in gh.labels[22]


def test_needs_human_outcome_also_blocks():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    devin.sessions[sid].update(
        status="exit", structured_output=dict(VALID_OUTPUT, outcome="needs_human")
    )
    run_reconcile(cfg, gh, devin)
    assert "devin:blocked" in gh.labels[22]


def test_reported_pr_url_must_match_the_verified_pr():
    gh, devin, cfg, sid = dispatched_world()
    add_pr(gh, devin, sid, checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    mismatched = dict(VALID_OUTPUT, pull_request_url="https://github.com/sb1992/superset/pull/999")
    devin.sessions[sid].update(status="exit", structured_output=mismatched)

    run_reconcile(cfg, gh, devin)
    assert "devin:blocked" in gh.labels[22]   # claim disagrees with verified reality


# --- decisive checks must actually pass -------------------------------------

def test_skipped_required_check_is_not_green():
    assert ci_verdict({"unit-tests (3.11)": "skipped", "pre-commit": "success"},
                      ["unit-tests", "pre-commit"]) != "green"


def test_neutral_required_check_is_not_green():
    assert ci_verdict({"unit-tests (3.11)": "neutral", "pre-commit": "success"},
                      ["unit-tests", "pre-commit"]) != "green"


# --- a failed create_session must not wedge the issue -----------------------

def test_failed_session_creation_leaves_the_issue_retryable():
    gh, devin, cfg = make_world()

    def boom(payload):
        raise RuntimeError("devin api 500")

    devin.create_session = boom
    with pytest.raises(RuntimeError):
        run_dispatch(cfg, gh, devin, issue_number=22)

    # the intent marker exists but names no session
    marker = parse_marker(gh.list_comments(22)[0]["body"])
    assert marker.state == "dispatching"
    assert marker.session_id == ""
    # the approval label must survive so a retry can proceed
    assert "devin:ready" in gh.labels[22]

    # retry succeeds and reuses the same marker rather than refusing forever
    gh2, devin2, _ = make_world()
    devin.create_session = devin2.create_session
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    assert result["dispatched"] is True
    assert parse_marker(gh.list_comments(22)[0]["body"]).session_id != ""


# --- simulate must be self-contained and assert real outcomes ---------------

def test_simulate_reaches_succeeded_without_any_env_configuration(monkeypatch, capsys):
    """The README advertises these scenarios; they must work with no env set."""
    from src.main import main
    for var in ("DEVIN_TARGET_REPO", "DEVIN_TARGET_BRANCH", "DEVIN_CI_ALLOWLIST"):
        monkeypatch.delenv(var, raising=False)
    assert main(["prog", "simulate", "", "fixtures/session-finished.json"]) == 0
    out = capsys.readouterr().out
    assert "Successful remediations | 1 of 1" in out


def test_simulate_ci_red_sends_exactly_one_repair_message(monkeypatch, capsys):
    from src.main import main
    for var in ("DEVIN_TARGET_REPO", "DEVIN_TARGET_BRANCH", "DEVIN_CI_ALLOWLIST"):
        monkeypatch.delenv(var, raising=False)
    assert main(["prog", "simulate", "", "fixtures/ci-red.json"]) == 0
    out = capsys.readouterr().out
    assert out.count("CI on your pull request is failing") == 1
