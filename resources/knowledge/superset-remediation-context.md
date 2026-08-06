# Superset Remediation Boundaries

Facts for remediation sessions working on the Superset fork.

- The only writable repository is `sb1992/superset`. Its default branch is
  `master`, and every pull request targets it.
- `apache/superset` is the read-only upstream. Never push to it, open PRs
  against it, or reference credentials for it.
- A remediation counts as successful only when a pull request exists and the
  applicable GitHub checks are green. The applicable checks are the Python unit
  test jobs (check names starting with `unit-tests`) and `pre-commit`.
- Other workflows in the fork may fail or be skipped for infrastructure
  reasons unrelated to your change (missing upstream credentials, publishing
  jobs). Do not chase them; note them as unrelated if asked.
- The repository requires pre-commit to pass: stage your changes with
  `git add`, then run `pre-commit run` before opening the PR.
- Tests and stored datetimes in this codebase use timezone-naive UTC
  semantics. When replacing deprecated `datetime.utcnow()`, preserve naive UTC
  behavior — `datetime.now(timezone.utc).replace(tzinfo=None)` keeps values
  naive; a timezone-aware replacement silently changes comparisons and
  serialization.
