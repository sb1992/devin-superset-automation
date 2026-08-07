"""Dashboard generation: honest counts with denominators, real ACU, medians."""

from src.report import build_dashboard, collect_runs, median_minutes
from src.dispatch import run_dispatch
from src.reconcile import run_reconcile
from tests.test_dispatch import make_world
from tests.test_reconcile import VALID_OUTPUT, add_pr


def test_median_minutes():
    assert median_minutes([("2026-08-07T04:00:00Z", "2026-08-07T04:18:00Z")]) == 18
    assert median_minutes(
        [
            ("2026-08-07T04:00:00Z", "2026-08-07T04:10:00Z"),
            ("2026-08-07T04:00:00Z", "2026-08-07T04:30:00Z"),
        ]
    ) == 20
    assert median_minutes([]) is None


def test_collect_runs_reads_markers_from_all_state_labels():
    gh, devin, cfg = make_world()
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    add_pr(gh, devin, result["session_id"],
           checks={"unit-tests (3.11)": "success", "pre-commit": "success"})
    devin.sessions[result["session_id"]].update(
        status="exit", acus_consumed=2.1, structured_output=VALID_OUTPUT
    )
    run_reconcile(cfg, gh, devin)  # terminal: succeeded, active labels removed

    runs = collect_runs(gh)
    assert len(runs) == 1
    assert runs[0]["state"] == "succeeded"
    assert runs[0]["acus_consumed"] == 2.1


def test_dashboard_shows_denominators_and_no_bare_percentages():
    runs = [
        {"issue": 21, "title": "A", "state": "succeeded", "session_url": "https://s/1",
         "pr_url": "https://p/1", "acus_consumed": 2.1,
         "dispatched_at": "2026-08-07T04:00:00Z", "pr_opened_at": "2026-08-07T04:18:00Z"},
        {"issue": 22, "title": "B", "state": "running", "session_url": "https://s/2",
         "pr_url": None, "acus_consumed": 0.7,
         "dispatched_at": "2026-08-07T05:00:00Z", "pr_opened_at": None},
    ]
    md = build_dashboard(runs, generated_at="2026-08-07 05:10 UTC")

    assert "1 of 2" in md            # successes shown with denominator
    assert "%" not in md             # no bare percentages at small n
    assert "18" in md                # median dispatch->PR minutes
    assert "https://s/1" in md and "https://p/1" in md
    assert "#21" in md and "#22" in md
    assert "Last updated: 2026-08-07 05:10 UTC" in md


def test_dashboard_handles_zero_runs():
    md = build_dashboard([], generated_at="now")
    assert "0 of 0" in md


def test_median_ignores_pairs_with_end_before_start():
    pairs = [
        ("2026-08-07T04:00:00Z", "2026-08-07T04:18:00Z"),
        ("2026-08-07T04:00:00Z", "2026-08-06T04:00:00Z"),  # skew: end < start
    ]
    assert median_minutes(pairs) == 18
    assert median_minutes([("2026-08-07T04:00:00Z", "2026-08-06T04:00:00Z")]) is None


def test_dashboard_shows_duration_and_states_cost_is_unavailable():
    runs = [
        {"issue": 21, "title": "A", "state": "succeeded", "session_url": "https://s/1",
         "pr_url": "https://p/1", "acus_consumed": 0.0,
         "dispatched_at": "2026-08-07T04:00:00Z", "pr_opened_at": "2026-08-07T04:18:00Z"},
    ]
    md = build_dashboard(runs, generated_at="now")
    assert "18m" in md                     # duration to PR
    assert "unavailable" in md.lower()     # cost stated as unavailable, not zero
