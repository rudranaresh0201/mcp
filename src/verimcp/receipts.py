"""Receipts: what a verifier actually observed, shown as proof.

A pass/fail verdict asks the reader to trust verimcp. A receipt shows the
facts it read (a PR's real title, a commit's real message, a file's real
hash), where it read them, and when -- for a claim that held as much as for
one that didn't.

Receipts always go into the audit log (and so the console). With
--receipts they are also appended to the tool reply as one extra text
block, so the agent and the person watching the chat see the proof inline.
The original content blocks are left in place and in order: anything that
reads content[0] keeps working.
"""
import copy
from datetime import UTC, datetime
from typing import Any

HEADER = "[verimcp receipt]"


def fallback(verifier_names: list[str], passed: bool | None, detail: str | None) -> list[dict[str, Any]]:
    """For a call a verifier checked but that verifier records no receipt of
    its own (e.g. a third-party verifier written before receipts existed).
    Nothing is invented: it says only what the audit log already knows."""
    if passed is None:
        return []  # the backend reported its own error: nothing was checked, so nothing to prove
    checked_at = datetime.now(UTC).isoformat(timespec="seconds")
    names = ", ".join(verifier_names)
    if passed:
        summary = f"passed {names}'s check (this verifier doesn't record receipt details yet)"
    else:
        summary = detail or f"contradicted by {names}"
    return [{"verifier": names, "verdict": "verified" if passed else "contradicted", "summary": summary,
             "source": "verimcp verifier", "checked_at": checked_at, "evidence": {}}]


def format_text(receipts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for receipt in receipts:
        lines.append(f"{HEADER} {receipt['verdict'].upper()} · {receipt['verifier']} · {receipt['checked_at']}")
        lines.append(receipt["summary"])
        lines.append(f"source: {receipt['source']}")
        url = (receipt.get("evidence") or {}).get("url")
        if url:
            lines.append(f"link: {url}")
    return "\n".join(lines)


def attach_to_reply(message: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """A copy of a tools/call reply with the receipt block appended. A copy,
    because the original may also be held by the verify-before-retry cache,
    and a replayed answer must not carry a stale receipt as if newly checked."""
    result = message.get("result")
    if not receipts or not isinstance(result, dict):
        return message
    with_receipt = copy.deepcopy(message)
    content = with_receipt["result"].setdefault("content", [])
    content.append({"type": "text", "text": format_text(receipts)})
    return with_receipt
