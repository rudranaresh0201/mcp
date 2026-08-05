# devmcp

<!-- mcp-name: io.github.rudranaresh0201/devmcp -->

A from-scratch MCP server exposing git and CI tooling over the **full**
protocol surface — tools, resources with live push subscriptions, prompts,
roots negotiation, and sampling — not just the tools primitive most example
servers stop at.

Standalone and independently installable; has no import dependency on
`verimcp` (the sibling package in this repo that proxies and verifies
backends like this one — see the [root README](../README.md) for how they
fit together, though devmcp works fine as a plain MCP server on its own).

## What it does

Run `devmcp --repo-path ./some-repo` and it:

- answers `tools/call` for `write_file`, `git_commit`, `git_branch`,
  `run_ci_pipeline` — each produces a real, independently-checkable side
  effect on disk or in git, not just a claimed result
- serves `resources/read` for `repo://status`, `repo://log`,
  `repo://file/{path}`, `ci://last-run`, and pushes
  `notifications/resources/updated` unprompted when a subscribed resource
  changes
- serves `prompts/get` for `commit-message` and `pr-description` templates
- on startup, asks the *Client* `roots/list` and relocates its own working
  directory to whatever the Client declares, rather than trusting its own
  `--repo-path` flag blindly
- for `summarize_diff`, asks the *Client's own model* to do the
  summarization via `sampling/createMessage` — devmcp never needs its own
  LLM API key, it borrows whatever model the Host already has

## Getting started

```bash
pip install devmcp-server   # PyPI distribution name -- CLI/import are still plain `devmcp`
devmcp --repo-path ./some-repo
```

Contributing to this repo instead:

```bash
pip install -e ".[dev]"
pytest tests
ruff check src tests
```

## License

MIT — see [LICENSE](LICENSE).
