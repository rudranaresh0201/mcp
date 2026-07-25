"""Server-initiated roots negotiation: devmcp asks the Client what
filesystem roots it's allowed to touch, instead of blindly trusting its own
--repo-path flag. If the Client doesn't answer with a usable root, the
caller keeps whatever fallback it started with."""
from pathlib import Path
from typing import Any

from devmcp.connection import Connection

FILE_URI_PREFIX = "file://"


class RootsClient:
    async def negotiate(self, connection: Connection) -> Path | None:
        try:
            result: dict[str, Any] = await connection.request("roots/list", {})
        except Exception:  # noqa: BLE001 -- a Client that can't/won't answer just means "use the fallback"
            return None
        for root in result.get("roots", []):
            uri = root.get("uri", "")
            if uri.startswith(FILE_URI_PREFIX):
                return Path(uri[len(FILE_URI_PREFIX):]).resolve()
        return None
