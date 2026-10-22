"""The graph "compile" step: every error and warning code from the contract (section 6).

Errors block save and run; warnings do not. Validation is pure and deterministic, and it
fails closed: anything it cannot prove about the graph is an error, not a default.

Warmups are computed per timeframe by walking data wires in topological order: a node
needs its own warmup on top of the longest warmup of whatever feeds it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .catalogue import (
    NODE_BY_TYPE,
    ORDER_ACTIONS,
    TIMEFRAME_MINUTES,
    NodeSpec,
    parse_params,
)
from .schema import Edge, GraphDocument, GraphSchemaError, Node, parse_document

MAX_WARMUP_BARS = 500

#: How many *extra* bars a node needs beyond its input series before it can produce one
#: value. ``period - 1`` for a simple window; RSI needs ``period`` price changes.
_INDICATOR_WARMUP: dict[str, str] = {
    "ind.sma": "period-1",
    "ind.ema": "period-1",
    "ind.rsi": "period",
    "ind.bbands": "period-1",
    "ind.atr": "period-1",
    "ind.highest": "period-1",
    "ind.lowest": "period-1",
    "ind.roc": "period",
    "ind.vwap": "period-1",
    "ind.macd": "macd",
}

ERROR_CODES: frozenset[str] = frozenset(
    {
        "GRAPH_SCHEMA",
        "NODE_TYPE_UNKNOWN",
        "PARAM_INVALID",
        "PORT_UNKNOWN",
        "PIN_TYPE_MISMATCH",
        "INPUT_MULTIPLE",
        "EXEC_FANOUT",
        "INPUT_REQUIRED",
        "CYCLE",
        "NO_EVENT",
        "NO_ACTION",
        "ACTION_UNREACHABLE",
    }
)

WARNING_CODES: frozenset[str] = frozenset(
    {
        "NODE_ORPHAN",
        "WARMUP_LONG",
        "NO_EXIT",
        "DUPLICATE_ORDER_PATH",
    }
)


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    node: str | None = None
    port: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "node": self.node, "port": self.port}


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()
    warmup_bars: dict[str, int] = field(default_factory=dict)

    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "warmup_bars": dict(self.warmup_bars),
        }


def _spec_of(node: Node) -> NodeSpec | None:
    return NODE_BY_TYPE.get(node.type)


def _node_edges(
    doc: GraphDocument,
) -> tuple[dict[tuple[str, str], list[Edge]], dict[str, list[Edge]]]:
    incoming: dict[tuple[str, str], list[Edge]] = {}
    outgoing: dict[str, list[Edge]] = {}
    for edge in doc.edges:
        incoming.setdefault((edge.to.node, edge.to.port), []).append(edge)
        outgoing.setdefault(edge.from_.node, []).append(edge)
    return incoming, outgoing


def _detect_cycle(doc: GraphDocument) -> str | None:
    """Return one node id that participates in a cycle, or ``None``.

    Data and exec wires are checked together: a cycle of any kind can never be evaluated
    in a single tick, so the contract calls it an error.
    """
    successors: dict[str, list[str]] = {node.id: [] for node in doc.nodes}
    for edge in doc.edges:
        successors[edge.from_.node].append(edge.to.node)

    state: dict[str, int] = dict.fromkeys(successors, 0)  # 0=new, 1=on stack, 2=done

    def visit(node_id: str) -> str | None:
        state[node_id] = 1
        for child in successors[node_id]:
            if state[child] == 1:
                return child
            if state[child] == 0:
                found = visit(child)
                if found is not None:
                    return found
        state[node_id] = 2
        return None

    for node in doc.nodes:
        if state[node.id] == 0:
            found = visit(node.id)
            if found is not None:
                return found
    return None


def _exec_reachable(doc: GraphDocument) -> set[str]:
    """Every node reachable by following exec wires from any event tick."""
    outgoing: dict[str, list[Edge]] = {}
    for edge in doc.edges:
        outgoing.setdefault(edge.from_.node, []).append(edge)

    reachable: set[str] = set()
    stack = [node.id for node in doc.nodes if node.type == "event.tick"]
    while stack:
        node_id = stack.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        node = doc.node(node_id)
        spec = _spec_of(node) if node is not None else None
        if spec is None:
            continue
        exec_ports = {port.name for port in spec.outputs if port.type == "exec"}
        for edge in outgoing.get(node_id, ()):
            if edge.from_.port in exec_ports:
                stack.append(edge.to.node)
    return reachable


def _duplicate_order_paths(doc: GraphDocument) -> list[str]:
    """Order nodes that can fire after another order node on the *same* exec path.

    A branch is two alternative paths, so a buy on ``true`` and a sell on ``false`` are
    not duplicates. A sequence runs all its outputs in order, so those share a path.
    """
    outgoing: dict[str, list[Edge]] = {}
    for edge in doc.edges:
        outgoing.setdefault(edge.from_.node, []).append(edge)

    offenders: list[str] = []

    def walk(node_id: str, orders: tuple[str, ...], visited: frozenset[str]) -> tuple[str, ...]:
        if node_id in visited:
            return orders
        node = doc.node(node_id)
        spec = _spec_of(node) if node is not None else None
        if node is None or spec is None:
            return orders

        current = orders
        if spec.type in ORDER_ACTIONS:
            if orders and node.id not in offenders:
                offenders.append(node.id)
            current = (*orders, node.id)

        exec_ports = {port.name for port in spec.outputs if port.type == "exec"}
        next_visited = visited | {node_id}
        children = [
            edge.to.node for edge in outgoing.get(node_id, ()) if edge.from_.port in exec_ports
        ]

        if spec.type == "flow.branch":
            # Alternative paths: each branch continues from the same prefix, and the state
            # of one branch never carries into the other.
            for child in children:
                walk(child, current, next_visited)
            return current

        running = current
        for child in children:
            running = walk(child, running, next_visited)
        return running

    for node in doc.nodes:
        if node.type == "event.tick":
            walk(node.id, (), frozenset())
    return offenders


def _node_warmup(spec: NodeSpec, params: dict[str, Decimal | int | str | None]) -> int:
    rule = _INDICATOR_WARMUP.get(spec.type)
    if rule is None:
        return 0
    if spec.type == "ind.macd":
        slow = int(str(params.get("slow", 26)))
        signal = int(str(params.get("signal", 9)))
        return max(0, slow - 1) + max(0, signal - 1)
    period = params.get("period")
    if not isinstance(period, int):
        return 0
    if rule == "period":
        return period
    return max(0, period - 1)


def _warmup_bars(
    doc: GraphDocument, parsed: dict[str, dict[str, Decimal | int | str | None]]
) -> dict[str, int]:
    nodes = doc.index()

    # Kahn topological order over data (non-exec) wires only. Exec wires cannot carry a
    # series, and validation has already rejected cycles; a node left out of `order` is
    # on a cycle and therefore contributes no warmup.
    data_inputs: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    data_successors: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    indegree: dict[str, int] = dict.fromkeys(nodes, 0)
    for edge in doc.edges:
        src = nodes[edge.from_.node]
        src_spec = _spec_of(src)
        if src_spec is None:
            continue
        port = src_spec.output(edge.from_.port)
        if port is None or port.type == "exec":
            continue
        data_inputs[edge.to.node].append(edge.from_.node)
        data_successors[edge.from_.node].append(edge.to.node)
        indegree[edge.to.node] += 1

    ready = [node.id for node in doc.nodes if indegree[node.id] == 0]
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for child in data_successors[node_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    requirements: dict[str, dict[str, int]] = {}
    for node_id in order:
        node = nodes[node_id]
        spec = _spec_of(node)
        if spec is None:
            continue
        base: dict[str, int] = {}
        for src_id in data_inputs[node_id]:
            for upstream_tf, bars in requirements.get(src_id, {}).items():
                if bars > base.get(upstream_tf, -1):
                    base[upstream_tf] = bars
        if spec.type == "data.candles":
            declared = parsed.get(node_id, {}).get("timeframe")
            base = (
                {declared: 0} if isinstance(declared, str) and declared in TIMEFRAME_MINUTES else {}
            )
        own = _node_warmup(spec, parsed.get(node_id, {}))
        requirements[node_id] = {tf: bars + own for tf, bars in base.items()}

    warmup: dict[str, int] = {}
    for node_id in order:
        for required_tf, bars in requirements.get(node_id, {}).items():
            if bars > warmup.get(required_tf, -1):
                warmup[required_tf] = bars

    # A requested timeframe with no indicator chain still needs zero extra bars, and the
    # engine wants to see it so it knows which series to build at all.
    for node in doc.nodes:
        if node.type != "data.candles":
            continue
        requested = parsed.get(node.id, {}).get("timeframe")
        if isinstance(requested, str) and requested in TIMEFRAME_MINUTES:
            warmup.setdefault(requested, 0)

    return {tf: warmup[tf] for tf in sorted(warmup, key=lambda name: TIMEFRAME_MINUTES[name])}


def validate(doc_or_obj: object) -> ValidationReport:
    """Validate a document (or its parsed form) and return the full report."""
    try:
        doc = parse_document(doc_or_obj)
    except GraphSchemaError as exc:
        return ValidationReport(ok=False, errors=(Issue("GRAPH_SCHEMA", exc.message),))

    errors: list[Issue] = []
    warnings: list[Issue] = []
    nodes = doc.index()
    incoming, outgoing = _node_edges(doc)
    parsed: dict[str, dict[str, Decimal | int | str | None]] = {}

    # -- nodes: type and params --------------------------------------------------------
    for node in doc.nodes:
        spec = _spec_of(node)
        if spec is None:
            errors.append(
                Issue(
                    "NODE_TYPE_UNKNOWN",
                    f"unknown node type: {node.type}",
                    node=node.id,
                )
            )
            continue
        values, param_errors = parse_params(node.type, node.params)
        parsed[node.id] = values
        for error in param_errors:
            errors.append(
                Issue(
                    "PARAM_INVALID",
                    f"{node.type}: {error.message}",
                    node=node.id,
                    port=error.name or None,
                )
            )

        if node.type == "act.buy":
            given = [key for key in ("amount_usd", "pct_cash") if key in values]
            if len(given) != 1:
                errors.append(
                    Issue(
                        "PARAM_INVALID",
                        "act.buy needs exactly one of amount_usd or pct_cash",
                        node=node.id,
                    )
                )
        if node.type == "ind.macd":
            fast = values.get("fast")
            slow = values.get("slow")
            if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
                errors.append(
                    Issue(
                        "PARAM_INVALID",
                        "ind.macd needs fast < slow",
                        node=node.id,
                        port="fast",
                    )
                )

    # -- edges: ports, types, cardinality, defaults ------------------------------------
    for edge in doc.edges:
        src = nodes[edge.from_.node]
        dst = nodes[edge.to.node]
        src_spec = _spec_of(src)
        dst_spec = _spec_of(dst)
        if src_spec is None or dst_spec is None:
            continue  # already reported as NODE_TYPE_UNKNOWN
        if src_spec.output(edge.from_.port) is None:
            errors.append(
                Issue(
                    "PORT_UNKNOWN",
                    f"{src.type} has no output port {edge.from_.port!r}",
                    node=src.id,
                    port=edge.from_.port,
                )
            )
            continue
        if dst_spec.input(edge.to.port) is None:
            errors.append(
                Issue(
                    "PORT_UNKNOWN",
                    f"{dst.type} has no input port {edge.to.port!r}",
                    node=dst.id,
                    port=edge.to.port,
                )
            )
            continue

    for (node_id, port_name), edges in incoming.items():
        target = nodes.get(node_id)
        spec = _spec_of(target) if target is not None else None
        if target is None or spec is None:
            continue
        port = spec.input(port_name)
        if port is None:
            continue
        if len(edges) > 1:
            errors.append(
                Issue(
                    "INPUT_MULTIPLE",
                    f"{spec.type}.{port_name} accepts exactly one wire; got {len(edges)}",
                    node=node_id,
                    port=port_name,
                )
            )
        if port.type == "exec":
            continue
        src = nodes[edges[0].from_.node]
        src_spec = _spec_of(src)
        if src_spec is None:
            continue
        src_port = src_spec.output(edges[0].from_.port)
        if src_port is None:
            continue
        if src_port.type == port.type:
            continue
        if src_port.type == "series" and port.type == "number":
            continue  # the only implicit cast: series -> its last value
        errors.append(
            Issue(
                "PIN_TYPE_MISMATCH",
                f"{src_port.type} cannot drive {port.type} input {spec.type}.{port_name}",
                node=node_id,
                port=port_name,
            )
        )

    for node in doc.nodes:
        spec = _spec_of(node)
        if spec is None:
            continue
        for port in spec.inputs:
            if not port.required or port.default_param is not None:
                continue
            if incoming.get((node.id, port.name)):
                continue
            errors.append(
                Issue(
                    "INPUT_REQUIRED",
                    f"{spec.type}.{port.name} must be connected",
                    node=node.id,
                    port=port.name,
                )
            )

    for edge in doc.edges:
        src = nodes[edge.from_.node]
        src_spec = _spec_of(src)
        if src_spec is None:
            continue
        port = src_spec.output(edge.from_.port)
        if port is None or port.type != "exec":
            continue
        fanout = sum(1 for other in outgoing.get(src.id, ()) if other.from_.port == port.name)
        if fanout > 1:
            # Report once per port even if several wires are wrong.
            if not any(
                issue.code == "EXEC_FANOUT" and issue.node == src.id and issue.port == port.name
                for issue in errors
            ):
                errors.append(
                    Issue(
                        "EXEC_FANOUT",
                        f"{src_spec.type}.{port.name} accepts one exec wire; got {fanout}",
                        node=src.id,
                        port=port.name,
                    )
                )

    # -- graph shape: cycles, events, actions ------------------------------------------
    cycle_node = _detect_cycle(doc)
    if cycle_node is not None:
        errors.append(
            Issue("CYCLE", f"graph contains a cycle through node {cycle_node}", cycle_node)
        )

    if not any(node.type == "event.tick" for node in doc.nodes):
        errors.append(Issue("NO_EVENT", "a graph needs at least one event.tick node"))

    reachable = _exec_reachable(doc)
    reachable_orders = [
        node for node in doc.nodes if node.type in ORDER_ACTIONS and node.id in reachable
    ]

    for node in doc.nodes:
        if node.type in ORDER_ACTIONS and node.id not in reachable:
            errors.append(
                Issue(
                    "ACTION_UNREACHABLE",
                    f"{node.type} is not reachable from an event tick and can never fire",
                    node=node.id,
                )
            )

    if not reachable_orders:
        errors.append(Issue("NO_ACTION", "no order action is reachable from an event tick"))

    # -- warnings ----------------------------------------------------------------------
    for node in doc.nodes:
        spec = _spec_of(node)
        if spec is None:
            continue
        if all(port.type != "exec" for port in spec.outputs) and not outgoing.get(node.id):
            warnings.append(Issue("NODE_ORPHAN", f"{spec.type} output feeds nothing", node=node.id))

    warmup = _warmup_bars(doc, parsed)
    for timeframe, bars in warmup.items():
        if bars > MAX_WARMUP_BARS:
            warnings.append(
                Issue(
                    "WARMUP_LONG",
                    f"{timeframe} needs {bars} warmup bars (limit {MAX_WARMUP_BARS})",
                )
            )

    has_buy = any(node.type == "act.buy" and node.id in reachable for node in doc.nodes)
    has_sell = any(node.type == "act.sell" and node.id in reachable for node in doc.nodes)
    if has_buy and not has_sell:
        warnings.append(Issue("NO_EXIT", "buys are reachable but no sell is"))

    for node_id in _duplicate_order_paths(doc):
        offender = nodes.get(node_id)
        warnings.append(
            Issue(
                "DUPLICATE_ORDER_PATH",
                f"{offender.type if offender else 'action'} can fire on the same exec path after "
                "another order; only the first order per tick is kept",
                node=node_id,
            )
        )

    return ValidationReport(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        warmup_bars=warmup,
    )


__all__ = [
    "ERROR_CODES",
    "MAX_WARMUP_BARS",
    "WARNING_CODES",
    "Issue",
    "ValidationReport",
    "validate",
]
