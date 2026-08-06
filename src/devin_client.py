"""Thin Devin v3 organization API client."""

from __future__ import annotations

import requests

API = "https://api.devin.ai/v3"


class DevinClient:
    def __init__(self, api_key: str, org_id: str):
        self.org = org_id
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

    def _url(self, path: str) -> str:
        return f"{API}/organizations/{self.org}{path}"

    def _request(self, method: str, path: str, **kwargs):
        r = self.session.request(method, self._url(path), timeout=60, **kwargs)
        r.raise_for_status()
        return r.json() if r.text else {}

    def create_session(self, payload: dict) -> dict:
        return self._request("POST", "/sessions", json=payload)

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/sessions/{session_id}")

    def send_message(self, session_id: str, message: str) -> dict:
        return self._request("POST", f"/sessions/{session_id}/messages", json={"message": message})

    def list_playbooks(self) -> list[dict]:
        return self._request("GET", "/playbooks?limit=100").get("items", [])

    def create_playbook(self, name: str, instructions: str) -> dict:
        return self._request("POST", "/playbooks", json={"name": name, "instructions": instructions})

    def update_playbook(self, playbook_id: str, name: str, instructions: str) -> dict:
        return self._request(
            "PUT", f"/playbooks/{playbook_id}", json={"name": name, "instructions": instructions}
        )

    def list_knowledge(self) -> list[dict]:
        return self._request("GET", "/knowledge/notes?limit=100").get("items", [])

    def create_knowledge(self, name: str, body: str) -> dict:
        return self._request("POST", "/knowledge/notes", json={"name": name, "body": body})

    def update_knowledge(self, note_id: str, name: str, body: str) -> dict:
        return self._request(
            "PUT", f"/knowledge/notes/{note_id}", json={"name": name, "body": body}
        )
