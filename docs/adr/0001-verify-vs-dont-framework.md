# ADR 0001: What verimcp verifies, and what it deliberately doesn't

## Status

Accepted

## Context

MCP's spec defines six primitives a Host/Client and Server exchange:
`tools`, `resources`, `prompts`, `roots`, and `sampling`, sitting on top of
the `initialize` capability handshake. For `tools/call`, the spec only
guarantees that `isError: false` means the tool didn't raise while running —
it says nothing about whether the tool's *claimed effect* actually happened.
A tool can report success while doing nothing, or doing something different
from what it claims. Nothing in the protocol catches this; a client has to
trust the server.

verimcp exists to close that gap for the primitives where it's meaningful to
close — but "meaningful" isn't the same for all six, and treating them
identically would either under-verify (leave real gaps open) or over-verify
(reject things that were never lies to begin with, or invent "ground truth"
where none exists).

## Decision

We split the primitives into three treatments:

**Correctness verifiers** — independently re-derive ground truth and compare
it to the claim. Applies where an objective, externally-checkable fact
exists that the response is making a claim about:
- `tools/call` — e.g. did `write_file` actually write that content to that
  path; did `git_commit` actually produce a commit with that hash. The
  verifier re-reads the file / re-runs `git show`, and compares.
- `resources/read` — the response claims to reflect current state; the
  verifier re-fetches the same resource independently and compares.

**Policy gates** — no objective truth to check (nothing to compare against),
but a real cost/abuse surface worth constraining. Applies to:
- `sampling/createMessage` — a Server asking the Client to run an LLM
  completion on its behalf. There's no "correct" generated text to verify
  against, but there is a real question of whether this Server should be
  allowed to make this request at all, how often, and how large. This is
  authorization, not correctness — a `RequestGate`, not a `Verifier`.

**No verifier, on principle** — not an oversight, a decision that no
independent check is coherent here:
- `prompts/get` — static templates. There's no claim about external state to
  falsify.
- `roots/list` — the *Client* is definitionally authoritative over its own
  roots. The Server has no independent source of truth to compare the
  Client's answer against; asking "did the Client lie about its own roots"
  doesn't have a meaningful ground truth outside the Client itself.

## Consequences

- Every new verifier added to this project must first answer: which of the
  three buckets above does this belong to? A correctness verifier that has
  no independent ground truth to check against (e.g. reusing the server's
  own code path to "verify" its own claim) doesn't belong in this bucket —
  see the decoupled-packages decision, which exists partly to keep
  correctness verifiers honest.
- `prompts` and `roots` are expected to stay unverified permanently, not as
  a stretch goal.
