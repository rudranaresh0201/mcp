# A trusted agent stack for MCP

**Because `isError: false` only means a tool didn't crash, not that it told the truth — and an agent operating across git, CI, and infrastructure needs more than hope that its tools are honest.**

This repo is two packages that only make sense together:

- **`devmcp`** — a broad, verifiable-by-design MCP server. Every domain it covers (git and CI today, expanding per [ADR 0002](docs/adr/0002-devmcp-broad-verifiable-server.md)) is deliberately implemented across the *full* MCP protocol surface (tools, resources with push subscriptions, prompts, roots negotiation, sampling), and every tool ships with a stated, independently-checkable postcondition rather than an implicit "trust me."
- **`verimcp`** — the trust engine. It sits between an MCP Host and a backend server, forwards everything transparently, and independently re-derives ground truth for the calls it has a verifier for before letting the response through. Verification is the first trust primitive; policy enforcement, audit logging, and observability (see [ROADMAP](docs/ROADMAP.md)) are the same idea applied to allow/deny, provenance, and visibility.

Neither half is the point on its own. A broad tool server without verification is just another dev-tool MCP server; a verification engine with nothing real to verify is a proof of concept. Together, the pitch is: **an agent operating through this stack cannot get away with a false claim, an unapproved action, or an unlogged side effect.**

```
Host  <--stdio-->  verimcp (proxy + verification engine)  <--stdio-->  devmcp (git/CI server)
```

Both packages are independently installable and have **no import dependency on each other** — `devmcp` is a standalone MCP server usable by any Host, and `verimcp` is a general-purpose proxy usable in front of any backend server, not just `devmcp`. They're proven together only via the end-to-end tests.

## Status

| Milestone | What it is | Status |
|---|---|---|
| M0 | devmcp scaffolding, CI, verify-vs-don't design ADR | ✅ done |
| M1 | devmcp core: transport, `initialize`, git tools (`write_file`, `git_commit`, `git_branch`, `run_ci_pipeline`) | ✅ done |
| M2 | devmcp resources (`repo://status`, `repo://log`, `repo://file/{path}`, `ci://last-run`), push subscriptions, prompts | ✅ done |
| M3 | devmcp roots negotiation + sampling (`summarize_diff`) — the two directions where devmcp asks the *Client* something | ✅ done |
| M4 | verimcp's generalized verifier engine: `GitCommitVerifier`, `GitBranchVerifier`, `CIRunVerifier`, `ResourceReadVerifier` done; a sampling rate-limit gate still open | 🔜 almost done |
| M5 | verimcp's own served capability — an audit log of every verification it's run (`verimcp://audit`), YAML config, working CLI | planned |
| M6 | Packaging polish, full ADR set, end-to-end demo script | planned |

`devmcp` is fully built and independently working today: real `initialize` handshake, five tools, resources with live subscriptions, two prompts, roots negotiation, and sampling — all proven end-to-end over real stdio, not mocked. `verimcp` now has a verifier for every tool call and resource read with an independently-checkable postcondition — `write_file` (filesystem hash-compare), `git_commit` (commit-object existence), `git_branch` (ref existence + target), `run_ci_pipeline` (per-step self-consistency plus re-execution of steps marked safe to re-run), and `repo://status`/`repo://log`/`repo://file/{path}` (re-derive the same git/filesystem state and compare). What's left for M4 is a policy-not-correctness gate on the one remaining primitive, sampling.

## Why this split

Every MCP primitive gets one of three treatments, decided in [`docs/adr/0001-verify-vs-dont-framework.md`](docs/adr/0001-verify-vs-dont-framework.md):

- **Correctness verifiers** (tools, resources) — re-derive ground truth independently and compare it to the claim.
- **Policy gates** (sampling) — no objective truth to check, but a real cost/abuse surface worth rate-limiting.
- **No verifier, on principle** (prompts, roots) — nothing independent to check them against; treating them as "unverified" is a decision, not an oversight.

## What devmcp actually does

Run `devmcp --repo-path ./some-repo` and it:
- answers `tools/call` for `write_file`, `git_commit`, `git_branch`, `run_ci_pipeline` (each producing a real, independently-checkable side effect on disk / in git)
- serves `resources/read` for live repo state, and pushes `notifications/resources/updated` unprompted when a subscribed resource changes
- serves `prompts/get` for `commit-message` and `pr-description` templates
- on startup, asks the *Client* `roots/list` and relocates its own working directory to whatever the Client declares, rather than trusting its own CLI flag blindly
- for `summarize_diff`, asks the *Client's own model* to do the summarization via `sampling/createMessage`, since devmcp has no model of its own

## Getting started

```bash
pip install -e ".[dev]"
pip install -e "./devmcp[dev]"

pytest tests devmcp/tests   # 20+ tests, real subprocess + real git repo, nothing mocked
ruff check src tests devmcp/src devmcp/tests
```

## License

MIT — see [LICENSE](LICENSE).
