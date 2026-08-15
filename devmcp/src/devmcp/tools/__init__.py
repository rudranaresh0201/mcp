from devmcp.tools.base import Tool
from devmcp.tools.git_branch import GitBranchTool
from devmcp.tools.git_commit import GitCommitTool
from devmcp.tools.run_ci_pipeline import RunCiPipelineTool
from devmcp.tools.sqlite_create_table import SqliteCreateTableTool
from devmcp.tools.sqlite_insert_row import SqliteInsertRowTool
from devmcp.tools.summarize_diff import SummarizeDiffTool
from devmcp.tools.write_file import WriteFileTool

ALL_TOOLS: list[Tool] = [
    WriteFileTool(),
    GitCommitTool(),
    GitBranchTool(),
    RunCiPipelineTool(),
    SummarizeDiffTool(),
    SqliteCreateTableTool(),
    SqliteInsertRowTool(),
]

TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}
