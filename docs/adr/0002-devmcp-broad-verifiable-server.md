# ADR 0002: devmcp's role — a broad, verifiable-by-design developer/infra MCP server

## Status

Accepted

## Context

Early framing treated devmcp as a small, intentionally-scoped reference
server whose only job was giving verimcp real ground truth to check —
a research companion, not a standalone product. That framing was tested
directly: if someone finds both repos on GitHub, would they choose devmcp
over an existing, mature git/filesystem MCP server? Feature-for-feature,
no — devmcp's git/CI tools don't do anything those don't already do, some
more maturely.

Two paths were considered:
1. Keep devmcp small and explicitly non-standalone (reference/research
   companion only).
2. Grow devmcp into a genuinely broad, multi-domain server, and find a
   differentiator that isn't raw tool count.

Path 2 was chosen. The differentiator isn't breadth alone — plenty of
broad dev-tool MCP servers exist. It's breadth **combined with** every
tool being designed against a stated, independently-checkable postcondition
(a "verifiable tool contract"), with verimcp as the reference engine that
enforces it. No existing broad multi-domain MCP server pairs breadth with
verification. That combination is the actual value proposition.

## Decision

devmcp grows into a multi-domain developer/infrastructure MCP server —
git and CI (existing), filesystem (existing), then further domains from
the original vision (Docker, databases, cloud, messaging, orchestration).
It is **no longer** framed as "intentionally staying small."

Two rules govern how it grows, both non-negotiable:

1. **Domain sequencing is cost-driven, not importance-driven.** Domains
   testable for free, locally, with no external account (Docker via Docker
   Desktop, Postgres via a local container) come first. Domains needing
   real paid/cloud infrastructure or credentials (AWS, Kubernetes, Slack,
   managed databases) are deferred until there's a concrete need funding
   that infrastructure — not because they're out of scope, but because
   untested, uncredentialed "support" for them would be vaporware.
2. **Every tool, in every domain, ships with (or is promptly followed by)
   a stated verifiable postcondition and a corresponding verimcp verifier.**
   Breadth without verification is just another dev-tool server; breadth
   *with* verification is the differentiator this ADR exists to protect.
   A tool that can't state a checkable postcondition (like sampling
   already demonstrates) still gets an explicit "no verifier, on
   principle" statement per [ADR 0001](0001-verify-vs-dont-framework.md)
   — it doesn't get skipped from this discipline, it gets classified by it.

## Inclusion boundary

"Broad" is not "everything." A domain is only ever considered for devmcp
core if it passes all three:

1. **Free and local to run and test** — a contributor runs the full test
   suite with zero signups, zero API keys, zero billing. A local
   daemon/container (Docker Desktop, a local Postgres container) qualifies;
   a hosted third-party service does not.
2. **Ground truth is independently checkable by us, locally** — verification
   can't depend on querying a third party's API as the source of truth.
   That would make verimcp's checks only as reliable and available as
   someone else's service, undermining the entire premise.
3. **Part of a developer's own local inner loop** — writing, testing,
   building, running code — not general business/orchestration tooling
   that happens to expose an API.

Applied to the original vision list:

| Domain | Status | Reason |
|---|---|---|
| Git, filesystem, CI | In (built) | Free, local, deterministic, inner-loop |
| Docker | In (next domain) | Local daemon, free, `docker inspect` gives real state |
| PostgreSQL | In, local container only | A local `postgres:latest` container passes all three; a managed cloud instance would not |
| Kubernetes | In, low priority | A local `kind`/`minikube` cluster passes all three; sequenced after Docker/Postgres for setup friction, not scope reasons |
| Slack | **Out, permanently** | Fails #1 (real workspace/API key) and #2 (ground truth lives in Slack's API) |
| AWS / cloud providers | **Out, permanently** | Fails #1 (billing account) and #2 (ground truth is a third party's API) |

Domains that fail this boundary aren't dropped from the project's ambition —
they move to Phase 7 (SDK for verifier authors) as documented, community-
authored examples ("here's how you'd write a verifier for something like
this"), not something devmcp itself builds or maintains.

## Consequences

- The devmcp README/positioning should describe it as a broad
  developer/infrastructure MCP server whose tools are individually
  verifiable — not as a small reference implementation.
- `docs/ROADMAP.md`'s Phase 1 note about Docker/Postgres/etc. being
  "documented examples, not built by us" is superseded — they become real
  devmcp domains, sequenced by the cost rule above, each paired with a
  real verifier in verimcp.
- The two-repo relationship stays coherent by construction: devmcp cannot
  grow a new domain without verimcp growing the matching verifier. Neither
  project can silently outpace the other.
- Near-term sequencing (cost-driven): Docker is the natural next domain
  after finishing the current git/CI verifier set — free, local, no
  account needed, and it's a genuinely different class of ground truth
  (container/image state) than anything git-based.
