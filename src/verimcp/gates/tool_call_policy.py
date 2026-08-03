"""Phase 2's policy gate: allow/deny/require_approval on tools/call, evaluated
before the backend ever runs the tool -- unlike a Verifier, which only checks
after. See docs/ROADMAP.md Phase 2.

require_approval is deliberately NOT enforced by check() below -- unlike
allow/deny, it needs an async round-trip (send elicitation/create to the
Host, await the reply) that this gate has no way to perform, since it only
ever sees one request at a time with no access to the proxy's transport.
Proxy._forward_host_to_backend calls action_for() directly for tools/call,
before ever calling check(), and handles require_approval itself -- see
docs/adr/0003-require-approval-via-elicitation.md.
"""
import fnmatch
from pathlib import Path
from typing import Any

import yaml

from verimcp.gates.base import RequestGate

_VALID_ACTIONS = {"allow", "deny", "require_approval"}


class ToolCallPolicyGate(RequestGate):
    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self._rules = rules

    @classmethod
    def from_yaml(cls, path: Path) -> "ToolCallPolicyGate":
        data = yaml.safe_load(path.read_text()) or {}
        rules = data.get("rules") or []
        for rule in rules:
            if rule.get("action") not in _VALID_ACTIONS:
                raise ValueError(
                    f"policy rule for tool {rule.get('tool')!r} has invalid action "
                    f"{rule.get('action')!r} -- must be one of {sorted(_VALID_ACTIONS)}"
                )
        return cls(rules)

    def applies_to(self, method: str) -> bool:
        return method == "tools/call"

    def action_for(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str | None:
        """Pure rule lookup, no I/O: which action (if any) matches this tool
        call? First matching rule wins; None means no rule matched (the
        default is allow -- this is an opt-in overlay, not a default-deny
        gate)."""
        arguments = arguments or {}
        for rule in self._rules:
            if rule.get("tool") != tool_name:
                continue
            if not self._matches_arguments(rule.get("arguments"), arguments):
                continue
            return rule["action"]
        return None

    @staticmethod
    def _matches_arguments(rule_arguments: dict[str, str] | None, actual_arguments: dict[str, Any]) -> bool:
        if not rule_arguments:
            return True
        # AND across keys -- every key the rule specifies must be present
        # in the actual call and match its glob pattern. A key the rule
        # cares about but the call never supplied is a non-match, not a
        # crash (e.g. an optional argument the caller omitted).
        return all(
            key in actual_arguments and fnmatch.fnmatch(str(actual_arguments[key]), pattern)
            for key, pattern in rule_arguments.items()
        )

    def check(self, request: dict[str, Any]) -> dict[str, Any] | None:
        params = request.get("params", {})
        tool_name = params.get("name", "")
        action = self.action_for(tool_name, params.get("arguments"))
        if action == "deny":
            return self._deny(request, f"tool {tool_name!r} is denied by policy")
        return None
