from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devmcp.connection import Connection
from devmcp.subscriptions import SubscriptionManager


@dataclass
class ServerContext:
    repo_root: Path
    connection: Connection | None
    subscriptions: SubscriptionManager = field(default_factory=SubscriptionManager)
    last_ci_run: dict[str, Any] | None = None
