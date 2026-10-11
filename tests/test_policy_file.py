"""A policy file must never load as "no policy" by accident.

The gates default to allow, so a file that silently parses as empty turns every
deny / require_approval into allow. Found 2026-10-11 with a file written by
Windows PowerShell 5.1 (`Set-Content -Encoding utf8` adds a BOM): zero rules
loaded, no error.
"""
import subprocess
import sys

import pytest

from verimcp.gates.policy_file import PolicyFileError, load_policy_file
from verimcp.gates.tool_call_policy import ToolCallPolicyGate
from verimcp.gates.tool_visibility import ToolVisibility

RULES = "rules:\n  - tool: write_file\n    action: deny\n"


def write(tmp_path, data: bytes):
    path = tmp_path / "policy.yaml"
    path.write_bytes(data)
    return path


def test_a_bom_no_longer_hides_the_rules(tmp_path):
    path = write(tmp_path, b"\xef\xbb\xbf" + RULES.encode())
    assert ToolCallPolicyGate.from_yaml(path).action_for("write_file") == "deny"


def test_a_bom_no_longer_hides_the_roles(tmp_path):
    path = write(tmp_path, b"\xef\xbb\xbfroles:\n  intern:\n    tools: [git_status]\n")
    assert ToolVisibility.from_yaml(path, "intern").allows("git_status")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("rule:\n  - tool: write_file\n    action: deny\n", "unknown top-level key"),  # typo
        ("", "empty"),
        ("- tool: write_file\n  action: deny\n", "must be a mapping"),  # forgot `rules:`
        ("rules:\n  - tools: write_file\n    action: deny\n", "needs a `tool:`"),  # typo inside a rule
    ],
)
def test_anything_that_would_load_as_no_policy_is_refused(tmp_path, content, message):
    with pytest.raises(PolicyFileError, match=message):
        load_policy_file(write(tmp_path, content.encode()))


@pytest.mark.parametrize("content", ["rules: []\n", "roles:\n  intern:\n    tools: ['*']\n"])
def test_deliberately_empty_rules_or_roles_only_are_still_fine(tmp_path, content):
    load_policy_file(write(tmp_path, content.encode()))


def test_verimcp_refuses_to_start_with_a_readable_error(tmp_path):
    path = write(tmp_path, b"rule:\n  - tool: write_file\n    action: deny\n")
    result = subprocess.run(
        [sys.executable, "-m", "verimcp.cli", "--policy-config", str(path), "--", sys.executable, "-c", "pass"],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr and "unknown top-level key" in result.stderr
    assert "Traceback" not in result.stderr
