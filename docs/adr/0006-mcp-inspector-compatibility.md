# ADR 0006: MCP Inspector compatibility -- a real client, run for real, caught a real bug

## Status

Accepted

## Context

Phase 5 (`docs/ROADMAP.md`) asks for devmcp, then verimcp-fronting-devmcp,
to be run through **MCP Inspector** -- the official, free, open-source MCP
client -- specifically to catch spec-compliance edge cases this project's
own test suite would never generate, since every test here was written
against the same assumptions the code was.

That is exactly what happened. This ADR records both the tooling friction
(so the next run doesn't re-discover it) and the real bug Inspector's first
live run against verimcp caught -- fixed in `proxy.py`, with a permanent
regression test.

## Decision: pin Inspector to v1.0.1 with `--transport stdio`, not `@latest`

`@modelcontextprotocol/inspector@latest` (currently `2.0.0`) has a
reproducible CLI bug on this Windows setup: `--cli <target...> --method
tools/list` fails before any MCP connection is attempted, with
`NameError: name 'true' is not defined` from (apparently) an internal
environment probe, followed by `Connection timed out after 15000 ms`.
Confirmed **target-independent** -- the identical error occurs even with a
trivial `python --version` target, and with a completely different server
(`@modelcontextprotocol/server-everything`) -- ruling out devmcp/verimcp as
the cause. Several argument orderings were tried (flags before/after the
target, `--` separators, `--transport stdio` in different positions);
each produced a *different* failure (`No servers found in config file`,
`'--transport' is not recognized as...a command`), indicating v2's argument
parser itself mishandles a multi-word target command mixed with its own
flags in this environment, not a one-off fluke.

`@modelcontextprotocol/inspector@1.0.1` (the last v1 release; v1 is
deprecated upstream but still published) works reliably once `--transport
stdio` is passed explicitly -- v1's transport auto-detection doesn't
reliably fire for a `python -m ...`-style target the way it does for a
single-word command. With that flag, every method used below
(`tools/list`, `tools/call`, `resources/list`, `resources/read`) worked
against both devmcp directly and verimcp-fronting-devmcp. `scripts/
inspector_smoke_test.py` pins the exact version and documents why in its
own docstring, rather than silently depending on `@latest` and breaking
whenever that resolves to a new major version.

## Bug found: `resources/list`/`initialize` response tracking collided with the backend's own request ids

**Symptom**, caught on the smoke test's first real run against verimcp:
`resources/list` through verimcp returned *only* verimcp's two audit
resources -- devmcp's own `repo://status`/`repo://log`/`ci://last-run` were
silently missing, even though verimcp directly to devmcp returns those
fine, and this project's own `test_audit_log_records_and_exposes_a_forwarded_tool_call`
was already green.

**Root cause**, found via a byte-level stdio tee inserted between Inspector
and verimcp (`scripts/` history; not kept, was a throwaway debugging aid):
devmcp's own self-originated `roots/list` request is *always* id `1` (its
first request, its own independent counter -- see ADR 0002/Phase 1's
`--root` discussion). Inspector's `resources/list` request, on this run,
also happened to use id `1` (Host-side ids and a backend's own
self-originated ids are two entirely separate JSON-RPC id spaces with no
coordination between them -- ADR 0003 already made exactly this point once,
for elicitation). `Proxy._forward_backend_to_host`'s tracking of
`_pending_initialize`/`_pending_resource_list` (added in Phase 3, ADR 0004)
matched purely on `message.get("id") in self._pending_resource_list`,
**without checking whether the message was actually a response** (a request
never has a `"method"` key; a response never has one either -- so `"method"
not in message` is the correct, spec-level discriminator). devmcp's
`roots/list` *request* -- which does carry `"method"` -- collided on id and
got misread as *the* `resources/list` response, triggering the
error/no-result fallback branch in `_inject_audit_resources` (a bare
synthesized response containing only verimcp's own entries), one full
iteration before devmcp's genuine `resources/list` response arrived and was
forwarded unmodified (arriving too late -- `_pending_resource_list` had
already been consumed by the false match).

This was invisible to every existing integration test because they all
answer the Host's `roots/list`-forwarding *before* sending their next
request -- closing the window before it could be probed. Nothing in the MCP
spec requires a client to do that, and a real one-shot CLI client
(reasonably) doesn't.

**Fix**: both checks in `_forward_backend_to_host` now require `"method"
not in message` before treating an id match as the tracked response --
```python
if "method" not in message and message.get("id") in self._pending_initialize:
    ...
if "method" not in message and message.get("id") in self._pending_resource_list:
    ...
```
This is the same id-collision class ADR 0003 already solved once, for a
different mechanism (elicitation's reserved `verimcp-elicit-<n>` id prefix)
-- Phase 3 added a second, unguarded tracking mechanism later and
reintroduced the same risk in a new place. **Regression test**:
`tests/test_proxy_integration.py::test_resource_list_id_collision_with_backends_own_roots_list_id`
deliberately reuses id `1` for a Host-side `resources/list` request and
deliberately delays answering `roots/list`, reproducing the exact ordering
Inspector produced -- verified to fail without the fix (reverted locally,
confirmed red) and pass with it.

## Verification

`scripts/inspector_smoke_test.py`, run live against this repo: baseline
devmcp directly (`tools/list` five tools, `resources/list` three
resources), then verimcp-fronting-devmcp (`tools/list` superset,
`tools/call write_file` with a genuine on-disk side effect --
not just a claimed success --, `resources/list` correctly merging
devmcp's three resources with verimcp's two audit resources, and
`resources/read` on `verimcp://audit` -- the all-sessions URI, since each
`--cli` invocation is a fresh process/session -- returning the real
`write_file` entry recorded by an earlier invocation). **6/6 checks
passed** after the fix above.

## Consequences

- `scripts/inspector_smoke_test.py` is a repeatable artifact, not a one-off
  claim -- rerun it after any change to `proxy.py`'s message-forwarding
  logic.
- Inspector v2 remains untested here; pinning to v1.0.1 is a known,
  documented workaround, not a permanent position -- worth revisiting once
  a v2 patch release fixes the Windows argument-parsing issue (not
  investigated further, out of scope for this project to fix).
- `docs/ROADMAP.md` Phase 5's second sub-item -- testing against at least
  one more real client beyond Inspector (e.g. an editor's MCP integration)
  -- remains untested, stated plainly rather than implied as covered.
