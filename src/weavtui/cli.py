"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
from importlib.metadata import version

from weavtui.backend import ConnectionSettings


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"weavtui: {name} must be an integer, got {raw!r}") from None


def parse_args(argv: list[str] | None = None) -> tuple[ConnectionSettings, str]:
    parser = argparse.ArgumentParser(
        prog="weavtui",
        description="Terminal UI explorer for Weaviate databases.",
        epilog="Every option can also be set with an environment variable (shown in brackets).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("WEAVTUI_HOST", "localhost"),
        help="Weaviate host (WEAVTUI_HOST, default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_int("WEAVTUI_PORT", 8080),
        help="HTTP port (WEAVTUI_PORT, default: 8080)",
    )
    parser.add_argument(
        "--grpc-port",
        type=int,
        default=_env_int("WEAVTUI_GRPC_PORT", 50051),
        help="gRPC port (WEAVTUI_GRPC_PORT, default: 50051)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("WEAVTUI_API_KEY"),
        help="API key, if the server requires one (WEAVTUI_API_KEY)",
    )
    parser.add_argument(
        "--start",
        choices=["diagram", "browse"],
        default=os.environ.get("WEAVTUI_START", "diagram"),
        help="first view after connecting: schema diagram or object browser "
        "(WEAVTUI_START, default: diagram)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('weavtui')}")
    args = parser.parse_args(argv)
    settings = ConnectionSettings(args.host, args.port, args.grpc_port, args.api_key)
    return settings, args.start


def main(argv: list[str] | None = None) -> None:
    settings, start = parse_args(argv)

    from weavtui.app import WeavtuiApp
    from weavtui.backend import WeaviateBackend

    WeavtuiApp(WeaviateBackend(settings), start=start).run()
