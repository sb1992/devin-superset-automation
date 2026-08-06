"""Controller configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    github_token: str
    devin_api_key: str
    devin_org_id: str
    target_repo: str
    target_branch: str
    dashboard_issue: int | None
    playbook_id: str | None
    knowledge_id: str | None
    skill_name: str
    max_acu_limit: int
    ci_allowlist: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        dashboard = os.environ.get("DEVIN_DASHBOARD_ISSUE", "").strip()
        allowlist = os.environ.get("DEVIN_CI_ALLOWLIST", "unit-tests,pre-commit")
        return cls(
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            devin_api_key=os.environ.get("DEVIN_API_KEY", ""),
            devin_org_id=os.environ.get("DEVIN_ORG_ID", ""),
            target_repo=os.environ.get("DEVIN_TARGET_REPO", ""),
            target_branch=os.environ.get("DEVIN_TARGET_BRANCH", "master"),
            dashboard_issue=int(dashboard) if dashboard.isdigit() else None,
            playbook_id=os.environ.get("DEVIN_PLAYBOOK_ID", "").strip() or None,
            knowledge_id=os.environ.get("DEVIN_KNOWLEDGE_ID", "").strip() or None,
            skill_name=os.environ.get("DEVIN_SKILL_NAME", "superset-quarantine-to-green"),
            max_acu_limit=int(os.environ.get("DEVIN_MAX_ACU", "5")),
            ci_allowlist=[e.strip() for e in allowlist.split(",") if e.strip()],
        )

    def secrets(self) -> list[str]:
        return [self.github_token, self.devin_api_key]

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise SystemExit(f"missing required configuration: {', '.join(missing)}")
