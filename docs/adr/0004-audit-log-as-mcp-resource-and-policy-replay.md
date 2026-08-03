# ADR 0004: Audit log as an MCP resource, replay scoped to policy decisions

## Status

Accepted

## Context

Phase 3 (`docs/ROADMAP.md`) asks for every tool call verimcp handles to be
recorded (arguments, timestamp, verification result, approval status),
served back as a `verimcp://audit` resource, plus session replay described as
"not a separate system -- just feeding that JSONL back through the same
proxy message flow."

Two questions had to be answered against the real spec and the real shape of
verimcp's verifiers, not assumed:

1. verimcp is a *proxy*, not the MCP server the Host talks to conceptually --
   how does a resource it invents itself actually reach the Host?
2. What does "feeding JSONL back through the proxy message flow" honestly
   mean, given some of verimcp's own verifiers check live, mutable state?

## Decision: capability patching

Confirmed against `modelcontextprotocol.io/specification/2025-06-18/server/resources`:
a resource is only discoverable if the `initialize` response declares
`capabilities.resources` at all, and `resources/subscribe` only if that
capability includes `subscribe: true`. Not every backend declares this --
devmcp does (`{"subscribe": true}`, `devmcp/src/devmcp/capabilities.py`), but
nothing guarantees a third-party backend does, and Phase 1's whole premise is
that verimcp works in front of *any* backend.

Since verimcp always has an audit resource to offer once `--audit-log` is
set, `Proxy` patches the `initialize` response in transit
(`_forward_backend_to_host`, tracked via `_pending_initialize` the same way
`_pending_roots` already tracks `roots/list`): `setdefault` in
`capabilities.resources.subscribe = true`, merging with whatever the backend
already declared rather than overriding it. `resources/list` responses get
the same treatment (`_pending_resource_list` / `_inject_audit_resources`) --
and if the backend's `resources/list` came back as an *error* (plausible for
a backend with no resources support at all), verimcp synthesizes a bare
success containing just its own audit entries instead of forwarding the
error, since the capability patch already promised the Host that
`resources/list` works.

`resources/read`, `resources/subscribe`, and `resources/unsubscribe` for a
`verimcp://` uri are answered locally in `_forward_host_to_backend` and never
forwarded to the backend at all -- the same "handled here, not the backend's
business" shape already established for elicitation replies (ADR 0003) and
gate denials.

**URI namespace:** `verimcp://audit` (every entry ever written to the log
file, across all sessions), `verimcp://audit/current` (just this run's
session -- listed, since the Host has no way to know the session id ahead of
time), and `verimcp://audit/<session_id>` (readable but unlisted, the same
prefix-dispatch pattern devmcp's `repo_file.py` uses for `repo://file/*`) for
pulling an older session out of a log file that spans many proxy runs.

## Decision: replay is scoped to gate/policy decisions, not full verifier re-runs

`ToolCallPolicyGate.action_for(tool_name, arguments)` is a pure function --
no I/O, no live state -- so replaying it against a new `--policy-config` for
a recorded call is sound regardless of how much time has passed since the
session was recorded. `verimcp replay <audit-log> --policy-config <file>`
(`src/verimcp/replay.py`) does exactly this: re-run every recorded
`tools/call` entry's `(target, arguments)` through the new gate, diff the
verdict against what `gate.action` was recorded as, report only the changes.

Verifiers are a different kind of check by construction (see
`verifiers/base.py`'s own docstring: "recompute ground truth"). `Filesystem`-
and `GitServerCommitVerifier` check the *current* disk/repo state, not a
snapshot from when the original response arrived. Replaying "was this
`write_file` response valid" against today's filesystem would silently check
the wrong thing the moment that file has been touched since -- a full replay
feature would either need to snapshot the filesystem itself at record time
(a substantially bigger feature, out of scope here) or produce answers that
look authoritative but sometimes aren't, which is exactly the kind of
false-confidence trust this whole project (ADR 0001) exists to avoid
building.

So v1 replay does not re-run verifiers at all. This is a stated scope
boundary, not a TODO: gate/policy backtesting is a real, sound, useful
feature on its own (e.g. "would last week's traffic have tripped this new
`deny` rule I'm about to ship?"), and it's the only part of "replay the
message flow" that's honest to build without a ground-truth-snapshotting
system behind it.

## Decision: synchronous file I/O in `AuditStore.record()`

ADR 0003 established a background-task pattern (`asyncio.create_task`,
tracked in `self._background_tasks`) specifically to keep
`_forward_host_to_backend`'s read loop free while a coroutine awaits its own
nested request's reply -- the deadlock class that bit `devmcp/server.py`'s
`tools/call` dispatch. `AuditStore.record()`'s `Path.open("a")` append is a
plain blocking local write with no reply being awaited at all, so that
deadlock class doesn't apply here; it's the same reasoning that already
justifies `jsonrpc.write_message_sync`'s synchronous stdout writes elsewhere
in `Proxy`. Introducing `asyncio.to_thread` or a queue-based writer for this
would add real complexity (out-of-order writes vs. the in-order `seq` counter
`AuditStore` relies on) to guard against a failure mode that isn't present.

## Consequences

- `--audit-log` is fully opt-in: every hook in `Proxy` is gated on
  `self._audit is not None`, so omitting the flag reproduces today's
  behavior exactly (verified by the existing test suite passing unchanged).
- The require_approval *accept* path needed a small side-table
  (`Proxy._pending_approval_meta`, keyed by request id) rather than stashing
  gate/approval metadata directly on the pending request dict -- that dict is
  the literal object forwarded to the backend over the wire
  (`jsonrpc.write_message(backend.stdin, request)`), so mutating it would
  have leaked non-protocol keys into a real JSON-RPC message.
- Full verifier replay (rebuilding ground truth as of the recording, not
  today) remains a real future extension, not ruled out -- just not this
  phase's problem, and explicitly not silently faked as if it were solved.
