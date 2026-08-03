"""Session replay, Phase 3's second half: "not a separate system -- just
feeding [the audit] JSONL back through the same proxy message flow"
(docs/ROADMAP.md).

Deliberately scoped to policy/gate decisions, not full verifier re-runs.
ToolCallPolicyGate.action_for() is a pure function of (tool name, arguments)
-- replaying it against a new --policy-config is fully sound offline. Verifiers
like FilesystemVerifier/GitServerCommitVerifier re-check *live* ground truth
(current disk/repo state); replaying "was this response valid" against
today's filesystem for an old write_file call would silently check the wrong
thing once the file has changed since the recording. Rather than build a
replay feature that quietly gives wrong answers for state-dependent verifiers,
this only re-runs the part that's sound to re-run everywhere: the gate. See
docs/adr/0004-audit-log-as-mcp-resource-and-policy-replay.md.
"""
from typing import Any

from verimcp.gates.tool_call_policy import ToolCallPolicyGate


def replay(entries: list[dict[str, Any]], policy_gate: ToolCallPolicyGate) -> list[dict[str, Any]]:
    """Re-run `policy_gate` against every recorded tools/call entry, comparing
    its verdict now against what was actually decided at record time. Returns
    only the entries whose verdict would change -- an empty list means the
    new policy is a no-op against this session's traffic."""
    diffs = []
    for entry in entries:
        if entry.get("method") != "tools/call":
            continue

        gate = entry.get("gate")
        was = gate["action"] if gate else "allow"
        now = policy_gate.action_for(entry.get("target", ""), entry.get("arguments")) or "allow"

        if now != was:
            diffs.append({"seq": entry.get("seq"), "target": entry.get("target"), "was": was, "now": now})
    return diffs


def format_report(diffs: list[dict[str, Any]]) -> str:
    if not diffs:
        return "No verdict changes -- the new policy agrees with every recorded decision."
    lines = [f"{len(diffs)} verdict change(s):"]
    for diff in diffs:
        lines.append(f"  #{diff['seq']} {diff['target']!r}: {diff['was']} -> {diff['now']}")
    return "\n".join(lines)
