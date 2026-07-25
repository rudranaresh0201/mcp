# verimcp

**A verification proxy for the Model Context Protocol — because `isError: false` only means a tool didn't crash, not that it told the truth.**

MCP's spec has no notion of whether a tool's *claimed* effect actually happened. A `write_file` call can report success while writing nothing, or writing something different from what it claims — and nothing in the protocol catches that. `verimcp` sits between an MCP Host and a real backend MCP server, forwards everything transparently, and independently re-checks ground truth for the calls it has a verifier for before letting the response through. If a claim doesn't match reality, the response gets rewritten with a real diagnostic before the Host ever sees it.

This repo also contains **`devmcp`** — a from-scratch MCP server, built specifically to give `verimcp` something real to verify: git and CI tooling operating on an actual local repository, deliberately implementing the *full* MCP protocol surface (tools, resources with push subscriptions, prompts, roots negotiation, sampling) rather than just tools, which is where most reference/tutorial servers stop.

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
| M4 | verimcp's generalized verifier engine: `GitCommitVerifier`, `GitBranchVerifier`, `ResourceReadVerifier`, `CIRunVerifier`, a sampling rate-limit gate | 🔜 next |
| M5 | verimcp's own served capability — an audit log of every verification it's run (`verimcp://audit`), YAML config, working CLI | planned |
| M6 | Packaging polish, full ADR set, end-to-end demo script | planned |

`devmcp` is fully built and independently working today: real `initialize` handshake, four tools, resources with live subscriptions, two prompts, roots negotiation, and sampling — all proven end-to-end over real stdio, not mocked. `verimcp`'s verification engine currently covers one case (`write_file`, filesystem ground-truth check); wiring it up with git/CI-specific verifiers in front of `devmcp` is the next milestone.

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
