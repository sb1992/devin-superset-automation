"""Dispatch: label-gated, deduplicated session creation."""

import pytest

from src.dispatch import run_dispatch
from src.models import Config
from src.state import parse_marker
from tests.fakes import FakeDevin, FakeGitHub


def make_config(**overrides):
    base = dict(
        github_token="gh-token",
        devin_api_key="cog-key",
        devin_org_id="org-1",
        target_repo="sb1992/superset",
        target_branch="master",
        dashboard_issue=None,
        playbook_id="playbook-1",
        knowledge_id="note-1",
        skill_name="superset-quarantine-to-green",
        max_acu_limit=5,
        ci_allowlist=["unit-tests", "pre-commit"],
    )
    base.update(overrides)
    return Config(**base)


def make_world(labels=("devin:ready",), body="Fix the test. Steps..."):
    gh = FakeGitHub(
        issues={
            22: {
                "number": 22,
                "title": "Replace deprecated datetime.utcnow()",
                "body": body,
                "labels": [{"name": n} for n in labels],
                "html_url": "https://github.com/sb1992/superset/issues/22",
            }
        }
    )
    return gh, FakeDevin(), make_config()


def test_happy_path_creates_session_with_bounded_payload():
    gh, devin, cfg = make_world()
    result = run_dispatch(cfg, gh, devin, issue_number=22)

    assert result["dispatched"] is True
    assert len(devin.created) == 1
    payload = devin.created[0]
    assert payload["max_acu_limit"] == 5
    assert payload["structured_output_required"] is True
    assert "structured_output_schema" in payload
    assert payload["playbook_id"] == "playbook-1"
    assert payload["knowledge_ids"] == ["note-1"]
    assert "sb1992/superset" in payload["repos"]
    assert any("issue:22" in t for t in payload["tags"])
    assert "@skills:superset-quarantine-to-green" in payload["prompt"]
    # secrets never enter the prompt
    assert "gh-token" not in payload["prompt"]
    assert "cog-key" not in payload["prompt"]


def test_happy_path_writes_marker_comment_and_swaps_labels():
    gh, devin, cfg = make_world()
    run_dispatch(cfg, gh, devin, issue_number=22)

    comments = gh.list_comments(22)
    assert len(comments) == 1
    marker = parse_marker(comments[0]["body"])
    assert marker is not None
    assert marker.session_id.startswith("devin-fake-")
    assert marker.state == "running"
    assert "devin:running" in gh.labels[22]
    assert "devin:ready" not in gh.labels[22]


def test_refuses_duplicate_dispatch_when_marker_exists():
    gh, devin, cfg = make_world()
    run_dispatch(cfg, gh, devin, issue_number=22)
    gh.add_labels(22, ["devin:ready"])  # someone re-adds the label

    result = run_dispatch(cfg, gh, devin, issue_number=22)
    assert result["dispatched"] is False
    assert result["reason"] == "duplicate"
    assert len(devin.created) == 1  # still exactly one session


def test_refuses_when_label_missing_on_refetch():
    gh, devin, cfg = make_world(labels=())
    result = run_dispatch(cfg, gh, devin, issue_number=22)
    assert result["dispatched"] is False
    assert result["reason"] == "not-approved"
    assert devin.created == []


def test_omits_playbook_and_knowledge_when_unconfigured():
    gh, devin, _ = make_world()
    cfg = make_config(playbook_id=None, knowledge_id=None)
    run_dispatch(cfg, gh, devin, issue_number=22)
    payload = devin.created[0]
    assert "playbook_id" not in payload
    assert "knowledge_ids" not in payload


def test_missing_issue_raises_clean_error():
    gh, devin, cfg = make_world()
    with pytest.raises(KeyError):
        run_dispatch(cfg, gh, devin, issue_number=99)
