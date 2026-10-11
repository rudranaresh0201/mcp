"""Play the Host against the real verimcp proxy, stateless-era (2026-07-28).

A policy says the `touch` tool needs approval. You watch four tools/call
requests go in and see what verimcp answers to each -- and for the last one
YOU are the human who approves or declines.

    python scripts/see_approval.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "tests" / "fixtures" / "stateless_server.py"

workdir = Path(tempfile.mkdtemp())
policy = workdir / "policy.yaml"
policy.write_text("rules:\n  - tool: touch\n    action: require_approval\n")
target = workdir / "made.txt"

proxy = subprocess.Popen(
    [sys.executable, "-m", "verimcp.cli", "--policy-config", str(policy), "--", sys.executable, str(BACKEND)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)


def call(id_: int, capabilities: dict, **extra) -> dict:
    params = {
        "name": "touch", "arguments": {"path": str(target)}, **extra,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": capabilities,
        },
    }
    proxy.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": params}) + "\n")
    proxy.stdin.flush()
    reply = json.loads(proxy.stdout.readline())

    # Print only the parts that differ between the four requests.
    print(f"  Host    -> tools/call touch   capabilities={json.dumps(capabilities)}", end="")
    for key, value in extra.items():
        print(f"\n             {key}={json.dumps(value)}", end="")
    print(f"\n  verimcp <- {json.dumps(reply.get('result') or reply.get('error'))}")
    print(f"  file created on disk: {target.exists()}\n")
    return reply


print("\n1) Host says it can show forms (elicitation). verimcp does NOT run the tool; it asks.\n")
first = call(1, {"elicitation": {}})
real_state = first["result"]["requestState"]

print("2) Next request declares no capabilities. Nothing is remembered from request 1.\n")
call(2, {})

print('3) A retry that just claims "accept", with a requestState verimcp never signed.\n')
call(3, {"elicitation": {}}, inputResponses={"verimcp_approval": {"action": "accept"}}, requestState="9999999999.fake")

answer = input("4) Now you are the human. Approve running `touch`? [y/n] ").strip().lower()
action = "accept" if answer.startswith("y") else "decline"
print(f"\n   Host retries the SAME call with your answer ({action}) + the real requestState from step 1.\n")
call(4, {"elicitation": {}}, inputResponses={"verimcp_approval": {"action": action}}, requestState=real_state)

proxy.terminate()
