import asyncio
import subprocess
from typing import Any, ClassVar

from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class RunCiPipelineTool(Tool):
    name = "run_ci_pipeline"
    description = "Run a sequence of shell steps in the repo; report pass/fail per step."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "cmd": {"type": "string"},
                        "idempotent": {"type": "boolean"},
                    },
                    "required": ["name", "cmd"],
                },
            }
        },
        "required": ["steps"],
    }
    output_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "cmd": {"type": "string"},
                        "idempotent": {"type": "boolean"},
                        "exit_code": {"type": "integer"},
                        "passed": {"type": "boolean"},
                        "stdout_tail": {"type": "string"},
                        "stderr_tail": {"type": "string"},
                    },
                    "required": ["name", "cmd", "exit_code", "passed"],
                },
            },
            "passed": {"type": "boolean"},
        },
        "required": ["steps", "passed"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        results = []
        overall_ok = True
        for step in arguments["steps"]:
            # blocking subprocess call -- must not run on the event loop directly,
            # or every other in-flight request would stall for the step's duration.
            # stdin=DEVNULL: same reason as git_ops._run -- devmcp's own real stdin
            # is read on a background thread, and an inheriting child can deadlock
            # against it on Windows, now that tool calls run concurrently with the
            # main dispatch loop's next read.
            proc = await asyncio.to_thread(
                subprocess.run,
                step["cmd"], shell=True, cwd=ctx.repo_root, capture_output=True, text=True, check=False,
                stdin=subprocess.DEVNULL,
            )
            passed = proc.returncode == 0
            overall_ok = overall_ok and passed
            results.append(
                {
                    "name": step["name"],
                    "cmd": step["cmd"],
                    "idempotent": step.get("idempotent", False),
                    "exit_code": proc.returncode,
                    "passed": passed,
                    "stdout_tail": proc.stdout[-500:],
                    "stderr_tail": proc.stderr[-500:],
                }
            )
        summary = "\n".join(
            f"{r['name']}: {'PASS' if r['passed'] else 'FAIL'} (exit {r['exit_code']})" for r in results
        )
        structured = {"steps": results, "passed": overall_ok}
        ctx.last_ci_run = structured
        await ctx.subscriptions.notify_changed("ci://last-run", ctx.connection)
        return {
            "isError": not overall_ok,
            "content": [{"type": "text", "text": summary}],
            "structuredContent": structured,
        }
