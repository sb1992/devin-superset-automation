# Devin Superset Remediation Controller

An event-driven automation that turns approved GitHub issues in an Apache
Superset fork into Devin-authored, CI-verified pull requests — with the whole
lifecycle observable inside GitHub.

A maintainer approves work by adding one label. Everything after that is
automated: session creation, progress tracking, CI verification, a bounded
repair loop, and a generated leadership dashboard.

## Architecture

```
maintainer adds `devin:ready` to an issue
        │  (GitHub issues.labeled event)
        ▼
GitHub Actions (fork) ── builds this repo's Docker action
        │
        ▼
dispatch: dedup check → POST Devin v3 session
  (playbook + knowledge + repo skill + ACU cap + structured-output schema)
        │
        ▼
Devin Cloud: diagnoses, edits, tests, opens a PR against the fork
        │
        ▼
reconcile (5-min schedule + manual): GET session → verify PR via GitHub →
read allowlisted CI checks → update status comment, labels, dashboard issue
        │                     │
     CI green              CI red → ONE repair message to the same session
        │                     │
   devin:succeeded      re-check → devin:failed if still red
```

Three systems, three responsibilities:

- **GitHub** — event source, state store (labels + a hidden JSON marker in the
  status comment), CI authority, and the entire observability UX.
- **This controller** (Docker action, batch-invoked) — policy, idempotency,
  budgets, verification, reporting. It exits after every run; there is no
  server and no database.
- **Devin Cloud** — the coding worker, driven exclusively through the v3 API.

Success is never Devin's claim: a run is `devin:succeeded` only when a verified
PR exists, required structured output validates, and the allowlisted CI checks
(`unit-tests*`, `pre-commit`) are green on GitHub.

## Repository layout

```
action.yml                 Docker container action (commands: dispatch, reconcile, simulate)
Dockerfile                 python:3.12-slim, no credentials baked in
src/
  main.py                  entrypoint: command routing, step summary, report.json
  dispatch.py              label-gated, deduplicated session creation
  reconcile.py             session/PR/CI reconciliation + one bounded CI repair message
  report.py                generated dashboard issue body (honest denominators)
  policy.py                CI verdict (prefix allowlist), state mapping, success formula
  prompts.py               fixed instruction template, delimited untrusted issue text, redaction
  state.py                 versioned hidden marker = durable run state
  github_client.py         thin GitHub REST wrapper
  devin_client.py          thin Devin v3 wrapper (sessions, messages, playbooks, knowledge)
  fakes.py                 in-memory fakes shared by tests and simulate mode
schemas/remediation-result.json   structured-output contract Devin must fill
resources/                 source-controlled Playbook + Knowledge (synced via API)
scripts/sync_devin_resources.py   idempotent resource sync, prints IDs
fixtures/                  offline scenarios for simulate mode
tests/                     51 unit tests (dedup, marker, policy, prompts, dashboard)
```

## Prerequisites

- A public Superset fork you own (default branch `master`) with Issues and
  Actions enabled.
- A Devin organization with a `cog_` service-user API key and the GitHub
  integration granted access to the fork.
- The `gh` CLI authenticated as the fork owner (setup convenience only).

## Setup

1. **Sync Devin resources** (idempotent; prints the IDs used below):

   ```bash
   DEVIN_API_KEY=... DEVIN_ORG_ID=... python scripts/sync_devin_resources.py
   ```

2. **Create labels and the dashboard issue** in the fork: labels
   `devin:ready|running|pr-opened|succeeded|failed|blocked`, plus one issue
   titled "Devin Remediation Dashboard" (pin it).

3. **Configure the fork** (Settings → Secrets and variables → Actions):

   | Kind | Name | Value |
   |---|---|---|
   | Secret | `DEVIN_API_KEY` | service-user key |
   | Variable | `DEVIN_ORG_ID` | your org id |
   | Variable | `DEVIN_PLAYBOOK_ID` | from step 1 |
   | Variable | `DEVIN_KNOWLEDGE_ID` | from step 1 |
   | Variable | `DEVIN_DASHBOARD_ISSUE` | dashboard issue number |
   | Variable | `DEVIN_TARGET_REPO` | `<owner>/superset` |
   | Variable | `DEVIN_TARGET_BRANCH` | `master` |
   | Variable | `DEVIN_CI_ALLOWLIST` | `unit-tests,pre-commit` |

4. **Bootstrap the fork** with one commit on `master` containing:
   - `.github/workflows/devin-remediation.yml` (thin workflow calling this
     action, pinned to a commit SHA)
   - `.agents/skills/superset-quarantine-to-green/SKILL.md` (the repo-native
     procedure Devin invokes via `@skills:`)

   The workflow must be on the default branch before labeling — `issues` and
   `schedule` events only read workflows from there.

5. **Smoke test without spending a session**: Actions → Devin Remediation →
   Run workflow. The reconcile job proves Docker builds, both APIs
   authenticate, and the dashboard updates — with zero sessions created.

## Run the live workflow

Create a remediation issue with evidence, acceptance criteria, and forbidden
actions, then add the `devin:ready` label. Watch:

1. The dispatch job in Actions (session created, step summary, report artifact)
2. The issue's status comment and `devin:running` label
3. The session working live in the Devin app
4. The PR when it opens, CI running on it
5. The dashboard issue updating on each reconcile

Re-adding the label never creates a second session — dedup is enforced through
a durable marker before any session is created.

## Simulate locally (no credentials, no spend)

```bash
docker build -t devin-controller .
docker run --rm -e DEVIN_CI_ALLOWLIST="unit-tests,pre-commit" \
  devin-controller simulate "" fixtures/session-finished.json
docker run --rm -e DEVIN_CI_ALLOWLIST="unit-tests,pre-commit" \
  devin-controller simulate "" fixtures/ci-red.json
```

Fixtures cover: finished-and-green, CI-red (shows the single repair message),
still-running, and session-error. Tests: `pip install -r requirements.txt
pytest && pytest`.

## Observability

Four drill-down levels, all in GitHub:

1. **Dashboard issue** — generated metrics table and per-run table; every
   ratio shows numerator and denominator ("1 of 2", never bare percentages).
   ACU numbers come from the Devin API (`acus_consumed`), not estimates.
2. **Per-issue status comment** — status, session link, PR, ACU, validation
   checks, current action; controller-owned, edited in place. A hidden
   versioned JSON marker in the same comment is the durable state.
3. **State labels** — `devin:*` gives the issue list an at-a-glance view.
4. **Actions** — step summaries per run and a `report.json` artifact for
   machine-readable inspection.

## Security controls

- The Devin API key exists only as an Actions secret; prompts, logs, summaries,
  and report.json are redacted before writing.
- Issue text enters the session prompt only inside delimiters, truncated, and
  explicitly marked as data; controller rules are stated after the block and
  take precedence.
- Dispatch validates the event repository against the configured fork.
- Least-privilege workflow token (`issues: write`, everything else read).
- No `pull_request_target`, no auto-merge, one CI repair message per session,
  per-session ACU cap.

## Known limitations

- State lives in GitHub objects; a race between session creation and comment
  write can, in the worst case, orphan one session (documented residual risk —
  acceptable at this scale; production would use transactional storage).
- The 5-minute reconcile schedule is best-effort on GitHub's side; the manual
  trigger covers demos.
- One target repository and one active skill per session by design.

## Production extension

At customer scale the same lifecycle moves onto a persistent control plane
(FastAPI + Postgres + webhooks), adds scanners as finding sources
(dependencies, security, flaky-test detection from CI retries), routes
`devin_mode` by measured task class, and pulls runtime evidence through MCP
(Sentry, Datadog). The GitHub-native UX remains as the per-repo surface.
