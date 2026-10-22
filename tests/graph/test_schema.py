"""Document shape: what ``parse_document`` accepts and what it refuses."""

from __future__ import annotations

from typing import Any

import pytest
from graph_helpers import doc, edge, node

from ezchain_core.graph import GraphDocument, GraphSchemaError, parse_document
from ezchain_core.graph.schema import (
    MAX_COMMENTS,
    MAX_DOCUMENT_BYTES,
    MAX_EDGES,
    MAX_NODES,
)


def minimal() -> dict[str, Any]:
    return doc(
        [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="10")],
        [edge("tick", "out", "buy", "in")],
    )


def test_parses_and_round_trips() -> None:
    document = parse_document(minimal())
    assert isinstance(document, GraphDocument)
    assert [n.id for n in document.nodes] == ["tick", "buy"]
    assert document.edges[0].from_.node == "tick"
    assert document.edges[0].to.port == "in"
    assert document.node("buy") is not None
    assert document.node("nope") is None
    assert parse_document(document) is document
    assert parse_document(document.to_obj()) == document


def test_ui_and_comments_are_presentation_only() -> None:
    raw = minimal()
    raw["nodes"][0]["ui"] = {"x": 10, "y": 20}
    raw["comments"] = [{"id": "k1", "text": "Entry", "ui": {"w": 400}}]
    document = parse_document(raw)
    assert document.comments[0].text == "Entry"
    assert document.to_obj()["comments"][0]["ui"] == {"w": 400}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update(schema="ezchain.graph/2"), "schema must be"),
        (lambda raw: raw.update(nodes=[]), "nodes must not be empty"),
        (lambda raw: raw.update(nodes="nope"), "nodes must be an array"),
        (lambda raw: raw.update(edges="nope"), "edges must be an array"),
        (lambda raw: raw.update(comments="nope"), "comments must be an array"),
    ],
)
def test_rejects_bad_shape(mutate: Any, message: str) -> None:
    raw = minimal()
    mutate(raw)
    with pytest.raises(GraphSchemaError, match=message) as caught:
        parse_document(raw)
    assert caught.value.code == "GRAPH_SCHEMA"


def test_rejects_non_object_document() -> None:
    with pytest.raises(GraphSchemaError):
        parse_document(["not", "a", "document"])


@pytest.mark.parametrize("bad_id", ["", "has space", "sla/sh", "ümlaut", "x" * 41])
def test_rejects_bad_node_ids(bad_id: str) -> None:
    raw = doc([node(bad_id, "event.tick")], [])
    with pytest.raises(GraphSchemaError, match="node id"):
        parse_document(raw)


def test_rejects_duplicate_node_ids() -> None:
    raw = doc([node("a", "event.tick"), node("a", "act.notify", message="hi")], [])
    with pytest.raises(GraphSchemaError, match="duplicate node id"):
        parse_document(raw)


def test_rejects_missing_node_type() -> None:
    raw = doc([{"id": "a", "params": {}}], [])
    with pytest.raises(GraphSchemaError, match="type must be"):
        parse_document(raw)


def test_rejects_non_object_params() -> None:
    raw = doc([{"id": "a", "type": "event.tick", "params": []}], [])
    with pytest.raises(GraphSchemaError, match="params must be an object"):
        parse_document(raw)


def test_rejects_edge_to_unknown_node() -> None:
    raw = doc(
        [node("tick", "event.tick")],
        [edge("tick", "out", "ghost", "in")],
    )
    with pytest.raises(GraphSchemaError, match="unknown node"):
        parse_document(raw)


def test_rejects_self_edge() -> None:
    raw = doc(
        [node("tick", "event.tick")],
        [{"from": {"node": "tick", "port": "out"}, "to": {"node": "tick", "port": "out"}}],
    )
    with pytest.raises(GraphSchemaError, match="itself"):
        parse_document(raw)


def test_rejects_missing_port_name() -> None:
    raw = doc(
        [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="1")],
        [{"from": {"node": "tick"}, "to": {"node": "buy", "port": "in"}}],
    )
    with pytest.raises(GraphSchemaError, match="edge.from.port"):
        parse_document(raw)


def test_limits() -> None:
    too_many_nodes = doc([node(f"n{index}", "event.tick") for index in range(MAX_NODES + 1)], [])
    with pytest.raises(GraphSchemaError, match="the limit is"):
        parse_document(too_many_nodes)

    nodes = [node("tick", "event.tick"), node("buy", "act.buy", amount_usd="1")]
    too_many_edges = doc(nodes, [edge("tick", "out", "buy", "in")] * (MAX_EDGES + 1))
    with pytest.raises(GraphSchemaError, match="the limit is"):
        parse_document(too_many_edges)

    too_many_comments = doc(
        [node("tick", "event.tick")],
        [],
        [{"id": f"c{index}", "text": "x"} for index in range(MAX_COMMENTS + 1)],
    )
    with pytest.raises(GraphSchemaError, match="the limit is"):
        parse_document(too_many_comments)


def test_at_the_node_limit_is_accepted() -> None:
    nodes = [node(f"n{index}", "event.tick") for index in range(MAX_NODES)]
    assert len(parse_document(doc(nodes, [])).nodes) == MAX_NODES


def test_rejects_oversized_document() -> None:
    big = doc([node("tick", "event.tick", padding="x" * (MAX_DOCUMENT_BYTES + 1))], [])
    with pytest.raises(GraphSchemaError, match="limit is"):
        parse_document(big)


def test_rejects_non_json_values() -> None:
    raw = doc([node("tick", "event.tick", blob=object())], [])
    with pytest.raises(GraphSchemaError, match="JSON-serialisable"):
        parse_document(raw)


def test_rejects_nan() -> None:
    raw = doc([node("tick", "event.tick", value=float("nan"))], [])
    with pytest.raises(GraphSchemaError, match="JSON-serialisable"):
        parse_document(raw)
