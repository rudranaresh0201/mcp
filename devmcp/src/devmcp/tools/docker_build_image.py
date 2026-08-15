import asyncio
from typing import Any, ClassVar

from devmcp import docker_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class DockerBuildImageTool(Tool):
    name = "docker_build_image"
    description = "Build a docker image from a Dockerfile in the repo."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "dockerfile_path": {"type": "string"},
            "context_path": {"type": "string"},
            "tag": {"type": "string"},
        },
        "required": ["dockerfile_path", "context_path", "tag"],
    }
    output_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tag": {"type": "string"},
            "dockerfile_path": {"type": "string"},
            "context_path": {"type": "string"},
        },
        "required": ["tag", "dockerfile_path", "context_path"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        dockerfile_path = arguments["dockerfile_path"]
        context_path = arguments["context_path"]
        tag = arguments["tag"]
        try:
            # A real image build can run long -- to_thread so it doesn't
            # stall the single dispatch loop, same reasoning
            # run_ci_pipeline.py already states for its own subprocess call.
            result = await asyncio.to_thread(docker_ops.build_image, ctx.repo_root, dockerfile_path, context_path, tag)
        except ValueError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except docker_ops.DockerError as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"docker build failed: {exc}"}]}
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"built image {tag!r}"}],
            "structuredContent": result,
        }
