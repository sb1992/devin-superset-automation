"""Success policy: CI is the authority, one CI follow-up max, honest status mapping."""

from src.policy import (
    ci_verdict,
    evaluate_run,
    map_session_state,
    should_send_ci_feedback,
)

ALLOWLIST = ["python-lint", "unit-tests"]


def checks(**kv):
    return dict(kv)


# --- ci_verdict -------------------------------------------------------------

def test_ci_green_when_all_allowlisted_checks_succeed():
    assert ci_verdict(checks(**{"python-lint": "success", "unit-tests": "success"}), ALLOWLIST) == "green"


def test_ci_pending_when_an_allowlisted_check_is_missing_or_running():
    assert ci_verdict(checks(**{"python-lint": "success"}), ALLOWLIST) == "pending"
    assert ci_verdict(checks(**{"python-lint": "success", "unit-tests": None}), ALLOWLIST) == "pending"


def test_ci_red_when_any_allowlisted_check_fails():
    assert ci_verdict(checks(**{"python-lint": "failure", "unit-tests": "success"}), ALLOWLIST) == "red"


def test_irrelevant_check_failures_do_not_affect_verdict():
    all_checks = checks(**{"python-lint": "success", "unit-tests": "success", "docker-publish": "failure"})
    assert ci_verdict(all_checks, ALLOWLIST) == "green"


# --- map_session_state ------------------------------------------------------

def test_working_statuses_map_to_running():
    for s in ("new", "claimed", "running", "resuming", "suspended"):
        assert map_session_state(s) == "running"


def test_error_maps_to_failed_and_exit_maps_to_exited():
    assert map_session_state("error") == "failed"
    assert map_session_state("exit") == "exited"


def test_unknown_future_status_maps_to_running_not_crash():
    assert map_session_state("some-new-status") == "running"


# --- evaluate_run (terminal classification) ---------------------------------

def test_success_requires_pr_and_valid_output_and_green_ci():
    assert evaluate_run(session_state="exited", pr_exists=True, output_valid=True, ci="green") == "succeeded"


def test_exited_without_pr_is_failed():
    assert evaluate_run(session_state="exited", pr_exists=False, output_valid=True, ci="pending") == "failed"


def test_exited_with_invalid_output_but_green_ci_is_still_succeeded_with_warning_path():
    # Output validation failure alone should not override real CI evidence -> blocked for human review
    assert evaluate_run(session_state="exited", pr_exists=True, output_valid=False, ci="green") == "blocked"


def test_red_ci_after_feedback_used_is_failed():
    assert (
        evaluate_run(session_state="exited", pr_exists=True, output_valid=True, ci="red", feedback_used=True)
        == "failed"
    )


def test_red_ci_with_feedback_available_stays_open():
    assert (
        evaluate_run(session_state="exited", pr_exists=True, output_valid=True, ci="red", feedback_used=False)
        == "pr-opened"
    )


def test_running_session_with_pr_is_pr_opened():
    assert evaluate_run(session_state="running", pr_exists=True, output_valid=False, ci="pending") == "pr-opened"


def test_running_session_without_pr_stays_running():
    assert evaluate_run(session_state="running", pr_exists=False, output_valid=False, ci="pending") == "running"


# --- should_send_ci_feedback ------------------------------------------------

def test_feedback_sent_once_only():
    assert should_send_ci_feedback(ci="red", feedback_sent=False) is True
    assert should_send_ci_feedback(ci="red", feedback_sent=True) is False


def test_no_feedback_when_ci_not_red():
    assert should_send_ci_feedback(ci="green", feedback_sent=False) is False
    assert should_send_ci_feedback(ci="pending", feedback_sent=False) is False


# --- prefix matching (Superset matrix jobs like "unit-tests (3.11)") --------

def test_allowlist_matches_matrix_expanded_check_names_by_prefix():
    cc = {"unit-tests (3.11)": "success", "unit-tests (3.12)": "success", "pre-commit": "success"}
    assert ci_verdict(cc, ["unit-tests", "pre-commit"]) == "green"


def test_prefix_red_when_one_matrix_leg_fails():
    cc = {"unit-tests (3.11)": "success", "unit-tests (3.12)": "failure", "pre-commit": "success"}
    assert ci_verdict(cc, ["unit-tests", "pre-commit"]) == "red"


def test_prefix_pending_when_no_check_matches_an_entry_yet():
    cc = {"pre-commit": "success"}
    assert ci_verdict(cc, ["unit-tests", "pre-commit"]) == "pending"
