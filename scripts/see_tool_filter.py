"""The same backend (devmcp), asked for its tools by two differently-launched
verimcp proxies: one with no role, one launched as `--role intern`.

Then the intern-side Host tries to call a tool it was never shown.

    python scripts/see_tool_filter.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

workdir = Path(tempfile.mkdtemp())
policy = workdir / "policy.yaml"
policy.write_text(
    "roles:\n"
    "  intern:\n"
    "    tools: [summarize_diff, git_branch]\n"
    "  maintainer:\n"
    "    tools: ['*']\n"
)


def launch(*verimcp_args: str) -> subprocess.Popen:
    proxy = subprocess.Popen(
        [sys.executable, "-m", "verimcp.cli", *verimcp_args, "--",
         sys.executable, "-m", "devmcp.cli", "--repo-path", str(workdir)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    ask(proxy, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "see-tool-filter", "version": "0"}}})
    proxy.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proxy.stdin.flush()
    return proxy


def ask(proxy: subprocess.Popen, request: dict) -> dict:
    proxy.stdin.write(json.dumps(request) + "\n")
    proxy.stdin.flush()
    while True:
        reply = json.loads(proxy.stdout.readline())
        if reply.get("id") == request["id"] and "method" not in reply:
            return reply


def tool_names(proxy: subprocess.Popen) -> list[str]:
    reply = ask(proxy, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [tool["name"] for tool in reply["result"]["tools"]]


plain = launch()
intern = launch("--policy-config", str(policy), "--role", "intern")

everything = tool_names(plain)
shown = tool_names(intern)
print(f"\nverimcp with no role   -> Host sees {len(everything)} tools:\n  {everything}")
print(f"\nverimcp --role intern  -> Host sees {len(shown)} tools:\n  {shown}")
print(f"\nhidden from the intern's model: {[name for name in everything if name not in shown]}")

call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "write_file", "arguments": {"path": "pwned.txt", "content": "x"}}}
print("\nThe intern-side Host calls write_file anyway (it guessed the name):")
print(f"  verimcp <- {json.dumps(ask(intern, call).get('error'))}")
print(f"  pwned.txt written: {(workdir / 'pwned.txt').exists()}")

call["params"]["name"] = "no_such_tool_at_all"
print("\nFor comparison, a tool that truly does not exist, asked of the no-role proxy:")
reply = ask(plain, call)
print(f"  backend <- {json.dumps(reply.get('error') or reply.get('result'))}\n")

plain.terminate()
intern.terminate()
