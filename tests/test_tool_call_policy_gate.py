"""Unit-level: exercise ToolCallPolicyGate directly, no proxy involved."""
from pathlib import Path

import pytest

from verimcp.gates.tool_call_policy import ToolCallPolicyGate


def _tool_call(tool_name: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name}}


def test_applies_only_to_tools_call():
    gate = ToolCallPolicyGate(rules=[])

    assert gate.applies_to("tools/call") is True
    assert gate.applies_to("sampling/createMessage") is False
    assert gate.applies_to("resources/read") is False


def test_denies_a_tool_matched_by_a_deny_rule():
    gate = ToolCallPolicyGate(rules=[{"tool": "run_ci_pipeline", "action": "deny"}])

    denial = gate.check(_tool_call("run_ci_pipeline"))

    assert denial is not None
    assert denial["error"]["code"] == -32002
    assert "run_ci_pipeline" in denial["error"]["message"]
    assert "denied by policy" in denial["error"]["message"]


def test_allows_a_tool_matched_by_an_allow_rule():
    gate = ToolCallPolicyGate(rules=[{"tool": "write_file", "action": "allow"}])

    assert gate.check(_tool_call("write_file")) is None


def test_allows_a_tool_with_no_matching_rule():
    """Opt-in overlay, not default-deny: an unlisted tool is allowed."""
    gate = ToolCallPolicyGate(rules=[{"tool": "run_ci_pipeline", "action": "deny"}])

    assert gate.check(_tool_call("write_file")) is None


def test_first_matching_rule_wins():
    gate = ToolCallPolicyGate(
        rules=[
            {"tool": "write_file", "action": "allow"},
            {"tool": "write_file", "action": "deny"},
        ]
    )

    assert gate.check(_tool_call("write_file")) is None


def test_from_yaml_loads_rules(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: run_ci_pipeline\n    action: deny\n")

    gate = ToolCallPolicyGate.from_yaml(policy_file)

    assert gate.check(_tool_call("run_ci_pipeline")) is not None
    assert gate.check(_tool_call("write_file")) is None


def test_from_yaml_with_no_rules_key_allows_everything(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("{}\n")

    gate = ToolCallPolicyGate.from_yaml(policy_file)

    assert gate.check(_tool_call("write_file")) is None


def test_from_yaml_rejects_an_invalid_action(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: maybe\n")

    with pytest.raises(ValueError, match="invalid action"):
        ToolCallPolicyGate.from_yaml(policy_file)


def test_from_yaml_accepts_require_approval(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: git_commit\n    action: require_approval\n")

    gate = ToolCallPolicyGate.from_yaml(policy_file)

    assert gate.action_for("git_commit") == "require_approval"


def test_action_for_returns_none_when_no_rule_matches():
    gate = ToolCallPolicyGate(rules=[{"tool": "run_ci_pipeline", "action": "deny"}])

    assert gate.action_for("write_file") is None


def test_check_does_not_deny_a_require_approval_match():
    """require_approval is deliberately a no-op pass-through here -- the
    proxy intercepts it via action_for() before check() is ever called for
    this case; check() alone must never turn it into a denial."""
    gate = ToolCallPolicyGate(rules=[{"tool": "git_commit", "action": "require_approval"}])

    assert gate.check(_tool_call("git_commit")) is None


def test_matches_arguments_exact_value():
    gate = ToolCallPolicyGate(
        rules=[{"tool": "write_file", "arguments": {"path": "secrets.env"}, "action": "deny"}]
    )

    assert gate.action_for("write_file", {"path": "secrets.env"}) == "deny"
    assert gate.action_for("write_file", {"path": "notes.txt"}) is None


def test_matches_arguments_glob():
    gate = ToolCallPolicyGate(
        rules=[{"tool": "write_file", "arguments": {"path": "*.env"}, "action": "deny"}]
    )

    assert gate.action_for("write_file", {"path": "prod.env"}) == "deny"
    assert gate.action_for("write_file", {"path": "notes.txt"}) is None


def test_matches_arguments_and_across_keys():
    gate = ToolCallPolicyGate(
        rules=[
            {
                "tool": "run_ci_pipeline",
                "arguments": {"name": "deploy", "env": "prod"},
                "action": "require_approval",
            }
        ]
    )

    assert gate.action_for("run_ci_pipeline", {"name": "deploy", "env": "prod"}) == "require_approval"
    # one mismatching key fails the whole rule
    assert gate.action_for("run_ci_pipeline", {"name": "deploy", "env": "staging"}) is None


def test_matches_arguments_missing_key_does_not_match_or_crash():
    gate = ToolCallPolicyGate(
        rules=[{"tool": "write_file", "arguments": {"path": "*.env"}, "action": "deny"}]
    )

    assert gate.action_for("write_file", {"content": "hello"}) is None
    assert gate.action_for("write_file", {}) is None
    assert gate.action_for("write_file", None) is None


def test_rule_with_no_arguments_key_still_matches_on_tool_name_alone():
    gate = ToolCallPolicyGate(rules=[{"tool": "run_ci_pipeline", "action": "deny"}])

    assert gate.action_for("run_ci_pipeline", {"steps": []}) == "deny"
