"""Reading an A2A Task safely, across both spec generations.

A2A renamed things between v0.3.x and v1.0 without changing what they mean:

    v0.3.x                     v1.0+
    state: "completed"         state: "TASK_STATE_COMPLETED"
    method: "message/send"     method: "a2a.SendMessage"

Both are deployed right now -- v0.3.x is what most live agents speak, v1.0 is
current -- and a proxy does not get to pick which one the agent on the other
side implements. So every state comparison in this package goes through
`state_of()` rather than reading `task["status"]["state"]` directly. The
alternative is `if state in ("completed", "TASK_STATE_COMPLETED")` scattered
across every verifier, which is the shape of code that gets one branch right
and the other wrong six months later.

Nothing here does I/O or knows about HTTP. Same reason verifiers/base.py
doesn't: the part that decides whether a claim is true should be testable
without a network.
"""
from typing import Any

# Canonical (v0.3-style) names. v1.0's TASK_STATE_* form normalizes onto these
# rather than the other way round, because they read better in code and in the
# audit log -- and the audit log is a record humans read.
SUBMITTED = "submitted"
WORKING = "working"
INPUT_REQUIRED = "input-required"
AUTH_REQUIRED = "auth-required"
COMPLETED = "completed"
FAILED = "failed"
CANCELED = "canceled"
REJECTED = "rejected"
UNKNOWN = "unknown"

# Once a task reaches one of these it will not change again, so it is the only
# point at which a claim is final enough to be worth checking. Verifying a
# `working` task would be checking a half-finished job.
TERMINAL_STATES = frozenset({COMPLETED, FAILED, CANCELED, REJECTED})

_V1_PREFIX = "TASK_STATE_"
_ALIASES = {
    "input_required": INPUT_REQUIRED,
    "auth_required": AUTH_REQUIRED,
    "unspecified": UNKNOWN,
}


def normalize_state(raw: Any) -> str:
    """Map any spec generation's spelling onto one canonical name.

    Unrecognized values come back as UNKNOWN rather than raising. A proxy that
    crashed on a state it had not seen would take down a working agent
    conversation over a vocabulary it does not need to understand -- and the
    A2A extension mechanism (v1.0.1) exists precisely so agents can add state
    machines this package has never heard of.
    """
    if not isinstance(raw, str) or not raw:
        return UNKNOWN

    name = raw.strip()
    if name.startswith(_V1_PREFIX):
        name = name[len(_V1_PREFIX):]
    name = name.lower().replace("_", "-")

    # a couple of spellings that survive the transform in the wrong shape
    name = _ALIASES.get(name.replace("-", "_"), name)
    return name if name in _KNOWN else UNKNOWN


_KNOWN = frozenset({
    SUBMITTED, WORKING, INPUT_REQUIRED, AUTH_REQUIRED,
    COMPLETED, FAILED, CANCELED, REJECTED,
})


def state_of(task: dict[str, Any]) -> str:
    """The task's normalized state. Never raises on a malformed task -- a
    missing or oddly-shaped `status` reads as UNKNOWN, which no verifier
    acts on."""
    status = task.get("status")
    if not isinstance(status, dict):
        return UNKNOWN
    return normalize_state(status.get("state"))


def is_terminal(task: dict[str, Any]) -> bool:
    return state_of(task) in TERMINAL_STATES


def claims_success(task: dict[str, Any]) -> bool:
    """The A2A analogue of `isError: false`.

    This is the only condition under which a verifier has anything to do. A
    task that failed, was canceled, or was rejected is the agent reporting its
    own outcome -- there is no success claim to contradict, exactly as with
    MCP's backend_error.
    """
    return state_of(task) == COMPLETED


def artifacts_of(task: dict[str, Any]) -> list[dict[str, Any]]:
    """The task's output artifacts, or [] -- `artifacts` is optional in the
    spec and absent on a task that produced no output."""
    artifacts = task.get("artifacts")
    return [a for a in artifacts if isinstance(a, dict)] if isinstance(artifacts, list) else []


def parts_of(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    parts = artifact.get("parts")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def text_of(task: dict[str, Any]) -> str:
    """All TextPart content across every artifact, joined.

    Convenience for verifiers that need to find a claim (a URL, a path, a
    commit hash) inside what the agent produced. `kind` is the spec's
    discriminator across TextPart / FilePart / DataPart; anything that is not
    a text part is skipped rather than stringified, so a base64 file blob
    never lands in a regex.
    """
    chunks: list[str] = []
    for artifact in artifacts_of(task):
        for part in parts_of(artifact):
            if part.get("kind") == "text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(chunks)


def data_of(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Every DataPart payload across the task's artifacts.

    This is the A2A counterpart to MCP's `structuredContent`, and it is where
    a checkable claim usually lives: {"deployed_url": ...}, {"commit": ...}.
    Free text is what an agent *says*; a DataPart is what it *asserts*.
    """
    payloads = []
    for artifact in artifacts_of(task):
        for part in parts_of(artifact):
            if part.get("kind") == "data" and isinstance(part.get("data"), dict):
                payloads.append(part["data"])
    return payloads
