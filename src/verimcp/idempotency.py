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
    dedupe store."""

    def __init__(self) -> None:
        self._verified_ok: dict[str, dict[str, Any]] = {}

    def lookup(self, key: str) -> dict[str, Any] | None:
        return self._verified_ok.get(key)

    def record_verified_ok(self, key: str, response: dict[str, Any]) -> None:
        self._verified_ok[key] = response
