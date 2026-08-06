"""In-memory fakes for the GitHub and Devin clients (behavioral test doubles)."""

from __future__ import annotations

import itertools


class FakeGitHub:
    default_author = "github-actions[bot]"

    def __init__(self, issues: dict[int, dict] | None = None):
        self.issues = issues or {}
        self.comments: dict[int, list[dict]] = {n: [] for n in self.issues}
        self.labels: dict[int, set[str]] = {
            n: {l["name"] for l in i.get("labels", [])} for n, i in self.issues.items()
        }
        self.issue_bodies: dict[int, str] = {}
        self.pulls: dict[int, dict] = {}
        self.checks: dict[str, dict] = {}
        self._comment_ids = itertools.count(1000)

    def get_issue(self, number):
        issue = dict(self.issues[number])
        issue["labels"] = [{"name": n} for n in sorted(self.labels.get(number, set()))]
        return issue

    def list_issues_with_labels(self, labels, state="open"):
        out = []
        for n, issue in self.issues.items():
            issue_state = issue.get("state", "open")
            if state != "all" and issue_state != state:
                continue
            if self.labels.get(n, set()) & set(labels):
                out.append(self.get_issue(n))
        return out

    def list_comments(self, issue_number):
        return list(self.comments.get(issue_number, []))

    def create_comment(self, issue_number, body, author=None):
        comment = {"id": next(self._comment_ids), "body": body,
                   "user": {"login": author or self.default_author}}
        self.comments.setdefault(issue_number, []).append(comment)
        return comment

    def update_comment(self, comment_id, body):
        for clist in self.comments.values():
            for c in clist:
                if c["id"] == comment_id:
                    c["body"] = body
                    return c
        raise KeyError(comment_id)

    def add_labels(self, issue_number, labels):
        self.labels.setdefault(issue_number, set()).update(labels)

    def remove_label(self, issue_number, label):
        self.labels.get(issue_number, set()).discard(label)

    def update_issue_body(self, issue_number, body):
        self.issue_bodies[issue_number] = body

    def get_pull(self, number):
        return self.pulls[number]

    def check_runs_for_ref(self, ref):
        return dict(self.checks.get(ref, {}))

    def authenticated_login(self):
        return self.default_author


class FakeDevin:
    def __init__(self):
        self.created: list[dict] = []
        self.messages: list[tuple[str, str]] = []
        self.sessions: dict[str, dict] = {}
        self._ids = itertools.count(1)

    def create_session(self, payload):
        sid = f"devin-fake-{next(self._ids)}"
        session = {
            "session_id": sid,
            "url": f"https://app.devin.ai/sessions/{sid}",
            "status": "new",
            "acus_consumed": 0,
            "pull_requests": [],
        }
        self.created.append(payload)
        self.sessions[sid] = session
        return session

    def get_session(self, session_id):
        return self.sessions[session_id]

    def send_message(self, session_id, message):
        self.messages.append((session_id, message))
        return {}
