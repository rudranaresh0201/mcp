# ADR 0008: A generic, zero-setup schema-conformance layer

## Status

Accepted

## Context

Every verifier before this one shares the same limitation, stated plainly
rather than hidden: it only checks a claim for the *specific tool name* it
was written for. Point verimcp at `mcp-server-git` (proven backend-
independent in [ADR](0001-verify-vs-dont-framework.md)/Phase 1) and its
`git_commit` calls get independently checked by `GitServerCommitVerifier` --
but any *other* tool that server exposes (`git_diff`, `git_log`, ...) passes
through completely unverified, because nobody wrote a check for it. Coverage
is per-tool-name, not per-server -- "verimcp works with other servers" was
never the same claim as "verimcp verifies everything on other servers," and
that distinction is worth stating here precisely because it's easy to blur.

The real, spec-level MCP `tools/outputSchema` field (2025-06-18
specification, "Tools") gives a way to shrink that gap without writing a
bespoke check per tool: "if an output schema is provided, servers MUST
provide structured results that conform to this schema." Any tool that
declares one is making a machine-checkable promise about its own response
shape -- checkable generically, by any proxy, with zero domain knowledge.

## Decision

Add `SchemaConformanceVerifier` (`src/verimcp/verifiers/schema_conformance.py`):
validates a tool's `structuredContent` against whatever `outputSchema` that
same tool declared in a real `tools/list` response this session. Unlike
every other verifier, it isn't stateless and isn't discovered via the
`verimcp.verifiers` entry-point group -- `Proxy` constructs it directly,
sharing a live `dict[str, dict]` (tool name -> schema) that gets populated
as real `tools/list` responses are observed passing through
(`proxy.py`'s `_pending_tools_list` tracking, same "method not in message"
discriminator discipline ADR 0006 already established for the id-collision
class this exact kind of pending-set tracking is prone to). Always
appended, never excludable via `--verifiers` -- it can't misfire against
the wrong backend's same-named tool the way a domain verifier could, since
it only ever checks a tool against the schema *that same tool* declared in
*this* session, not a hardcoded assumption about what that tool name means.

Also added `output_schema` as an optional field on devmcp's own `Tool` base
class, declared for the five tools with a fixed `structuredContent` shape
(`write_file` and `summarize_diff` correctly have none -- free text and no
fixed shape respectively) -- so this layer has real, live schemas to check
against a real backend, not just the synthetic ones the unit tests use.

## What this deliberately does not claim

This is a shallower check than every other verifier here, on purpose: it
can prove a response is honestly *shaped*, never that a specific *fact* is
true. `SchemaConformanceVerifier` passing tells you `commit_hash` really is
a string; it says nothing about whether that hash actually exists in the
repo (that's still `GitCommitVerifier`'s job, and both run independently --
proven together in
`tests/test_proxy_integration.py::test_schema_conformance_runs_over_real_pipe_using_devmcps_own_declared_schema`).
The value this buys is breadth those verifiers can't: it applies to *any*
tool, on *any* backend, that declares an `outputSchema`, with zero
verifier-writing required -- verimcp is never fully idle on an unfamiliar
tool again, even though "not fully idle" and "as thoroughly checked as
`git_commit`" remain two different claims.

## Consequences

- A tool that declares no `outputSchema` at all (most third-party servers,
  today) still gets nothing from this layer -- it only activates on what a
  backend chooses to declare. This is a real, current limit, not solved by
  this ADR: most MCP servers in the wild don't yet declare `outputSchema`.
- Extending `tests/fixtures/lying_server.py` with a schema-violation `--lie`
  scenario is a natural follow-up, deliberately deferred here (same pattern
  ADR 0007 already used) -- the unit tests in
  `tests/test_schema_conformance_verifier.py` already prove the catch the
  same way every other verifier's unit tests do (`test_git_commit_verifier.py`
  et al.), so this isn't an unproven gap, just an unextended one.
- `jsonschema` (JSON Schema 2020-12, matching the MCP base spec's default
  dialect) is now a required dependency, not optional -- this verifier is
  always on, unlike the OTel exporters.
