# ADR 0009: Docker domain -- ADR 0002's original next domain, now built

## Status

Accepted

## Context

[ADR 0002](0002-devmcp-broad-verifiable-server.md) named Docker as the
literal next domain after git/CI/filesystem. [ADR 0007](0007-sqlite-as-next-domain.md)
built SQLite first instead, purely because Docker Desktop wasn't installed
on this machine at the time. Docker Desktop and WSL2 are now installed and
the engine is confirmed running (`docker info` succeeds) -- ADR 0007's
ordering exception is resolved, and this ADR builds what ADR 0002 always
intended, unchanged in scope.

## Decision

Two devmcp tools, one verimcp verifier, the same pairing rule every prior
domain has followed:

- `docker_build_image` (`devmcp/src/devmcp/tools/docker_build_image.py`) --
  runs a real `docker build -f <dockerfile> -t <tag> <context>`
  (`devmcp/src/devmcp/docker_ops.py`), `dockerfile_path`/`context_path`
  resolved through the same `git_ops.resolve_safe_path` boundary check
  every other path-taking tool already reuses. Claims `{tag, dockerfile_path,
  context_path}`.
- `docker_run_container` -- runs `docker run -d <image> <command>` (the
  `-d` flag is what makes docker print the container id immediately,
  instead of blocking on the command's own output), then `docker wait
  <container_id>` for the real exit code -- no `--cidfile` temp file
  needed. Claims `{container_id, exit_code}`. `isError` mirrors the
  container's own exit code (nonzero = true), the same convention
  `run_ci_pipeline` already uses for a failed step -- a real failure the
  claim can still be independently confirmed *true* about, not "verimcp
  couldn't check this."
- `DockerVerifier` (`src/verimcp/verifiers/docker.py`) -- independently
  calls `docker inspect` on the claimed tag or container id. For a build
  claim: does the image exist at all. For a run claim: does the container
  exist, and does its real `.State.ExitCode` match what was claimed --
  same "re-derive the specific fact, don't just check existence"
  discipline `FilesystemVerifier`'s content-hash-compare already
  established, not just `GitCommitVerifier`'s existence-only check.

Both tools run through `asyncio.to_thread`, matching `run_ci_pipeline.py`'s
existing rationale: a real build or a real container run can take long
enough to block devmcp's single dispatch loop, unlike git/sqlite calls.

Both tools also declare a real `output_schema` (ADR 0008), so
`SchemaConformanceVerifier` covers this domain immediately too -- proof the
generic layer generalizes to a brand-new domain with zero extra work.

## A real environment finding, not a code bug

`docker run <image>` on an image not already in the local store falls back
to a registry pull -- which hit a genuine, reproducible TLS negotiation
error against Docker Hub on this machine (`tls: protocol version not
supported`), while `docker build`'s buildx-based pull of the same base
image succeeds. Ruled out as project-code-caused by reproducing it with the
bare `docker` CLI directly, outside any of this project's code. Every test
here builds a local image first, then runs *that* local tag -- `docker run`
on an already-local tag needs no registry access at all, sidestepping the
issue entirely rather than working around it in `docker_ops.py`, since the
underlying cause is this machine's network/TLS stack, not something devmcp
should paper over. Worth knowing if a future run hits the same error on a
bare image reference.

## Consequences

- `tests/fixtures/lying_server.py`/adversarial corpus and the MCP Inspector
  smoke test remain deliberately unextended for this domain too, same
  stated-not-hidden deferral pattern ADR 0007 and ADR 0008 both used.
- `docs/ROADMAP.md` Phase 10 covers this; Postgres and Kubernetes remain
  queued per ADR 0002, now genuinely unblocked by real installed
  infrastructure (Docker/WSL2) rather than just documented intent.
