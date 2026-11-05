"""Verify-before-retry, adapted from arxiv 2608.02645 ("Verified Tool Calls
Improve LLM Agent Reliability Under Non-Atomic Failures"). That paper wraps
an agent's own tool-calling loop: before retrying a call whose result was
ambiguous (timeout, delayed visibility), it asks a postcondition verifier
"did this already succeed?" and only re-executes if the answer is no.

verimcp already has the postcondition verifier half of that (the existing
Verifier registry -- FilesystemVerifier, GitCommitVerifier, etc. each
independently recheck ground truth after a call). What's new here is the
*retry* half, moved to the proxy layer instead of the agent's tool wrapper:
verimcp sees every tools/call from every Host, regardless of which agent
framework (if any) is doing the retrying above it, so it can catch a retry
and consult "did the last identical call already pass verification?" before
ever forwarding it to the backend a second time.

Deliberate simplification vs the paper: their idempotency key is
caller-supplied (hash of agent_id, action_type, payload, timestamp_bucket) --
callers are trusted to declare "this is the same logical operation." verimcp
doesn't need that: the key here is derived purely from (tool name,
arguments), and safety doesn't come from the key matching -- it comes from
the *previous* call's postcondition having been independently verified true.
A key collision with no prior verified success is a cache miss, not a false
dedupe.
"""
import hashlib
import json
from typing import Any


def compute_key(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Deterministic key for one logical tool invocation. sort_keys makes
    argument order irrelevant (two retries of "the same call" should hash
    identically even if a client happens to serialize dict keys differently
    between attempts)."""
    canonical = json.dumps({"tool": tool_name, "arguments": arguments or {}}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyCache:
    """In-memory, per-Proxy-instance (i.e. per Host<->backend session) --
    same lifetime as self._pending. A retry from a brand-new session has no
    entry to find, by design: verify-before-retry only ever helps *within*
    a session that's actively retrying, it isn't a durable cross-session
    dedupe store.

    Only an *immediate* repeat counts as a retry. A key built from (tool,
    arguments) alone cannot tell a resend from a deliberate repeat, so any
    call that actually reaches the backend clears the cache: after
    write(a, v1), write(a, v2), a later write(a, v1) is real work and must
    run, not be answered "wrote v1" from a cache while v2 sits on disk.
    The generation counter covers replies that arrive out of order: a
    reply to a call sent before the most recent forwarded call is stale
    and is never cached.
    """

    def __init__(self) -> None:
        self._verified_ok: dict[str, dict[str, Any]] = {}
        self._generation = 0

    def lookup(self, key: str) -> dict[str, Any] | None:
        return self._verified_ok.get(key)

    def note_forwarded(self) -> int:
        """Call for every tools/call that is sent to the backend. Returns the
        generation to pass back to record_verified_ok with its reply."""
        self._verified_ok.clear()
        self._generation += 1
        return self._generation

    def record_verified_ok(self, key: str, response: dict[str, Any], generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return  # another call reached the backend since this one was sent
        self._verified_ok[key] = response
