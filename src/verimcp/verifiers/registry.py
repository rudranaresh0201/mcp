"""Maps a tool name to whichever verifiers apply to it."""
from verimcp.verifiers.base import Verifier
from verimcp.verifiers.filesystem import FilesystemVerifier

ALL_VERIFIERS: list[Verifier] = [
    FilesystemVerifier(),
]


def verifiers_for(tool_name: str) -> list[Verifier]:
    return [v for v in ALL_VERIFIERS if v.applies_to(tool_name)]
