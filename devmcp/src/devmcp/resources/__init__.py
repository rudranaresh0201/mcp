from devmcp.resources.base import Resource
from devmcp.resources.ci_last_run import CiLastRunResource
from devmcp.resources.repo_file import RepoFileResource
from devmcp.resources.repo_log import RepoLogResource
from devmcp.resources.repo_status import RepoStatusResource

LISTED_RESOURCES: list[Resource] = [RepoStatusResource(), RepoLogResource(), CiLastRunResource()]
ALL_RESOURCES: list[Resource] = [*LISTED_RESOURCES, RepoFileResource()]


def resolve(uri: str) -> Resource | None:
    for resource in ALL_RESOURCES:
        if resource.matches(uri):
            return resource
    return None
