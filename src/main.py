"""Controller entrypoint.

Invoked by the Docker action with argv: [command, issue_number, fixture].
Commands:
  dispatch   — create one Devin session for an approved issue
  reconcile  — refresh all active runs and the dashboard
  simulate   — run reconcile against an offline fixture (no network, no spend)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .devin_client import DevinClient
from .dispatch import run_dispatch
from .github_client import GitHubClient
from .models import Config
from .prompts import redact
from .reconcile import run_reconcile
from .report import build_dashboard, collect_runs


def _now_display() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _step_summary(cfg: Config, lines: list[str]) -> None:
    text = "\n".join(redact(line, cfg.secrets()) for line in lines) + "\n"
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
    print(text)


def _write_report(cfg: Config, payload: dict) -> None:
    workspace = os.environ.get("GITHUB_WORKSPACE", ".")
    out = Path(workspace) / "report.json"
    out.write_text(redact(json.dumps(payload, indent=2), cfg.secrets()))


def _update_dashboard(cfg: Config, gh, errors: list[dict] | None = None) -> list[dict]:
    runs = collect_runs(gh)
    if cfg.dashboard_issue:
        body = build_dashboard(runs, generated_at=_now_display(), errors=errors)
        gh.update_issue_body(cfg.dashboard_issue, body)
    return runs


def _event_repo_allowed(cfg: Config) -> bool:
    """Reject events from any repository other than the configured fork."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        return True  # manual/local invocation; config still scopes everything
    event = json.loads(Path(event_path).read_text())
    repo = event.get("repository", {}).get("full_name", "")
    return repo == cfg.target_repo


def cmd_dispatch(cfg: Config, issue_number: int) -> int:
    cfg.require("github_token", "devin_api_key", "devin_org_id", "target_repo")
    if not _event_repo_allowed(cfg):
        print("event repository is not the configured target; refusing")
        return 1
    gh = GitHubClient(cfg.github_token, cfg.target_repo)
    devin = DevinClient(cfg.devin_api_key, cfg.devin_org_id)

    result = run_dispatch(cfg, gh, devin, issue_number)
    _update_dashboard(cfg, gh)
    _step_summary(
        cfg,
        [
            "## Devin Remediation Controller — dispatch",
            f"- Issue: #{issue_number}",
            f"- Dispatched: {result['dispatched']}",
            f"- Reason: {result.get('reason', 'ok')}",
            f"- Session: {result.get('session_id', '—')}",
            f"- Session URL: {result.get('session_url', '—')}",
            f"- ACU limit: {cfg.max_acu_limit}",
        ],
    )
    _write_report(cfg, result)
    return 0


def cmd_reconcile(cfg: Config) -> int:
    cfg.require("github_token", "devin_api_key", "devin_org_id", "target_repo")
    gh = GitHubClient(cfg.github_token, cfg.target_repo)
    devin = DevinClient(cfg.devin_api_key, cfg.devin_org_id)

    summary = run_reconcile(cfg, gh, devin)
    runs = _update_dashboard(cfg, gh, errors=summary.get("errors"))
    error_lines = [
        f"- ⚠️ #{e['issue']} skipped this cycle: {e['error']}" for e in summary.get("errors", [])
    ]
    _step_summary(
        cfg,
        ["## Devin Remediation Controller — reconcile", f"- Active runs processed: {len(summary['runs'])}"]
        + [
            f"- #{r['issue']}: {r['state']} (ci={r['ci']}, acu={r['acus_consumed']})"
            for r in summary["runs"]
        ]
        + error_lines,
    )
    _write_report(
        cfg,
        {"reconciled": summary["runs"], "errors": summary.get("errors", []), "all_runs": runs},
    )
    return 0


SIMULATION_DEFAULTS = {
    "target_repo": "sb1992/superset",
    "target_branch": "master",
    "ci_allowlist": ["unit-tests", "pre-commit"],
}


def cmd_simulate(cfg: Config, fixture_path: str) -> int:
    """Offline replay. Simulation must not depend on deployment configuration:
    a reader cloning this repo has no org, repo, or allowlist set, so the
    fixture's own world is used for anything unconfigured."""
    from .fakes import FakeDevin, FakeGitHub

    fixture = json.loads(Path(fixture_path).read_text())
    sim = fixture.get("config", {})
    cfg.target_repo = cfg.target_repo or sim.get("target_repo", SIMULATION_DEFAULTS["target_repo"])
    cfg.target_branch = cfg.target_branch or sim.get(
        "target_branch", SIMULATION_DEFAULTS["target_branch"]
    )
    cfg.ci_allowlist = cfg.ci_allowlist or sim.get(
        "ci_allowlist", SIMULATION_DEFAULTS["ci_allowlist"]
    )
    gh = FakeGitHub(issues={int(k): v for k, v in fixture.get("issues", {}).items()})
    for issue_number, comments in fixture.get("comments", {}).items():
        for body in comments:
            gh.create_comment(int(issue_number), body)
    devin = FakeDevin()
    devin.sessions.update(fixture.get("sessions", {}))
    for number, pull in fixture.get("pulls", {}).items():
        gh.pulls[int(number)] = pull
    gh.checks.update(fixture.get("checks", {}))

    summary = run_reconcile(cfg, gh, devin)
    runs = collect_runs(gh)
    print(build_dashboard(runs, generated_at=_now_display()))
    print()
    print("reconcile summary:", json.dumps(summary, indent=2))
    print("messages sent to devin:", json.dumps(devin.messages, indent=2))
    return 0


def main(argv: list[str]) -> int:
    command = (argv[1] if len(argv) > 1 else "").strip()
    issue_arg = (argv[2] if len(argv) > 2 else "").strip()
    fixture_arg = (argv[3] if len(argv) > 3 else "").strip()

    cfg = Config.from_env()
    if command == "dispatch":
        if not issue_arg.isdigit():
            print("dispatch requires an issue number")
            return 1
        return cmd_dispatch(cfg, int(issue_arg))
    if command == "reconcile":
        return cmd_reconcile(cfg)
    if command == "simulate":
        return cmd_simulate(cfg, fixture_arg or "fixtures/session-finished.json")
    print(f"unknown command: {command!r} (expected dispatch, reconcile, or simulate)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
