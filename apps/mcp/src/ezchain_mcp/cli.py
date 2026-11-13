"""stdio entry point for the ezCHAIN MCP server."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .client import EzchainClient
from .credentials import Credential, inspect_credential, refusal_message
from .server import SERVER_VERSION, McpServer


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ezchain-mcp",
        description="MCP server for the ezCHAIN paper trading API (stdio).",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("EZCHAIN_API_URL", "http://127.0.0.1:8000"),
        help="ezCHAIN API base URL (default: EZCHAIN_API_URL or http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("EZCHAIN_API_TOKEN"),
        help=(
            "scoped read/trade agent key; the deployment token is refused for writes "
            "(default: EZCHAIN_API_TOKEN)"
        ),
    )
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        default=_truthy(os.environ.get("EZCHAIN_MCP_ALLOW_WRITES")),
        help="expose mutation tools (default: EZCHAIN_MCP_ALLOW_WRITES)",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="force read-only even when EZCHAIN_MCP_ALLOW_WRITES is set",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {SERVER_VERSION}")
    return parser


def resolve_write_access(
    client: EzchainClient,
    token: str | None,
    requested: bool,
) -> Credential:
    """Verify the credential once, at startup.

    The server re-checks the returned credential itself, so a caller cannot bypass this
    probe by constructing ``McpServer`` directly.
    """
    if not requested:
        return Credential("missing", "writes were not requested")
    return inspect_credential(client, token)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = EzchainClient(args.api_url, args.token)
    requested = bool(args.allow_writes) and not args.read_only
    credential = resolve_write_access(client, args.token, requested)
    allow_writes = credential.may_write
    write_refusal = None if allow_writes or not requested else refusal_message(credential)
    if write_refusal is not None:
        print(f"ezchain-mcp: {write_refusal}", file=sys.stderr)
    server = McpServer(
        client, allow_writes=allow_writes, credential=credential, write_refusal=write_refusal
    )
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message: Any = json.loads(line)
            except json.JSONDecodeError as exc:
                response: Any = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {exc.msg}"},
                }
            else:
                response = server.handle(message)
            if response is None:
                continue
            sys.stdout.write(json.dumps(response, separators=(",", ":"), default=str) + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130
    finally:
        client.close()
    return 0


__all__ = ["build_parser", "main", "resolve_write_access"]
