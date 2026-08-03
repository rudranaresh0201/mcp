"""Unit tests for replay() -- policy backtesting against recorded audit
entries. No proxy, no backend, no live state: this is exactly why replay is
scoped to gate decisions (see docs/adr/0004), so these are pure-function
tests against ToolCallPolicyGate.from_yaml()."""
from pathlib import Path

from verimcp.gates.tool_call_policy import ToolCallPolicyGate
from verimcp.replay import format_report, replay


def _policy(tmp_path: Path, yaml_text: str) -> ToolCallPolicyGate:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml_text)
    return ToolCallPolicyGate.from_yaml(path)


def test_replay_flags_a_call_that_would_now_be_denied(tmp_path: Path):
    entries = [
        {"seq": 1, "method": "tools/call", "target": "write_file", "arguments": {"path": "a.txt"}, "outcome": "forwarded", "gate": None},
    ]
    gate = _policy(tmp_path, "rules:\n  - tool: write_file\n    action: deny\n")

    diffs = replay(entries, gate)

    assert diffs == [{"seq": 1, "target": "write_file", "was": "allow", "now": "deny"}]


def test_replay_flags_a_call_that_would_now_be_allowed(tmp_path: Path):
    entries = [
        {
            "seq": 1, "method": "tools/call", "target": "write_file", "arguments": {"path": "a.txt"},
            "outcome": "denied", "gate": {"action": "deny", "reason": "tool 'write_file' is denied by policy"},
        },
    ]
    gate = _policy(tmp_path, "rules: []\n")  # new policy has no rules at all -- default allow

    diffs = replay(entries, gate)

    assert diffs == [{"seq": 1, "target": "write_file", "was": "deny", "now": "allow"}]


def test_replay_ignores_calls_whose_verdict_is_unchanged(tmp_path: Path):
    entries = [
        {"seq": 1, "method": "tools/call", "target": "write_file", "arguments": {}, "outcome": "forwarded", "gate": None},
    ]
    gate = _policy(tmp_path, "rules:\n  - tool: some_other_tool\n    action: deny\n")

    assert replay(entries, gate) == []


def test_replay_distinguishes_deny_from_require_approval(tmp_path: Path):
    """A call originally required approval and was accepted (outcome
    "verified_ok", not "denied") -- replay must read the *gate* action, not
    infer from outcome, or this would be misreported as "was: allow"."""
    entries = [
        {
            "seq": 1, "method": "tools/call", "target": "write_file", "arguments": {},
            "outcome": "verified_ok", "gate": {"action": "require_approval", "reason": "approved by Host"},
        },
    ]
    gate = _policy(tmp_path, "rules:\n  - tool: write_file\n    action: deny\n")

    diffs = replay(entries, gate)

    assert diffs == [{"seq": 1, "target": "write_file", "was": "require_approval", "now": "deny"}]


def test_replay_skips_non_tool_call_entries(tmp_path: Path):
    entries = [{"seq": 1, "method": "resources/read", "target": "repo://file/a.txt", "arguments": None, "outcome": "forwarded", "gate": None}]
    gate = _policy(tmp_path, "rules: []\n")

    assert replay(entries, gate) == []


def test_format_report_empty_diff():
    assert "No verdict changes" in format_report([])


def test_format_report_lists_each_diff():
    report = format_report([{"seq": 1, "target": "write_file", "was": "allow", "now": "deny"}])
    assert "#1" in report
    assert "write_file" in report
    assert "allow -> deny" in report
