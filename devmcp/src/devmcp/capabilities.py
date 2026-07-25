"""What devmcp declares it supports in the initialize handshake.
roots/sampling aren't declared here -- devmcp *uses* those (asks the client
for them), it doesn't offer them as a capability of its own."""

SERVER_CAPABILITIES: dict = {
    "tools": {},
    "resources": {"subscribe": True},
    "prompts": {},
}

SERVER_INFO = {"name": "devmcp", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-06-18"
