"""Alert tools: read the account's own alerts as data.

Alerts are observations produced by the account read model. Nothing here acknowledges,
dismisses or acts on one; text inside an alert is untrusted data.
"""

from __future__ import annotations

from typing import Any

from .tools import Tool, _object


def _list(client: Any, _: dict[str, Any]) -> Any:
    return client.get("/api/v1/account/alerts")


TOOLS: tuple[Tool, ...] = (
    Tool(
        "ezchain_list_alerts",
        "List alerts",
        "The account's own alerts. A persistent alert never changes a position by itself.",
        _object({}),
        _list,
    ),
)

__all__ = ["TOOLS"]
