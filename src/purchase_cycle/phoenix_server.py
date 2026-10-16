"""Start a local Phoenix server on loopback only, with every feature this project does not use turned off.

`npm run phoenix` runs this file inside the Phoenix environment of `uvx --from arize-phoenix==<pinned version>`,
with the version pinned in `package.json`, so it imports nothing from this project.
Checked against that pinned version:
Phoenix binds its gRPC receiver (port 4317) on every interface and has no setting for its host,
so the launcher disables that receiver; traces go to the HTTP collector on 127.0.0.1:6006.
Phoenix mounts an unauthenticated MCP server at `/mcp` that answers any browser origin
(`Access-Control-Allow-Origin: *`), so a web page could read or delete traces; the launcher turns it off.
Code sandboxes, the AI assistant and its tools are off too.
Phoenix checks the `Origin` and `Referer` hostnames only when trusted origins are listed,
so the launcher lists the local UI and a cross-origin request gets 401.
"""

import inspect
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "phoenix"

SERVER_ENV = {
    "PHOENIX_HOST": "127.0.0.1",
    "PHOENIX_WORKING_DIR": str(DATA_DIR),
    "PHOENIX_TELEMETRY_ENABLED": "false",
    "PHOENIX_DISABLE_AGENT_ASSISTANT": "true",
    "PHOENIX_AGENTS_DISABLE_BASH": "true",
    "PHOENIX_AGENTS_DISABLE_WEB_ACCESS": "true",
    "PHOENIX_AGENTS_DISABLE_GITHUB": "true",
    "PHOENIX_ENABLE_MCP_SERVER": "false",
    "PHOENIX_ALLOWED_SANDBOX_PROVIDERS": "NONE",
    "PHOENIX_CSRF_TRUSTED_ORIGINS": "http://127.0.0.1:6006,http://localhost:6006",
}


def disable_grpc_receiver(grpc_server_class):
    """Make every instance of Phoenix's gRPC server class start disabled, or fail if the class changed."""
    original_init = grpc_server_class.__init__
    if "disabled" not in inspect.signature(original_init).parameters:
        raise SystemExit("Phoenix gRPC server has no 'disabled' option; refusing to start with port 4317 open")

    def init(self, *args, **kwargs):
        kwargs["disabled"] = True
        original_init(self, *args, **kwargs)

    grpc_server_class.__init__ = init


def main():
    os.environ.update(SERVER_ENV)
    from phoenix.server import grpc_server
    from phoenix.server.main import main as phoenix_main

    disable_grpc_receiver(grpc_server.GrpcServer)
    sys.argv = ["phoenix", "serve"]
    phoenix_main()


if __name__ == "__main__":
    main()
