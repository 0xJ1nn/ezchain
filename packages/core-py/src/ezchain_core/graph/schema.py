"""The graph document shape (``ezchain.graph/1``).

A graph strategy is typed JSON: nodes, edges and (ignored by the evaluator) UI hints.
This module only enforces *shape*: types, limits, id syntax and references that must
exist. Port and parameter semantics live in :mod:`ezchain_core.graph.validate`, because
they need the catalogue.

Everything here fails closed: a malformed document raises :class:`GraphSchemaError`
(code ``GRAPH_SCHEMA``) instead of being coerced into something plausible.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

GRAPH_SCHEMA = "ezchain.graph/1"

MAX_NODES = 200
MAX_EDGES = 400
MAX_COMMENTS = 50
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_ID_LENGTH = 40

_ID_RE = re.compile(r"\A[A-Za-z0-9_-]{1," + str(MAX_ID_LENGTH) + r"}\Z")


class GraphSchemaError(ValueError):
    """The document does not have the ``ezchain.graph/1`` shape.

    ``code`` is always ``GRAPH_SCHEMA`` so callers can map it to one issue code without
    string matching the message.
    """

    code = "GRAPH_SCHEMA"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphSchemaError(f"{what} must be an object")
    return value


def _sequence(value: object, what: str) -> Sequence[Any]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise GraphSchemaError(f"{what} must be an array")
    return value


def _identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise GraphSchemaError(
            f"{what} must be 1-{MAX_ID_LENGTH} characters of A-Z, a-z, 0-9, '_' or '-'"
        )
    return value


def _port_name(value: object, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH:
        raise GraphSchemaError(f"{what} must be a non-empty port name")
    return value


@dataclass(frozen=True)
class PortRef:
    """One end of a wire: a node id plus one of that node's port names."""

    node: str
    port: str

    def to_dict(self) -> dict[str, str]:
        return {"node": self.node, "port": self.port}


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    params: Mapping[str, Any] = field(default_factory=dict)
    ui: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    from_: PortRef
    to: PortRef

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_.to_dict(), "to": self.to.to_dict()}


@dataclass(frozen=True)
class Comment:
    id: str
    text: str
    ui: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphDocument:
    """An immutable, shape-valid graph document.

    ``ui`` and comments are presentation only; nothing in the evaluator reads them.
    """

    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    comments: tuple[Comment, ...] = ()
    schema: str = GRAPH_SCHEMA

    def node(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def index(self) -> dict[str, Node]:
        """Node lookup by id. Built on demand (documents are small and frozen)."""
        return {node.id: node for node in self.nodes}

    def to_obj(self) -> dict[str, Any]:
        """The JSON form, key-for-key with the documented shape."""
        return {
            "schema": self.schema,
            "nodes": [
                {"id": n.id, "type": n.type, "params": dict(n.params), "ui": dict(n.ui)}
                for n in self.nodes
            ],
            "edges": [e.to_dict() for e in self.edges],
            "comments": [{"id": c.id, "text": c.text, "ui": dict(c.ui)} for c in self.comments],
        }


def _parse_node(raw: object, seen: set[str]) -> Node:
    obj = _mapping(raw, "node")
    node_id = _identifier(obj.get("id"), "node id")
    if node_id in seen:
        raise GraphSchemaError(f"duplicate node id: {node_id}")
    seen.add(node_id)

    node_type = obj.get("type")
    if not isinstance(node_type, str) or not node_type or len(node_type) > 80:
        raise GraphSchemaError(f"node {node_id}: type must be a non-empty string")

    params = obj.get("params", {})
    if not isinstance(params, Mapping):
        raise GraphSchemaError(f"node {node_id}: params must be an object")

    ui = obj.get("ui", {})
    if not isinstance(ui, Mapping):
        raise GraphSchemaError(f"node {node_id}: ui must be an object")

    return Node(id=node_id, type=node_type, params=dict(params), ui=dict(ui))


def _parse_port_ref(raw: object, what: str) -> PortRef:
    obj = _mapping(raw, what)
    return PortRef(
        node=_identifier(obj.get("node"), f"{what}.node"),
        port=_port_name(obj.get("port"), f"{what}.port"),
    )


def _parse_comment(raw: object, seen: set[str]) -> Comment:
    obj = _mapping(raw, "comment")
    comment_id = _identifier(obj.get("id"), "comment id")
    if comment_id in seen:
        raise GraphSchemaError(f"duplicate comment id: {comment_id}")
    seen.add(comment_id)
    text = obj.get("text", "")
    if not isinstance(text, str) or len(text) > 2_000:
        raise GraphSchemaError(
            f"comment {comment_id}: text must be a string of at most 2000 characters"
        )
    ui = obj.get("ui", {})
    if not isinstance(ui, Mapping):
        raise GraphSchemaError(f"comment {comment_id}: ui must be an object")
    return Comment(id=comment_id, text=text, ui=dict(ui))


def _check_size(obj: object) -> None:
    """Reject documents that are not JSON or are larger than the documented cap."""
    try:
        encoded = json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GraphSchemaError("document must be JSON-serialisable (no NaN or Infinity)") from exc
    size = len(encoded.encode("utf-8"))
    if size > MAX_DOCUMENT_BYTES:
        raise GraphSchemaError(f"document is {size} bytes; the limit is {MAX_DOCUMENT_BYTES} bytes")


def parse_document(obj: object) -> GraphDocument:
    """Parse and shape-check a document. Raises :class:`GraphSchemaError`."""
    if isinstance(obj, GraphDocument):
        return obj
    _check_size(obj)
    root = _mapping(obj, "document")

    declared = root.get("schema", GRAPH_SCHEMA)
    if declared != GRAPH_SCHEMA:
        raise GraphSchemaError(f"schema must be {GRAPH_SCHEMA!r}, got {declared!r}")

    raw_nodes = _sequence(root.get("nodes", ()), "nodes")
    if not raw_nodes:
        raise GraphSchemaError("nodes must not be empty")
    if len(raw_nodes) > MAX_NODES:
        raise GraphSchemaError(f"nodes is {len(raw_nodes)}; the limit is {MAX_NODES}")

    raw_edges = _sequence(root.get("edges", ()), "edges")
    if len(raw_edges) > MAX_EDGES:
        raise GraphSchemaError(f"edges is {len(raw_edges)}; the limit is {MAX_EDGES}")

    raw_comments = _sequence(root.get("comments", ()), "comments")
    if len(raw_comments) > MAX_COMMENTS:
        raise GraphSchemaError(f"comments is {len(raw_comments)}; the limit is {MAX_COMMENTS}")

    node_ids: set[str] = set()
    nodes = tuple(_parse_node(raw, node_ids) for raw in raw_nodes)

    edges: list[Edge] = []
    for raw in raw_edges:
        edge = Edge(
            from_=_parse_port_ref(_mapping(raw, "edge").get("from"), "edge.from"),
            to=_parse_port_ref(_mapping(raw, "edge").get("to"), "edge.to"),
        )
        for ref in (edge.from_, edge.to):
            if ref.node not in node_ids:
                raise GraphSchemaError(f"edge references unknown node: {ref.node}")
        if edge.from_ == edge.to:
            raise GraphSchemaError(f"edge connects node {edge.from_.node} to itself")
        edges.append(edge)

    comment_ids: set[str] = set()
    comments = tuple(_parse_comment(raw, comment_ids) for raw in raw_comments)

    return GraphDocument(nodes=nodes, edges=tuple(edges), comments=comments)


__all__ = [
    "GRAPH_SCHEMA",
    "MAX_COMMENTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_EDGES",
    "MAX_NODES",
    "Comment",
    "Edge",
    "GraphDocument",
    "GraphSchemaError",
    "Node",
    "PortRef",
    "parse_document",
]
