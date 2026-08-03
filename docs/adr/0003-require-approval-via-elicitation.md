# ADR 0003: `require_approval` uses the real `elicitation/create` primitive

## Status

Accepted

## Context

Phase 2's policy gate (`docs/ROADMAP.md`) specifies three actions for a
`tools/call` rule: `allow`, `deny`, `require_approval`. `allow`/`deny` are
synchronous, self-contained decisions and shipped first. `require_approval`
is different in kind: it needs to pause a tool call, ask a human (via the
Host) whether to proceed, and act on their answer — mid-flight
human-in-the-loop, which MCP's base message flow (fire a request, get a
response) doesn't provide by itself.

Two options existed:
1. Invent an out-of-band mechanism — e.g. verimcp blocks on its own
   stdin/stderr for a local yes/no, bypassing the Host entirely.
2. Use a real MCP protocol primitive, if one exists in the spec version
   this project already targets.

Checked against the actual spec (not assumed): devmcp declares
`protocolVersion: "2025-06-18"` in every `initialize` response
(`devmcp/src/devmcp/capabilities.py`). That exact spec version defines
**elicitation** — a client capability letting a server ask the Host's user
for structured input mid-operation, via `elicitation/create`. A Host
declares support with `capabilities: {"elicitation": {}}` during
`initialize`; the server sends
`{"id": X, "method": "elicitation/create", "params": {"message", "requestedSchema"}}`;
the Host replies
`{"id": X, "result": {"action": "accept" | "decline" | "cancel", "content"?}}`.
`requestedSchema` is restricted to flat, primitive-only objects.

## Decision

Use `elicitation/create`, not an invented mechanism. This is a real,
versioned, already-adopted part of the exact protocol this project targets
— inventing a parallel approval channel would mean maintaining bespoke UX
no Host has any reason to support, when a standard one already exists.

**Schema kept intentionally empty.** verimcp's `requestedSchema` is
`{"type": "object", "properties": {}}` — no fields requested. The
`accept`/`decline`/`cancel` action returned by the Host's reply *is* the
yes/no signal; asking for an additional "approved: true/false" field inside
`content` would be redundant with what the action already encodes.

**Fail closed, uniformly, on every non-approval outcome:**
- The Host never declared the `elicitation` capability during `initialize`
  (checked once, cached — `Proxy._host_supports_elicitation`).
- The Host replies `decline` or `cancel`.
- The Host never replies within `--approval-timeout` (default 60s).

In every case, the original `tools/call` is denied, never forwarded to the
backend. No fallback-to-allow exists for any of these. Rationale: a
`require_approval` rule that silently degrades to "allow" under any of
these conditions is worse than not having the rule at all — it creates the
appearance of a gate while providing none, with no operator-visible signal
that it happened. This mirrors `SamplingRateLimitGate`'s existing
behavior (deny past the threshold, no escape hatch) and ADR 0001's general
position against inventing false trust.

**Id namespacing.** verimcp had never originated its own JSON-RPC request
before this — every prior message either passed through unchanged or was a
denial addressed to an id someone else picked. `elicitation/create` needs
a fresh id verimcp picks itself, sent to the Host over the same stdout
stream carrying relayed backend-originated requests (e.g. `roots/list`).
Those ids are not coordinated with verimcp at all — devmcp's own
`Connection` (and, by the same pattern, most real backends) uses one
shared integer counter for every self-originated request, so its very
first outbound message (`roots/list`, fired right after
`notifications/initialized`) is almost always id `1`. A plain integer
counter in verimcp would collide with exactly that. Fix: verimcp's
self-originated ids use a reserved string prefix
(`verimcp-elicit-<n>`) no real backend generates. Incoming replies are
recognized by that shape, not by dict membership alone, and always
swallowed (never forwarded to the backend) even if a Future is no longer
parked for them — closing a real race where a late reply arriving after a
timeout had already denied the call would otherwise leak through to the
backend as an ordinary, unrelated message.

**Dispatched as a background task, not awaited inline.** Earlier this
session, `devmcp/server.py`'s single dispatch loop was found to deadlock
on any tool that made a nested backend-originated request inline: the loop
was the same coroutine frame blocked awaiting its own outgoing request's
reply, so it could never read that reply in. The identical shape applies
here — `_forward_host_to_backend`'s read loop must stay free to read the
Host's elicitation reply, so a `require_approval`-matched call is handled
by `asyncio.create_task(self._handle_approval(...))`, tracked in
`self._background_tasks` (same pattern already established in
`devmcp/server.py`), while the main loop immediately continues reading.

## Consequences

- `RequestGate.check()`'s interface stays synchronous. Making it `async`
  would not have avoided the deadlock above (the same coroutine-frame
  problem exists regardless of whether the interface method is async) and
  would force every other gate, including `SamplingRateLimitGate`, to
  carry an `async` signature it has no use for. `require_approval` is
  therefore intercepted by `Proxy` directly, via
  `ToolCallPolicyGate.action_for()`, before `check()` is ever called for
  it — `check()` treats `require_approval` as a pass-through no-op.
- Non-goal: MCP's generic `notifications/cancelled` primitive is not
  wired into this flow. Elicitation's own three-action reply
  (`accept`/`decline`/`cancel`) already covers cancellation for this
  specific request; a Host closing the elicitation dialog is expected to
  reply `cancel`, not send a separate cancellation notification.
- `Proxy.run()` cancels any still-pending `_background_tasks` after its
  main `asyncio.gather()` returns, so a Host disconnecting mid-approval
  doesn't leave a task awaiting a Future that can never resolve.
