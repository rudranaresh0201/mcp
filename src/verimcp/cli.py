"""Entry point for the verimcp verification proxy.

Unlike devmcp/cli.py, this doesn't need to build its own stdio workaround
here -- Proxy.run() already owns that internally (it's both a server to the
Host and a client to the backend at once, so it manages both stdio
directions itself). This file's only job is turning argv into a backend
command and handing it to Proxy.
"""
import argparse
import asyncio
from pathlib import Path

from verimcp.proxy import Proxy


def main() -> int:
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

    asyncio.run(Proxy(backend_cmd, verifier_names=verifier_names, root_override=root_override).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
