"""Observability integrity: honest metric names, controller health, explicit gates."""

from src.report import build_dashboard, build_health
from src.reconcile import classify_checks, describe_gates, blocked_reason


RUNS = [
    {"issue": 2, "title": "A", "state": "succeeded", "session_url": "https://s/1",
     "pr_url": "https://p/1", "acus_consumed": 0.0, "dispatched_at": "2026-08-06T16:00:00Z",
     "pr_opened_at": "2026-08-06T16:52:00Z", "green_at": "2026-08-06T17:30:00Z",
     "first_pass_ci": True, "blocked_reason": None},
    {"issue": 8, "title": "B", "state": "blocked", "session_url": "https://s/2",
     "pr_url": "https://p/2", "acus_consumed": 0.0, "dispatched_at": "2026-08-06T17:51:00Z",
     "pr_opened_at": "2026-08-06T18:07:00Z", "green_at": None,
     "first_pass_ci": True, "blocked_reason": "session stopped at its usage limit before reporting results"},
]


def test_zero_acu_metric_is_replaced_by_honest_telemetry_status():
    md = build_dashboard(RUNS, generated_at="now")
    assert "Total ACU consumed" not in md          # the misleading zero is gone
    assert "unavailable" in md.lower()             # replaced by a stated limitation
    assert "credit-metered" in md.lower()


def test_timing_metric_names_state_what_is_measured():
    md = build_dashboard(RUNS, generated_at="now")
    assert "approval-to-PR" not in md              # we never observed approval time
    assert "dispatch-to-PR" in md


def test_time_to_green_is_reported_when_known():
    md = build_dashboard(RUNS, generated_at="now")
    assert "dispatch-to-green" in md.lower()
    assert "90" in md                              # 16:00 -> 17:30


def test_first_pass_ci_rate_uses_counts_not_percentages():
    md = build_dashboard(RUNS, generated_at="now")
    assert "2 of 2" in md                          # both green without a repair message
    assert "%" not in md


def test_blocked_run_shows_its_reason_and_next_action():
    md = build_dashboard(RUNS, generated_at="now")
    assert "usage limit" in md
    assert "human" in md.lower()


def test_health_section_reports_degraded_when_reconcile_skipped_anything():
    healthy = build_health(errors=[], generated_at="now", runs=RUNS)
    assert "healthy" in healthy.lower()
    degraded = build_health(errors=[{"issue": 5, "error": "boom"}], generated_at="now", runs=RUNS)
    assert "degraded" in degraded.lower()
    assert "#5" in degraded                        # names what was skipped
    assert "boom" in degraded


def test_dashboard_embeds_health_so_a_degraded_observer_cannot_look_healthy():
    md = build_dashboard(RUNS, generated_at="now", errors=[{"issue": 5, "error": "api 500"}])
    assert "degraded" in md.lower()
    assert "api 500" in md


# --- reconcile-side gates ---------------------------------------------------

def test_checks_are_split_into_decisive_and_informational():
    checks = {"unit-tests (3.11)": "success", "pre-commit": "success", "docker-publish": "failure"}
    decisive, informational = classify_checks(checks, ["unit-tests", "pre-commit"])
    assert set(decisive) == {"unit-tests (3.11)", "pre-commit"}
    assert set(informational) == {"docker-publish"}


def test_gate_lines_state_each_success_condition_explicitly():
    lines = describe_gates(pr_exists=True, output_valid=False, ci="green")
    joined = " ".join(lines).lower()
    assert "pull request" in joined
    assert "structured output" in joined
    assert "not provided" in joined or "invalid" in joined
    assert "ci" in joined


def test_blocked_reason_explains_missing_output():
    assert "usage limit" in blocked_reason(session_detail="usage_limit_exceeded", output_valid=False)
    assert "structured output" in blocked_reason(session_detail="inactivity", output_valid=False)
    assert blocked_reason(session_detail="inactivity", output_valid=True) is None


def test_prerendered_sublist_items_are_not_double_bulleted():
    from src.state import Marker, render_status_comment
    m = Marker(session_id="s", session_url="u", dispatch_id="d", issue_number=1,
               dispatched_at="t", state="pr-opened", ci_feedback_sent=False,
               acus_consumed=0.0, pr_url=None, pr_number=None)
    body = render_status_comment(m, status_line="x",
                                 validation_lines=["Top level", "  - nested item: success"])
    assert "- Top level" in body
    assert "-   - nested" not in body        # no double bullet
    assert "  - nested item: success" in body
