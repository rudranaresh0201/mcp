"""Entry point for the devmcp server."""
import argparse
import asyncio
import sys
from pathlib import Path

from devmcp import git_ops
from devmcp.connection import Connection
from devmcp.server import Server


class _StdinReader:
    """Reads our own real stdin without asyncio's pipe transports -- same
    Windows limitation as _StdoutWriter below, mirrored for the read side.
    The blocking read runs in a worker thread so it doesn't block the loop."""

    async def readline(self) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, sys.stdin.buffer.readline)


class _StdoutWriter:
    """Synchronous fallback for writing to our own real stdout. Windows'
    asyncio event loop can't reliably wrap a subprocess-redirected stdout via
    connect_write_pipe -- the same reason verimcp.jsonrpc.write_message_sync
    writes directly instead of going through an async stream."""

    def write(self, data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    async def drain(self) -> None:
        return None


async def _stdio_connection() -> Connection:
    """Wire devmcp's own real stdin/stdout up as a Connection, the same way
    a Host would see any other MCP server it launches as a subprocess."""
    return Connection(_StdinReader(), _StdoutWriter())


async def _run(repo_path: Path) -> None:
    git_ops.ensure_repo(repo_path)
    connection = await _stdio_connection()
    server = Server(connection, repo_root=repo_path)
    await server.run()


def main() -> int:
    parser = argparse.ArgumentParser(prog="devmcp")
    parser.add_argument("--repo-path", required=True, help="Local git repo this server operates on")
    args = parser.parse_args()

    repo_path = Path(args.repo_path).resolve()
    repo_path.mkdir(parents=True, exist_ok=True)

    asyncio.run(_run(repo_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
