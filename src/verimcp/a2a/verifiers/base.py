"""The TaskVerifier interface -- the A2A counterpart to verifiers/base.py.

The claim boundary moves up a layer. In MCP a *tool* reports on work it did
and the Host has no way to check; in A2A an *agent* reports on work it did
and the calling agent has no way to check. The hole is the same shape and
strictly worse here: A2A agents are opaque to each other by design (that is
what an Agent Card is for -- advertised skills, not internals), errors
compound across hops, and unlike MCP there is no Host in the loop that could
elicit a human.

One thing is genuinely simpler than the MCP side. `Verifier.verify` needs
(request, response, root) because an MCP response does not carry what was
asked or where it happened. An A2A Task carries its own `history` (the
messages) and `artifacts` (the outputs) in one object, so `verify(task)`
has everything it needs.

Enforcement is the mirror image too: MCP rewrites `isError: false` into
`isError: true`; here a claimed `completed` is rewritten to `failed` with a
real reason attached, so the calling agent acts on the contradiction rather
than the claim.
"""
import uuid
from abc import ABC, abstractmethod
from typing import Any

from verimcp.a2a import task as task_mod

# The one place this string is written. `_fail` builds on it and
# `extract_failure_detail` reads it back off, in this module, on purpose --
# the MCP side learned that the hard way on 2026-08-30, when the diagnostic
# was constructed in verifiers/base.py and never persisted anywhere, so the
# audit log could say "passed: false" and nothing more.
FAILURE_PREFIX = "[verimcp] task postcondition check failed: "


def extract_failure_detail(task: dict[str, Any]) -> str | None:
    """Recover the reason out of a task `_fail` produced, or None if this
    failure did not come from verimcp.

    That None case carries the same meaning it does on the MCP side: an agent
    reporting its own failure is a self-report, which is exactly what this
    project declines to treat as evidence. Only a verimcp-authored message
    represents an independently re-derived contradiction.
    """
    status = task.get("status")
    if not isinstance(status, dict):
        return None
    message = status.get("message")
    if not isinstance(message, dict):
        return None

    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.startswith(FAILURE_PREFIX):
            return text[len(FAILURE_PREFIX):]
    return None


class TaskVerifier(ABC):
    @abstractmethod
    def applies_to(self, task: dict[str, Any]) -> bool:
        """Should this verifier run for this task?

        Takes the whole task, not a skill name, because A2A has no single
        equivalent of MCP's `tools/call` name -- what an agent did is
        described by its artifacts and history, and different verifiers key
        off different parts of that.
        """

    @abstractmethod
    def verify(self, task: dict[str, Any]) -> dict[str, Any]:
        """Return the task to actually forward to the calling agent.

        Unchanged if the claim holds. If it does not, return `_fail(...)` --
        the same fusing of checking and enforcement the MCP Verifier uses, and
        for the same reason: a boolean would leave every call site to build
        its own failure shape, and they would drift.

        A verifier that cannot check something must return `task` unchanged.
        Failing open on the unknown and closed on the disproven is what makes
        this deployable; a verifier that blocked whenever it was unsure would
        be turned off within a day.
        """

    @staticmethod
    def _fail(task: dict[str, Any], message: str) -> dict[str, Any]:
        """Rewrite a claimed-successful task into a real failure.

        Returns a new dict rather than mutating: the original task may still
        be referenced by the caller (for logging, for the audit record), and a
        verifier quietly rewriting an object someone else holds is the kind of
        bug that only shows up under concurrency.
        """
        failed = dict(task)
        failed["status"] = {
            "state": task_mod.FAILED,
            "message": {
                "role": "agent",
                "kind": "message",
                "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": f"{FAILURE_PREFIX}{message}"}],
            },
        }
        return failed
