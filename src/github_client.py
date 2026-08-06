"""Thin GitHub REST client. No business logic here — callers decide everything."""

from __future__ import annotations

import requests

API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str, repo: str):
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _url(self, path: str) -> str:
        return f"{API}/repos/{self.repo}{path}"

    def _get(self, path: str, **params):
        r = self.session.get(self._url(path), params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_issue(self, number: int) -> dict:
        return self._get(f"/issues/{number}")

    def list_issues_with_labels(self, labels: list[str]) -> list[dict]:
        found: dict[int, dict] = {}
        for label in labels:
            for issue in self._get("/issues", state="open", labels=label, per_page=100):
                if "pull_request" not in issue:
                    found[issue["number"]] = issue
        return list(found.values())

    def list_comments(self, issue_number: int) -> list[dict]:
        return self._get(f"/issues/{issue_number}/comments", per_page=100)

    def create_comment(self, issue_number: int, body: str) -> dict:
        r = self.session.post(
            self._url(f"/issues/{issue_number}/comments"), json={"body": body}, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def update_comment(self, comment_id: int, body: str) -> dict:
        r = self.session.patch(
            self._url(f"/issues/comments/{comment_id}"), json={"body": body}, timeout=30
        )
        r.raise_for_status()
        return r.json()

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        r = self.session.post(
            self._url(f"/issues/{issue_number}/labels"), json={"labels": labels}, timeout=30
        )
        r.raise_for_status()

    def remove_label(self, issue_number: int, label: str) -> None:
        r = self.session.delete(self._url(f"/issues/{issue_number}/labels/{label}"), timeout=30)
        if r.status_code not in (200, 404):
            r.raise_for_status()

    def update_issue_body(self, issue_number: int, body: str) -> None:
        r = self.session.patch(
            self._url(f"/issues/{issue_number}"), json={"body": body}, timeout=30
        )
        r.raise_for_status()

    def get_pull(self, number: int) -> dict:
        return self._get(f"/pulls/{number}")

    def check_runs_for_ref(self, ref: str) -> dict[str, str | None]:
        """Return {check name: conclusion} for a commit sha (None while running)."""
        data = self._get(f"/commits/{ref}/check-runs", per_page=100)
        return {run["name"]: run["conclusion"] for run in data.get("check_runs", [])}
