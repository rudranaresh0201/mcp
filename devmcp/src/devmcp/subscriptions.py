from devmcp.connection import Connection


class SubscriptionManager:
    def __init__(self) -> None:
        self._subscribed: set[str] = set()

    def subscribe(self, uri: str) -> None:
        self._subscribed.add(uri)

    def unsubscribe(self, uri: str) -> None:
        self._subscribed.discard(uri)

    async def notify_changed(self, uri: str, connection: Connection | None) -> None:
        if connection is not None and uri in self._subscribed:
            await connection.send_notification("notifications/resources/updated", {"uri": uri})
