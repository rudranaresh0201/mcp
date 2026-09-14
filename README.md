# A trusted agent stack for MCP

<!-- mcp-name: io.github.rudranaresh0201/verimcp -->

[![PyPI - verimcp](https://img.shields.io/pypi/v/verimcp?label=verimcp)](https://pypi.org/project/verimcp/)
[![PyPI - devmcp-server](https://img.shields.io/pypi/v/devmcp-server?label=devmcp-server)](https://pypi.org/project/devmcp-server/)
[![CI](https://github.com/rudranaresh0201/mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/rudranaresh0201/mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Listed on the [official MCP Registry](https://registry.modelcontextprotocol.io/?search=rudranaresh0201) as `io.github.rudranaresh0201/verimcp` and `io.github.rudranaresh0201/devmcp`.

**[Watch the console →](https://verimcp-console.onrender.com)** — a recorded session replaying in the browser: three verified calls, five caught lies, one honest backend error.

## Problem

MCP tool calls are trusted by default, in two different ways that both bite in production agent workflows:

1. **Claims aren't checked.** When a server says a commit succeeded, a file was written, or a CI check passed, the Host has no way to know if that's true — `isError: false` only means the tool didn't crash, not that it did what it claimed. An agent wired up to git, CI, or infrastructure through MCP is one confidently-wrong tool response away from acting on a lie.
2. **Retries aren't safe.** When a Host resends a `tools/call` after an ambiguous response (a timeout, a dropped connection), nothing in MCP stops the backend from just running it again — a second commit, a second CI run, a second charge. The protocol has no concept of "didn't I already do this?"

## Solution

**`verimcp`** is a transparent proxy that sits between an MCP Host and a backend server and forwards every message unchanged, except for two independent checks around each call:

- **Before forwarding** *(pre-check)* — if this exact call was already sent once and independently verified true this session, answer the retry from that confirmed result instead of hitting the backend again. `--idempotent-replay`, see [Proof](#proof-not-just-claims) below.
- **After the backend responds** *(post-check)* — for calls it knows how to check, independently re-derive the real outcome (re-read the file, re-check the git log, re-run the safe parts of a CI step) and compare that against what the backend claimed. A lie gets rewritten into a real error before the Host ever sees it as a success.

**`devmcp`** is the backend it's proven against — a real git/CI MCP server, deliberately built with the full protocol surface (tools, resources, prompts, roots, sampling), because a proxy is only as convincing as what it's shown catching.

```
Host  <--stdio-->  verimcp (pre-check: dedupe retries + post-check: verify claims)  <--stdio-->  devmcp (git/CI server)
```

Both are independently installable and have no import dependency on each other — `verimcp` works in front of *any* MCP backend (proven against the official `mcp-server-git` too, not just devmcp), and `devmcp` works with any Host directly, unproxied.

## Proof, not just claims

Two runnable benchmarks, real subprocess pipes, no mocks — the numbers below are what they actually print, gaps included:

```bash
pip install verimcp devmcp-server
python scripts/benchmark_claim_acceptance.py    # does verimcp catch a lying backend?
python scripts/benchmark_retry_duplication.py   # does --idempotent-replay stop a retry from duplicating a side effect?
```

**Claim-acceptance** — a real MCP Host with no verification layer accepts 100% of fabricated results by construction; that's not a benchmark artifact, it's the actual gap in the protocol:

```
RAW claim-acceptance rate:     14/14  (100%)
verimcp claim-catch rate:      11/14  (79%)
```

The 79%, not 100%, is deliberate and stated in the script's own output: 3 of the 14 scenarios exploit documented, principled gaps in specific verifiers (e.g. a hash-exists check can't tell *this* call created the hash vs an old one) — named plainly rather than hidden, because a tool claiming a perfect score on its own benchmark is the real red flag.

**Retry-duplication** — same idea applied to `--idempotent-replay`, against 5 realistic non-idempotent side effects (a counter bump, a notification send, an invoice increment, an audit entry, a two-step pipeline):

```
RAW duplicate-action rate:      5/5  (100%)
verimcp duplicate-action rate:  0/5  (0%)
```

`scripts/demo.py` is the smaller, narrative version of the same proof — one fabricated commit hash caught, one real write confirmed and passed through unchanged. `tests/test_adversarial_corpus.py` and `tests/test_proxy_integration.py` run the same claims through the real proxy pipe in CI on every commit, not just on demand.

## Technical summary

- **Correctness verifiers** — one per tool/resource with an independently-checkable postcondition: `write_file` (disk hash-compare), `git_commit`/`git_branch` (re-derived from real git state), `run_ci_pipeline` (self-consistency + re-execution of steps marked safe to re-run), plus resource reads (`repo://status`, `repo://log`, `repo://file/{path}`). New verifiers are a plugin system (`importlib.metadata.entry_points`, the same mechanism pytest/Black use) — see [`docs/writing-a-verifier.md`](docs/writing-a-verifier.md).
- **GitHub** — `create_pull_request`, `issue_write` (create) and `create_branch` from GitHub's official MCP server are checked against GitHub's own REST API: the PR/issue/branch must exist *and* carry the title that was requested, which also catches a server handing back a real, older PR as if it were new. A 404 only counts once the repo is confirmed visible, so a private repo without a token is left unchecked rather than falsely flagged.
- **Receipts** (`--receipts`, on by default under `wrap`) — every check records what it actually observed (a PR's real title and link, a commit's real message and author, a file's real hash), where it looked and when, for claims that held as well as ones that didn't. Written to the audit log and console always, appended to the tool reply with the flag.
- **Remote servers** (`--backend-url`) — fronts a server over Streamable HTTP (MCP spec 2025-06-18: JSON and SSE replies, session ids, protocol-version header), with `--header 'Authorization: Bearer ${TOKEN}'` / `--header-from-env` so a secret never has to live in a config file or a command line. Checked against a hosted server (DeepWiki) as well as the test suite.
- **Roots from the Host** — verimcp asks a roots-capable Host (Claude Code, Cursor) for its workspace itself, so filesystem and git verifiers work in front of servers that never ask, like `mcp-server-git`.
- **Verify-before-retry** (`--idempotent-replay`) — a retried `tools/call` is only ever answered from cache if the *previous* identical call was independently verified true by the checks above; a key match with no prior verified success is a cache miss, not a false dedupe. Adapted from [arXiv:2608.02645](https://arxiv.org/abs/2608.02645), moved to the proxy layer so it works for any Host/backend pair verimcp fronts, not just one agent framework's own retry wrapper.
- **Policy gates** — allow/deny/require-approval rules evaluated *before* a call reaches the backend, for the calls that have no objective truth to check (sampling rate limits, arbitrary tool-name/argument policy via YAML, human-in-the-loop approval over real MCP `elicitation/create`).
- **Audit + replay** — every call verimcp handles is logged and served back as a real `verimcp://audit` MCP resource; `verimcp replay` re-runs recorded traffic against a new policy to backtest "would this have changed anything."
- **Observability** — every call gets an OpenTelemetry span/metric using the GenAI semantic conventions, exportable to any OTLP collector.
- **Proven against real clients, not just our own tests** — the official MCP Inspector and VS Code's native MCP support (Copilot Chat) each caught a real bug our own test suite never triggered, now fixed with regression tests.

Deeper design reasoning (why prompts/roots get no verifier on principle, why policy is a separate concept from verification, etc.) lives in [`docs/adr/`](docs/adr/) for anyone who wants to go that deep — the summary above is everything needed to use or evaluate the project.

## Getting started

**Fastest: protect the MCP servers you already have** (0.4.0, not yet on PyPI — install from GitHub until it is):

```bash
pip install "verimcp[dashboard] @ git+https://github.com/rudranaresh0201/mcp"
verimcp wrap                 # puts verimcp in front of every server in Claude Code, Claude Desktop and Cursor
verimcp-dashboard --audit-log ~/.verimcp/audit.jsonl   # watch every verdict live at http://127.0.0.1:8787
```

Restart your MCP app and keep using it in plain English. `wrap` backs up each config file first, skips servers it can't safely front (ones that sign in through the app's own OAuth flow, legacy SSE), never puts a header token into command-line arguments, and `verimcp unwrap` restores the originals exactly. `--dry-run` shows the plan without writing anything.

What it looks like, from a real run through the official `mcp-server-git` and a deliberately lying file server:

```
[verimcp receipt] CONTRADICTED · FilesystemVerifier
todo.txt does not exist on disk

[verimcp receipt] VERIFIED · GitServerCommitVerifier
commit b6fcbbd50b11 exists: 'Add hello file' by Rudra
```

**As a user** — install straight from PyPI:

```bash
pip install verimcp devmcp-server
verimcp -- devmcp --repo-path ./some-repo
```

> Note on names: the PyPI distribution is `devmcp-server` (`devmcp` was blocked by
> PyPI's typosquat-similarity check against an unrelated existing package), but the
> Python import and CLI command are both still plain `devmcp` — nothing above changes
> if you're reading devmcp's own source.

**Use it with Claude Desktop, Claude Code, or Cursor** — after `pip install`,
add `devmcp` as an MCP server the normal way, just point its `command` at
`verimcp` instead of `devmcp` directly:

```json
{
  "mcpServers": {
    "devmcp": {
      "command": "verimcp",
      "args": ["--root", "/path/to/your/repo", "--", "devmcp", "--repo-path", "/path/to/your/repo"]
    }
  }
}
```

- **Claude Desktop**: paste this into `claude_desktop_config.json` (macOS:
  `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows:
  `%APPDATA%\Claude\claude_desktop_config.json`).
- **Claude Code**: `claude mcp add devmcp -- verimcp --root /path/to/your/repo -- devmcp --repo-path /path/to/your/repo`
- **Cursor**: same JSON shape, in Cursor's MCP settings.

From then on your assistant sees `write_file`, `git_commit`, `git_branch`,
`run_ci_pipeline`, `sqlite_*`, and `docker_*` as normal tools — no prompting
change needed — except every claim those tools make gets independently
re-checked before the assistant is told it succeeded.

**In Docker** — no local Python/git needed:

```bash
docker build -t verimcp-devmcp .
docker run -i -v /path/to/your/repo:/repo verimcp-devmcp
```

**As a contributor** — editable installs from this repo:

```bash
pip install -e ".[dev]"
pip install -e "./devmcp[dev]"

pytest tests devmcp/tests   # 138 tests, real subprocess + real git repo, nothing mocked
ruff check src tests devmcp/src devmcp/tests

python scripts/inspector_smoke_test.py   # verify against the real MCP Inspector client (needs node/npx)
```

## Try the observability yourself

```bash
# spans/metrics print to stderr (verimcp's stdout is the live MCP protocol channel)
verimcp --otel-exporter console -- devmcp --repo-path ./some-repo

# or point a real collector, Jaeger, or Grafana Agent at it
pip install "verimcp[otel]"
verimcp --otel-exporter otlp --otel-endpoint localhost:4317 -- devmcp --repo-path ./some-repo
```

Omitting `--otel-exporter` entirely means zero telemetry overhead — the default, same as every other opt-in flag here.

## Watch it catch a lie

The console reads the audit log verimcp already writes and streams it to a browser — verdict, tool, arguments, and the evidence each verdict rests on.

**[verimcp-console.onrender.com](https://verimcp-console.onrender.com)** replays a recorded session. It is a recording, not a live agent, and the UI says so: the log it serves was produced by `scripts/demo_audit_log.py` driving the real proxy in front of the real `devmcp` server and the adversarial fixture, so every event on screen came from the code path a real run uses. A dashboard screenshot of invented events would be exactly the kind of unearned claim this project exists to catch. (Free instance — the first load after an idle spell takes ~30s to wake.)

Against your own session:

```bash
pip install "verimcp[dashboard]"
verimcp --audit-log ./audit.jsonl -- devmcp --repo-path ./some-repo   # terminal 1
verimcp-dashboard --audit-log ./audit.jsonl                           # terminal 2
```

The console never talks to the proxy directly. verimcp speaks MCP over stdio to exactly one Host, and a second reader on that pipe would corrupt the session, so the audit log is the supported out-of-band surface ([ADR 0004](docs/adr/0004-audit-log-as-mcp-resource-and-policy-replay.md)) — reading a file cannot perturb what it observes, which matters for a tool whose whole claim is that it does not interfere.

## Try verify-before-retry yourself

```bash
verimcp --idempotent-replay -- devmcp --repo-path ./some-repo
```

Send the same `tools/call` twice in a row (same tool, same arguments, new request id — exactly what a client resending after a timeout looks like on the wire). The first call runs for real; the second is answered from the confirmed result without touching the backend again. Omit the flag for the unchanged default: every retry is re-executed, same as before this existed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, how to add a verifier,
this project's ADR discipline, and commit/PR conventions. See
[CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT — see [LICENSE](LICENSE).
