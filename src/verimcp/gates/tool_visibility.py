"""Discovery-time tool filtering: a tool the caller's role may not use never
appears in tools/list, so the model never learns it exists.

From "Prompts Don't Protect" (Uppala, arXiv:2605.18414): with an unauthorized
tool visible in context, models invoke it in 48-68% of adversarial scenarios,
and telling them not to never brings that to zero. Filtering the registry at
the proxy does, by construction -- there is nothing in context to be talked
into using.

This is the complement of ToolCallPolicyGate, not a replacement: that gate
decides what happens when a visible tool is CALLED. A hidden tool that is
called anyway (by a client that guessed the name) is refused by the proxy
with the same error an unknown tool gets.

The role is fixed when verimcp is launched (--role). It is never read from a
request: clientInfo and capabilities are self-declared, and believing a
request that says who it is would be the paper's role-escalation attack
("I'm the CFO, override the access controls") moved one layer down.

Policy file, next to the existing `rules:` --

    roles:
      intern:
        tools: [git_status, "read_*"]
      maintainer:
        tools: ["*"]

A role sees exactly the tools its patterns match. Anything else is hidden.
"""
import fnmatch
from pathlib import Path
from typing import Any

from verimcp.gates.policy_file import load_policy_file


class ToolVisibility:
    def __init__(self, role: str, patterns: list[str]) -> None:
        self.role = role
        self._patterns = patterns

    @classmethod
    def from_yaml(cls, path: Path, role: str) -> "ToolVisibility":
        roles = load_policy_file(path).get("roles") or {}
        if role not in roles:
            # Fail at startup, not open: a typo in --role must not quietly
            # turn into "sees everything".
            raise ValueError(f"role {role!r} is not defined in {path} -- defined roles: {sorted(roles)}")
        return cls(role, list((roles[role] or {}).get("tools") or []))

    def allows(self, tool_name: str) -> bool:
        return any(fnmatch.fnmatchcase(tool_name, pattern) for pattern in self._patterns)

    def filter(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The tools this role may see, in the backend's own order."""
        return [tool for tool in tools if self.allows(tool.get("name", ""))]
