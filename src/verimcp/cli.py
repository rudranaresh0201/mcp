"""Entry point for the verimcp verification proxy.

Unlike devmcp/cli.py, this doesn't need to build its own stdio workaround
here -- Proxy.run() already owns that internally (it's both a server to the
Host and a client to the backend at once, so it manages both stdio
directions itself). This file's only job is turning argv into a backend
command and handing it to Proxy.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from verimcp.gates.tool_call_policy import ToolCallPolicyGate
from verimcp.proxy import Proxy
from verimcp.replay import format_report, replay


def main() -> int:
    # Branched here, before the main parser exists, rather than as an
    # argparse subparser: backend_cmd below is nargs=REMAINDER, which a
    # subparser would force behind a new required prefix (e.g. `verimcp run
    # -- backend...`), breaking every existing `verimcp --flag -- backend...`
    # invocation (including the test suite). `replay` never takes a backend
    # command at all, so it's simplest as its own small parser.
    if len(sys.argv) > 1 and sys.argv[1] == "replay":
        return _replay_main(sys.argv[2:])

    parser = argparse.ArgumentParser(
        prog="verimcp",
        description="Verification proxy: sits between an MCP Host and a backend MCP server, "
        "forwarding traffic and independently re-verifying tool-call claims.",
    )
    parser.add_argument(
        "--verifiers",
        default=None,
        help="Comma-separated verifier names (entry points under the verimcp.verifiers "
        "group) to load, e.g. --verifiers git_server_commit. Omit to load every verifier "
        "installed -- restricting this matters once more than one backend is in play, "
        "since two backends can expose a same-named tool with differently-shaped responses.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Override for the backend's real working directory. Only needed for backends "
        "that never send the Host a roots/list request (verimcp normally learns the root by "
        "watching that exchange, same as devmcp's convention) -- e.g. the reference "
        "mcp-server-git takes its repo path from its own --repository flag instead and never "
        "asks the Host at all, so a verifier would otherwise have no root to check against.",
    )
    parser.add_argument(
        "--sampling-limit",
        type=int,
        default=10,
        help="Max sampling/createMessage requests a backend may make per --sampling-window "
        "before verimcp starts denying them on the backend's behalf (default: 10).",
    )
    parser.add_argument(
        "--sampling-window",
        type=float,
        default=60.0,
        help="Sliding window in seconds over which --sampling-limit applies (default: 60).",
    )
    parser.add_argument(
        "--policy-config",
        default=None,
        help="Path to a YAML policy file (rules: [{tool: <name>, arguments?: {key: glob}, "
        "action: allow|deny|require_approval}]) evaluated against every tools/call before it "
        "reaches the backend. Omit for no tools/call policy gating at all (existing behavior).",
    )
    parser.add_argument(
        "--approval-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for the Host to answer a require_approval elicitation before "
        "denying the tool call (default: 60). Only relevant if --policy-config has a "
        "require_approval rule.",
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Path to a JSONL file recording every tools/call and resources/read this proxy "
        "handles (arguments, timestamp, verification result, approval status). Also served "
        "back to the Host as the verimcp://audit and verimcp://audit/current MCP resources. "
        "Omit for no audit logging at all (existing behavior).",
    )
    parser.add_argument(
        "backend_cmd",
        nargs=argparse.REMAINDER,
        help="Backend MCP server command to launch, e.g.: verimcp -- devmcp --repo-path ./myrepo",
    )
    args = parser.parse_args()

    backend_cmd = args.backend_cmd
    if backend_cmd and backend_cmd[0] == "--":
        backend_cmd = backend_cmd[1:]
    if not backend_cmd:
        parser.error("no backend command given -- usage: verimcp -- <backend command...>")

    verifier_names = args.verifiers.split(",") if args.verifiers else None
    root_override = Path(args.root).resolve() if args.root else None
    policy_config = Path(args.policy_config).resolve() if args.policy_config else None
    audit_log = Path(args.audit_log).resolve() if args.audit_log else None

    asyncio.run(
        Proxy(
            backend_cmd,
            verifier_names=verifier_names,
            root_override=root_override,
            sampling_limit=args.sampling_limit,
            sampling_window=args.sampling_window,
            policy_config=policy_config,
            approval_timeout=args.approval_timeout,
            audit_log=audit_log,
        ).run()
    )
    return 0


def _replay_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="verimcp replay",
        description="Policy backtesting: re-run a --policy-config's allow/deny/require_approval "
        "decisions against a recorded --audit-log, and report which verdicts would change. "
        "Scoped to gate decisions only (a pure function of tool name + arguments) -- not a "
        "full re-verification, since verifiers like FilesystemVerifier check *live* ground "
        "truth that may have moved on since the session was recorded.",
    )
    parser.add_argument("audit_log", help="Path to a JSONL file previously written by --audit-log.")
    parser.add_argument(
        "--policy-config", required=True, help="YAML policy file to replay recorded tool calls against."
    )
    parser.add_argument(
        "--session", default=None, help="Restrict replay to one session id. Omit to replay every session in the log."
    )
    args = parser.parse_args(argv)

    audit_log_path = Path(args.audit_log)
    if not audit_log_path.exists():
        parser.error(f"audit log not found: {audit_log_path}")

    entries = [json.loads(line) for line in audit_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.session is not None:
        entries = [e for e in entries if e.get("session_id") == args.session]

    policy_gate = ToolCallPolicyGate.from_yaml(Path(args.policy_config).resolve())
    diffs = replay(entries, policy_gate)
    print(format_report(diffs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
