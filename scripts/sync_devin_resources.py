"""Sync the source-controlled Playbook and Knowledge note to the Devin org.

Idempotent by name: existing resources with the same name are updated, missing
ones are created. Prints the resulting IDs for use as GitHub Actions variables.

Usage: DEVIN_API_KEY=... DEVIN_ORG_ID=... python scripts/sync_devin_resources.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.devin_client import DevinClient  # noqa: E402

PLAYBOOK_NAME = "Issue to Verified Pull Request"
PLAYBOOK_FILE = Path("resources/playbooks/issue-to-verified-pr.devin.md")
KNOWLEDGE_NAME = "Superset Remediation Boundaries"
KNOWLEDGE_FILE = Path("resources/knowledge/superset-remediation-context.md")


def sync_playbook(client: DevinClient) -> str:
    """Create the playbook if absent; reuse the existing one by title otherwise."""
    for playbook in client.list_playbooks():
        if playbook.get("title") == PLAYBOOK_NAME:
            return playbook.get("playbook_id") or playbook.get("id")
    created = client.create_playbook(PLAYBOOK_NAME, PLAYBOOK_FILE.read_text())
    return created.get("playbook_id") or created.get("id")


def sync_knowledge(client: DevinClient) -> str:
    """Create the knowledge note if absent; reuse the existing one by name otherwise."""
    for note in client.list_knowledge():
        if note.get("name") == KNOWLEDGE_NAME:
            return note.get("note_id") or note.get("id")
    created = client.create_knowledge(
        KNOWLEDGE_NAME,
        KNOWLEDGE_FILE.read_text(),
        trigger="When remediating issues in the sb1992/superset fork",
    )
    return created.get("note_id") or created.get("id")


def main() -> int:
    api_key = os.environ.get("DEVIN_API_KEY", "")
    org_id = os.environ.get("DEVIN_ORG_ID", "")
    if not api_key or not org_id:
        print("set DEVIN_API_KEY and DEVIN_ORG_ID")
        return 1
    client = DevinClient(api_key, org_id)
    playbook_id = sync_playbook(client)
    knowledge_id = sync_knowledge(client)
    print(f"DEVIN_PLAYBOOK_ID={playbook_id}")
    print(f"DEVIN_KNOWLEDGE_ID={knowledge_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
