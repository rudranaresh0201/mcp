import asyncio
from typing import Any, ClassVar

from devmcp import docker_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class DockerRunContainerTool(Tool):
    name = "docker_run_container"
    description = "Run a docker container from an image, wait for it to exit, and report its real exit code."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "image": {"type": "string"},
            "command": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["image"],
    }
    output_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "container_id": {"type": "string"},
            "exit_code": {"type": "integer"},
        },
        "required": ["container_id", "exit_code"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        image = arguments["image"]
        command = arguments.get("command")
        try:
            # Same to_thread reasoning as docker_build_image -- docker run +
            # docker wait blocks until the container exits.
            result = await asyncio.to_thread(docker_ops.run_container, image, command)
        except docker_ops.DockerError as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"docker run failed: {exc}"}]}
        return {
            "isError": result["exit_code"] != 0,
            "content": [{"type": "text", "text": f"container {result['container_id'][:12]} exited {result['exit_code']}"}],
            "structuredContent": result,
        }
