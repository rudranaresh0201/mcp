# ADR 0007: SQLite, not Docker, as the next devmcp domain

## Status

Accepted

## Context

[ADR 0002](0002-devmcp-broad-verifiable-server.md) names Docker as the
literal next domain after git/CI/filesystem, sequenced by setup cost ahead
of Postgres and Kubernetes. That sequencing assumed Docker Desktop would be
available to test against. It isn't, on this machine -- `docker --version`
fails with command not found -- and installing Docker Desktop is a real
step (admin rights, WSL2 backend, likely a restart), not something to take
on just to keep the domain-expansion loop moving today.

ADR 0002's own inclusion boundary (a domain qualifies for devmcp core only
if it's free and local to test with zero signups, has ground truth
independently checkable by us locally, and is part of a developer's own
inner loop) doesn't actually name Docker as privileged over other domains
that pass the same test -- Docker was simply next by setup-cost ordering.
SQLite passes the identical three-part test with **zero** setup cost: it's
Python's standard library (`sqlite3`), needs no daemon or install, and "does
this table/row actually exist" is exactly the same class of independently-
checkable postcondition Docker's "does this container/image actually exist"
would have been.

## Decision

Build the SQLite domain now, ahead of Docker in ADR 0002's literal ordering,
purely because it's available today with no setup cost. Docker/Postgres/
Kubernetes remain in scope, unchanged, for whenever Docker Desktop is
actually installed -- this ADR reorders by availability, not by revising
ADR 0002's inclusion boundary or dropping anything from it.

Two new devmcp tools, one new verimcp verifier, same pairing rule ADR 0002
established ("devmcp cannot grow a new domain without verimcp growing the
matching verifier"):

- `sqlite_create_table` / `sqlite_insert_row` (`devmcp/src/devmcp/tools/`,
  backed by `devmcp/src/devmcp/sqlite_ops.py`) -- real `sqlite3` stdlib
  calls against a `.db` file under the negotiated repo root, no mocking,
  same principle `git_ops.py` already states for git.
- `SQLiteVerifier` (`src/verimcp/verifiers/sqlite.py`) -- reconnects to the
  same `.db` file with a **fresh** `sqlite3.connect`, independent of
  whatever connection the tool used, and re-queries `sqlite_master` (for
  `sqlite_create_table`) or the claimed row's exact values (for
  `sqlite_insert_row`). Same "don't trust the same actor's own report, only
  the Host would trust that verimcp doesn't have to" pattern as
  `FilesystemVerifier` (content hash) and `GitCommitVerifier`
  (`git cat-file -e`), just applied to relational state instead of a file or
  repo history.

## A new risk class this domain introduces: SQL identifier injection

Git and filesystem paths have one escape-boundary problem each (traversal /
absolute paths, already solved by `git_ops.resolve_safe_path`, extracted as
part of this change so `sqlite_ops.py` reuses it for `db_path` too). SQLite
adds a second, different one: table and column names can't be parameterized
in SQL the way values can (`sqlite3`'s `?` placeholders only bind values).
A `table` or `values` key argument gets string-interpolated into the
`CREATE TABLE`/`INSERT INTO` statement, so an unvalidated identifier is a
real injection point (e.g. `table="users; DROP TABLE x"`).

Both sides guard this independently, deliberately not trusting each other's
validation: `devmcp/src/devmcp/sqlite_ops.py`'s `_validate_identifier`
rejects anything not matching `^[A-Za-z_][A-Za-z0-9_]*$` before the tool
ever runs a statement, and `verimcp/verifiers/sqlite.py` re-validates the
same way before building its own ground-truth query -- an unvalidated
identifier reaching the verifier's own SQL would turn the ground-truth check
itself into a second injection point, which would defeat the entire
premise of verifying at all.

## Consequences

- `docs/ROADMAP.md` gets a Phase 9 entry for this; Docker/Postgres/
  Kubernetes stay queued exactly as ADR 0002 left them, unblocked by this.
- Adversarial corpus (`tests/fixtures/lying_server.py`,
  `tests/test_adversarial_corpus.py`, currently "6/6 real verifier catches")
  and the MCP Inspector smoke test are natural follow-ups to extend for this
  domain, deliberately deferred out of this pass rather than silently
  dropped.
- Once Docker Desktop is available, ADR 0002's original Docker sequencing
  resumes unchanged -- this ADR is an ordering exception, not a
  replacement.
