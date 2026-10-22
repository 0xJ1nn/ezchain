"use client";

// Blueprint-grade visual strategy builder (docs/STRATEGY-GRAPH.md).
//
// The page is an editor for one typed JSON document: it never evaluates a rule, prices an
// order or computes a PnL. Validation, dry runs and backtests come from the API; in
// fixture mode they come from `lib/graph-fixtures.ts` and are labelled FIXTURE. Saving is
// always a real POST/PATCH — a fixture build must not invent a strategy.

import "@xyflow/react/dist/style.css";

import {
	Background,
	BackgroundVariant,
	Controls,
	MiniMap,
	ReactFlow,
	ReactFlowProvider,
	addEdge,
	useEdgesState,
	useNodesState,
	useReactFlow,
	type Connection,
	type Edge,
	type IsValidConnection,
	type NodeTypes,
	type OnConnectEnd,
	type OnNodesChange,
} from "@xyflow/react";
import Link from "next/link";
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import { usd } from "@/lib/format";
import {
	createGraphStrategy,
	dryRunGraph,
	GraphApiError,
	getGraphCatalogue,
	getGraphStrategy,
	listGraphRuns,
	patchGraphStrategy,
	runBacktest,
	validateGraph,
	type GraphMode,
} from "@/lib/graph-api";
import { findNodeSpec, pinSignature, pinsCompatible, searchNodes } from "@/lib/graph-catalogue";
import {
	COMMENT_NODE_TYPE,
	GRAPH_LIMITS,
	edgeId,
	mergeDocument,
	nextNodeId,
	parseGraphDocument,
	selectionToDocument,
	toDocument,
	type FlowState,
} from "@/lib/graph-doc";
import { graphFromParams } from "@/lib/graph-fixtures";
import type {
	Backtest,
	BacktestEquityPoint,
	BacktestTrade,
	CatalogueNode,
	DryRunResult,
	GraphCatalogue,
	GraphEffect,
	GraphPinType,
	GraphTrace,
	GraphTraceValues,
	ValidationIssue,
	ValidationReport,
} from "@/lib/graph-types";
import { strategyChains } from "@/lib/strategy-fixtures";
import {
	CommentNodeView,
	GraphNodeView,
	NodeActionsContext,
	type GraphFlowNode,
} from "./graph-node";
import { NodeMenu, type MenuRequest } from "./node-menu";
import styles from "./builder.module.css";

/** Matches `.node` in builder.module.css. */
const NODE_WIDTH = 236;

const nodeTypes: NodeTypes = {
	graph: GraphNodeView,
	comment: CommentNodeView,
};

interface Meta {
	name: string;
	chain: string;
	token_address: string;
	token_symbol: string;
	interval_minutes: string;
	max_position_usd: string;
}

const EMPTY_META: Meta = {
	name: "",
	chain: "solana",
	token_address: "",
	token_symbol: "",
	interval_minutes: "15",
	max_position_usd: "250",
};

function defaultsFor(spec: CatalogueNode): Record<string, string> {
	const params: Record<string, string> = {};
	for (const param of spec.params) {
		if (param.default === null || param.default === undefined) continue;
		params[param.name] = String(param.default);
	}
	return params;
}

function shortSeed(type: string): string {
	const parts = type.split(".");
	return parts[parts.length - 1] ?? type;
}

function makeEdge(
	source: string,
	sourcePort: string,
	target: string,
	targetPort: string,
): Edge {
	return {
		id: edgeId(source, sourcePort, target, targetPort),
		source,
		sourceHandle: sourcePort,
		target,
		targetHandle: targetPort,
	};
}

function flowNodesFromState(state: FlowState): GraphFlowNode[] {
	return state.nodes.map((node) => {
		if (node.type === COMMENT_NODE_TYPE) {
			return {
				id: node.id,
				type: "comment",
				position: node.position,
				data: { nodeType: COMMENT_NODE_TYPE, params: {}, text: node.text ?? "Group" },
				width: node.width,
				height: node.height,
				zIndex: -10,
				selectable: true,
			};
		}
		return {
			id: node.id,
			type: "graph",
			position: node.position,
			// An explicit width keeps React Flow's visibility check satisfied while the
			// node measures itself; otherwise every node stays visibility:hidden.
			width: NODE_WIDTH,
			data: { nodeType: node.type, params: { ...node.params } },
		};
	});
}

function stateFromFlow(nodes: GraphFlowNode[], edges: Edge[]): FlowState {
	return {
		nodes: nodes.map((node) => ({
			id: node.id,
			type: node.data.nodeType,
			params: node.data.params,
			position: node.position,
			text: node.data.text,
			width: node.width ?? undefined,
			height: node.height ?? undefined,
		})),
		edges: edges.map((edge) => ({
			id: edge.id,
			source: edge.source,
			sourcePort: edge.sourceHandle ?? "",
			target: edge.target,
			targetPort: edge.targetHandle ?? "",
		})),
	};
}

function eventPoint(event: MouseEvent | React.MouseEvent): { x: number; y: number } {
	return { x: event.clientX, y: event.clientY };
}

function stamp(iso: string): string {
	return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
}

function money(value: string | number | null | undefined): string {
	if (value === null || value === undefined || value === "") return "—";
	const amount = Number(value);
	if (!Number.isFinite(amount)) return "—";
	return usd(amount);
}

function plain(value: string | number | boolean | null | undefined): string {
	if (value === null || value === undefined || value === "") return "—";
	if (typeof value === "boolean") return value ? "true" : "false";
	return String(value);
}

function issueLabel(issue: ValidationIssue): string {
	if (!issue.node) return issue.code;
	return `${issue.code} · ${issue.node}${issue.port ? `.${issue.port}` : ""}`;
}

export function GraphBuilder({ mode, strategyId }: { mode: GraphMode; strategyId?: string }) {
	return (
		<ReactFlowProvider>
			<BuilderInner mode={mode} strategyId={strategyId} />
		</ReactFlowProvider>
	);
}

function BuilderInner({ mode, strategyId }: { mode: GraphMode; strategyId?: string }) {
	const flow = useReactFlow<GraphFlowNode, Edge>();
	const [catalogue, setCatalogue] = useState<GraphCatalogue | null>(null);
	const [loadError, setLoadError] = useState<{ code: string; message: string } | null>(null);
	const [loading, setLoading] = useState(true);
	const [foreign, setForeign] = useState<{ id: string; name: string; kind: string } | null>(null);
	const [strategy, setStrategy] = useState<{ id: string; name: string } | null>(null);
	const [meta, setMeta] = useState<Meta>(EMPTY_META);
	const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

	const [nodes, setNodes, onNodesChange] = useNodesState<GraphFlowNode>([]);
	const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

	const [report, setReport] = useState<ValidationReport | null>(null);
	const [validating, setValidating] = useState(false);
	const [trace, setTrace] = useState<GraphTrace | null>(null);
	const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
	const [backtest, setBacktest] = useState<Backtest | null>(null);
	const [drawer, setDrawer] = useState<"dryrun" | "backtest">("dryrun");
	const [busy, setBusy] = useState<string | null>(null);
	const [notice, setNotice] = useState<{ tone: "ok" | "err" | "warn"; text: string } | null>(null);
	const [saveState, setSaveState] = useState<{ tone: "ok" | "err" | "idle"; text: string }>({
		tone: "idle",
		text: "not saved",
	});
	const [menu, setMenu] = useState<MenuRequest | null>(null);
	const [dialog, setDialog] = useState<{ mode: "copy" | "paste"; text: string } | null>(null);
	const [paletteQuery, setPaletteQuery] = useState("");
	const [wide, setWide] = useState(true);
	const [lastValidated, setLastValidated] = useState<string | null>(null);

	const wrapperRef = useRef<HTMLDivElement | null>(null);
	const pointerRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
	const nodesRef = useRef(nodes);
	const edgesRef = useRef(edges);
	const historyRef = useRef<{ past: FlowState[]; future: FlowState[] }>({ past: [], future: [] });

	nodesRef.current = nodes;
	edgesRef.current = edges;

	const snapshot = useCallback(
		(): FlowState => stateFromFlow(nodesRef.current, edgesRef.current),
		[],
	);

	const pushHistory = useCallback(() => {
		const history = historyRef.current;
		history.past.push(snapshot());
		if (history.past.length > 100) history.past.shift();
		history.future = [];
	}, [snapshot]);

	const applyState = useCallback(
		(state: FlowState) => {
			setNodes(flowNodesFromState(state));
			setEdges(state.edges.map((edge) => makeEdge(edge.source, edge.sourcePort, edge.target, edge.targetPort)));
		},
		[setEdges, setNodes],
	);

	useEffect(() => {
		const query = window.matchMedia("(min-width: 1024px)");
		const update = () => setWide(query.matches);
		update();
		query.addEventListener("change", update);
		return () => query.removeEventListener("change", update);
	}, []);

	// ---------------------------------------------------------------- load the editor
	useEffect(() => {
		let cancelled = false;
		const load = async () => {
			const cat = await getGraphCatalogue(mode);
			if (cancelled) return;
			if (!cat.ok) {
				setLoadError(cat.error);
				setLoading(false);
				return;
			}
			setCatalogue(cat.data);
			if (!strategyId) {
				setLoading(false);
				return;
			}
			const row = await getGraphStrategy(mode, strategyId);
			if (cancelled) return;
			if (!row.ok) {
				setLoadError(row.error);
				setLoading(false);
				return;
			}
			setStrategy({ id: row.data.id, name: row.data.name });
			if (row.data.kind !== "graph") {
				setForeign({ id: row.data.id, name: row.data.name, kind: row.data.kind });
				setLoading(false);
				return;
			}
			const parsed = graphFromParams(row.data.params);
			if (!parsed.ok) {
				setLoadError({ code: "STRATEGY_GRAPH_INVALID", message: parsed.message });
				setLoading(false);
				return;
			}
			const params = row.data.params;
			setMeta({
				name: row.data.name,
				chain: row.data.chain,
				token_address: row.data.token_address,
				token_symbol: row.data.token_symbol,
				interval_minutes: String(params.interval_minutes ?? "15"),
				max_position_usd: String(params.max_position_usd ?? "250"),
			});
			applyState({
				nodes: parsed.document.nodes
					.map((node) => ({
						id: node.id,
						type: node.type,
						params: { ...node.params },
						position: node.ui,
					}))
					.concat(
					parsed.document.comments.map((comment) => ({
						id: comment.id,
						type: COMMENT_NODE_TYPE,
						params: {},
						position: { x: comment.ui.x, y: comment.ui.y },
						text: comment.text,
						width: comment.ui.w,
						height: comment.ui.h,
					})),
				),
				edges: parsed.document.edges.map((edge) => ({
					id: edgeId(edge.from.node, edge.from.port, edge.to.node, edge.to.port),
					source: edge.from.node,
					sourcePort: edge.from.port,
					target: edge.to.node,
					targetPort: edge.to.port,
				})),
			});
			const runs = await listGraphRuns(mode, strategyId);
			if (cancelled) return;
			if (runs.ok && runs.data.length > 0) {
				const inputs = runs.data[0].inputs as { graph_trace?: GraphTrace };
				if (inputs.graph_trace) setTrace(inputs.graph_trace);
			}
			setLoading(false);
		};
		void load();
		return () => {
			cancelled = true;
		};
	}, [applyState, mode, strategyId]);

	// ---------------------------------------------------------------- derived rendering
	const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

	const reportByNode = useMemo(() => {
		const map = new Map<string, { error?: string; warning?: string }>();
		if (!report) return map;
		for (const error of report.errors) {
			if (!error.node) continue;
			const current = map.get(error.node) ?? {};
			map.set(error.node, { ...current, error: current.error ?? error.message });
		}
		for (const warning of report.warnings) {
			if (!warning.node) continue;
			const current = map.get(warning.node) ?? {};
			if (current.error) continue;
			map.set(warning.node, { ...current, warning: current.warning ?? warning.message });
		}
		return map;
	}, [report]);

	// Derived visual state is written onto the nodes and edges themselves, not into a new
	// array every render: React Flow measures the objects it is handed, and rebuilding them
	// discards that measurement (nodes then stay visibility:hidden).
	useEffect(() => {
		if (!catalogue) return;
		setNodes((current) => {
			let changed = false;
			const next = current.map((node) => {
				if (node.type === "comment") return node;
				const spec = findNodeSpec(catalogue, node.data.nodeType);
				const firstDataOutput = spec?.outputs.find((output) => output.type !== "exec")?.name;
				const connectedIn = edges
					.filter((edge) => edge.target === node.id)
					.map((edge) => edge.targetHandle ?? "");
				const connectedOut = edges
					.filter((edge) => edge.source === node.id)
					.map((edge) => edge.sourceHandle ?? "");
				const issue = reportByNode.get(node.id);
				const traced = trace?.exec.includes(node.id) ?? false;
				const values =
					trace && firstDataOutput && node.id in trace.values
						? { [firstDataOutput]: (trace.values[node.id] as string | null) ?? null }
						: undefined;
				const signature = JSON.stringify([
					connectedIn,
					connectedOut,
					issue?.error ?? null,
					issue?.warning ?? null,
					traced,
					values ?? null,
				]);
				if (node.data.signature === signature) return node;
				changed = true;
				return {
					...node,
					data: {
						...node.data,
						connectedIn,
						connectedOut,
						error: issue?.error ?? null,
						warning: issue?.warning ?? null,
						values,
						traced,
						signature,
					},
				};
			});
			return changed ? next : current;
		});
	}, [catalogue, edges, reportByNode, setNodes, trace]);

	useEffect(() => {
		if (!catalogue) return;
		setEdges((current) => {
			let changed = false;
			const next = current.map((edge) => {
				const source = nodeById.get(edge.source);
				const pinType: GraphPinType =
					((source
						? findNodeSpec(catalogue, source.data.nodeType)?.outputs.find(
								(output) => output.name === edge.sourceHandle,
							)?.type
						: null) ?? "number") as GraphPinType;
				const value = trace && edge.source in trace.values ? trace.values[edge.source] : undefined;
				const label =
					trace !== null && pinType !== "exec" && value !== undefined ? plain(value) : undefined;
				const signature = `${pinType}|${label ?? ""}`;
				if (edge.data?.signature === signature) return edge;
				changed = true;
				return {
					...edge,
					type: "default",
					style: {
						stroke: `var(--graph-${pinType})`,
						strokeWidth: pinType === "exec" ? 2.2 : 1.5,
						strokeOpacity: pinType === "exec" ? 0.95 : 0.75,
					},
					label,
					labelStyle: {
						fill: "var(--graph-ink)",
						font: "10px var(--mono)",
						fontVariantNumeric: "tabular-nums",
					},
					labelBgStyle: {
						fill: "#0d1117",
						fillOpacity: 0.92,
						stroke: `var(--graph-${pinType})`,
						strokeWidth: 1,
					},
					labelBgPadding: [4, 2] as [number, number],
					labelBgBorderRadius: 2,
					data: { ...(edge.data ?? {}), signature, pinType },
				};
			});
			return changed ? next : current;
		});
	}, [catalogue, nodeById, setEdges, trace]);

	const selectedNode = nodes.find((node) => node.selected) ?? null;
	const selectedCount = nodes.filter((node) => node.selected).length;
	const problemCount = (report?.errors.length ?? 0) + (report?.warnings.length ?? 0);

	// ---------------------------------------------------------------- graph mutations
	const createFlowNode = useCallback(
		(spec: CatalogueNode, position: { x: number; y: number }) => {
			const ids = nodesRef.current.map((node) => node.id);
			if (ids.length >= GRAPH_LIMITS.nodes) {
				setNotice({ tone: "err", text: `A graph holds at most ${GRAPH_LIMITS.nodes} nodes.` });
				return null;
			}
			const id = nextNodeId(ids, shortSeed(spec.type));
			const node: GraphFlowNode = {
				id,
				type: "graph",
				position: { x: Math.round(position.x), y: Math.round(position.y) },
				width: NODE_WIDTH,
				data: { nodeType: spec.type, params: defaultsFor(spec) },
				selected: true,
			};
			setNodes((current) => [...current.map((entry) => ({ ...entry, selected: false })), node]);
			return node;
		},
		[setNodes],
	);

	const addAtCentre = useCallback(
		(spec: CatalogueNode) => {
			const rect = wrapperRef.current?.getBoundingClientRect();
			const centre = rect
				? flow.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
				: { x: 0, y: 0 };
			// Stagger additions on a two-column grid around the viewport centre so a burst of
			// palette clicks never stacks nodes on top of each other.
			const index = nodesRef.current.filter((node) => node.type !== "comment").length;
			const column = index % 2;
			const row = Math.floor(index / 2);
			pushHistory();
			createFlowNode(spec, {
				x: centre.x - 260 + column * 260,
				y: centre.y - 140 + row * 200,
			});
		},
		[createFlowNode, flow, pushHistory],
	);

	const applyConnectedPick = useCallback(
		(spec: CatalogueNode, request: MenuRequest) => {
			pushHistory();
			const created = createFlowNode(spec, { x: request.flowX, y: request.flowY });
			if (!created) return;
			const from = request.from;
			const filter = request.filter;
			if (!from || !filter) return;
			if (filter.direction === "source") {
				const compatible = spec.inputs.filter((input) => {
					if (!pinsCompatible(filter.type, input.type)) return false;
					return !edgesRef.current.some(
						(edge) => edge.target === created.id && edge.targetHandle === input.name,
					);
				});
				const port = compatible[0];
				if (port) {
					setEdges((current) => addEdge(makeEdge(from.nodeId, from.port, created.id, port.name), current));
				}
				return;
			}
			const occupied = edgesRef.current.some(
				(edge) => edge.target === from.nodeId && edge.targetHandle === from.port,
			);
			if (occupied) return;
			const output = spec.outputs.find((entry) => pinsCompatible(entry.type, filter.type));
			if (output) {
				setEdges((current) => addEdge(makeEdge(created.id, output.name, from.nodeId, from.port), current));
			}
		},
		[createFlowNode, pushHistory, setEdges],
	);

	const isValidConnection = useCallback<IsValidConnection<Edge>>(
		(connection) => {
			if (!catalogue) return false;
			if (!connection.source || !connection.target) return false;
			const source = nodeById.get(connection.source);
			const target = nodeById.get(connection.target);
			if (!source || !target || source.type === "comment" || target.type === "comment") return false;
			const sourceSpec = findNodeSpec(catalogue, source.data.nodeType);
			const targetSpec = findNodeSpec(catalogue, target.data.nodeType);
			const sourcePin = sourceSpec?.outputs.find((output) => output.name === connection.sourceHandle);
			const targetPin = targetSpec?.inputs.find((input) => input.name === connection.targetHandle);
			if (!sourcePin || !targetPin) return false;
			if (!pinsCompatible(sourcePin.type, targetPin.type)) return false;
			const inputTaken = edgesRef.current.some(
				(edge) =>
					edge.target === connection.target &&
					edge.targetHandle === connection.targetHandle &&
					edge.id !== `existing:${connection.source}:${connection.target}`,
			);
			if (inputTaken) return false;
			if (sourcePin.type === "exec") {
				const execTaken = edgesRef.current.some(
					(edge) => edge.source === connection.source && edge.sourceHandle === connection.sourceHandle,
				);
				if (execTaken) return false;
			}
			return true;
		},
		[catalogue, nodeById],
	);

	const handleConnect = useCallback(
		(connection: Connection) => {
			if (!connection.source || !connection.target) return;
			if (!connection.sourceHandle || !connection.targetHandle) return;
			pushHistory();
			setEdges((current) =>
				addEdge(
					makeEdge(connection.source, connection.sourceHandle ?? "", connection.target ?? "", connection.targetHandle ?? ""),
					current,
				),
			);
			setNotice(null);
		},
		[pushHistory, setEdges],
	);

	const openMenuAt = useCallback(
		(point: { x: number; y: number }, request: Partial<MenuRequest>) => {
			setMenu({
				x: point.x,
				y: point.y,
				flowX: request.flowX ?? flow.screenToFlowPosition({ x: point.x, y: point.y }).x,
				flowY: request.flowY ?? flow.screenToFlowPosition({ x: point.x, y: point.y }).y,
				filter: request.filter ?? null,
				from: request.from ?? null,
			});
		},
		[flow],
	);

	const handleConnectEnd = useCallback<OnConnectEnd>(
		(event, connectionState) => {
			if (connectionState.isValid) return;
			if (connectionState.toHandle) return;
			const fromHandle = connectionState.fromHandle;
			const fromNode = connectionState.fromNode;
			if (!fromHandle || !fromNode || !catalogue) return;
			const direction = fromHandle.type === "source" ? "source" : "target";
			const nodeType = (fromNode.data as { nodeType?: string }).nodeType;
			if (!nodeType) return;
			const pinType =
				direction === "source"
					? findNodeSpec(catalogue, nodeType)?.outputs.find((output) => output.name === fromHandle.id)?.type
					: findNodeSpec(catalogue, nodeType)?.inputs.find((input) => input.name === fromHandle.id)?.type;
			if (!pinType) return;
			const point = eventPoint(event as MouseEvent);
			const stream = flow.screenToFlowPosition({ x: point.x, y: point.y });
			openMenuAt(point, {
				flowX: stream.x,
				flowY: stream.y,
				filter: { type: pinType, direction },
				from: { nodeId: fromNode.id, port: fromHandle.id ?? "" },
			});
		},
		[catalogue, flow, openMenuAt],
	);

	const handlePaneContextMenu = useCallback(
		(event: MouseEvent | React.MouseEvent) => {
			event.preventDefault();
			const point = eventPoint(event);
			openMenuAt(point, {});
		},
		[openMenuAt],
	);

	const handleDrop = useCallback(
		(event: React.DragEvent<HTMLDivElement>) => {
			event.preventDefault();
			const type = event.dataTransfer.getData("application/ezchain-node");
			if (!type || !catalogue) return;
			const spec = findNodeSpec(catalogue, type);
			if (!spec) return;
			const position = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
			pushHistory();
			createFlowNode(spec, { x: position.x - 120, y: position.y - 40 });
		},
		[catalogue, createFlowNode, flow, pushHistory],
	);

	const deleteSelection = useCallback(() => {
		const selectedNodes = nodesRef.current.filter((node) => node.selected).map((node) => node.id);
		const selectedEdges = edgesRef.current.filter((edge) => edge.selected).map((edge) => edge.id);
		if (selectedNodes.length === 0 && selectedEdges.length === 0) return;
		pushHistory();
		const gone = new Set(selectedNodes);
		setNodes((current) => current.filter((node) => !gone.has(node.id)));
		setEdges((current) =>
			current.filter(
				(edge) =>
					!selectedEdges.includes(edge.id) && !gone.has(edge.source) && !gone.has(edge.target),
			),
		);
	}, [pushHistory, setEdges, setNodes]);

	const copyText = useCallback(() => {
		const state = snapshot();
		const selected = nodesRef.current.filter((node) => node.selected).map((node) => node.id);
		const ids = selected.length > 0 ? selected : state.nodes.map((node) => node.id);
		return JSON.stringify(selectionToDocument(state, ids), null, 2);
	}, [snapshot]);

	const applyPaste = useCallback(
		(text: string) => {
			const parsed = parseGraphDocument(text);
			if (!parsed.ok) {
				setNotice({ tone: "err", text: `Paste failed: ${parsed.message}` });
				return;
			}
			pushHistory();
			const offset = nodesRef.current.length > 0 ? { x: 48, y: 48 } : { x: 0, y: 0 };
			const merged = mergeDocument(snapshot(), parsed.document, offset);
			const incoming = flowNodesFromState({ nodes: merged.nodes, edges: [] }).map((node) => ({
				...node,
				selected: true,
			}));
			setNodes((current) => [...current.map((node) => ({ ...node, selected: false })), ...incoming]);
			setEdges((current) => [
				...current,
				...merged.edges.map((edge) => makeEdge(edge.source, edge.sourcePort, edge.target, edge.targetPort)),
			]);
			setDialog(null);
			setNotice({ tone: "ok", text: `Pasted ${merged.nodes.length} node(s).` });
		},
		[pushHistory, setEdges, setNodes, snapshot],
	);

	const undo = useCallback(() => {
		const history = historyRef.current;
		const previous = history.past.pop();
		if (!previous) return;
		history.future.unshift(snapshot());
		applyState(previous);
	}, [applyState, snapshot]);

	const redo = useCallback(() => {
		const history = historyRef.current;
		const next = history.future.shift();
		if (!next) return;
		history.past.push(snapshot());
		applyState(next);
	}, [applyState, snapshot]);

	const groupSelection = useCallback(() => {
		const selected = nodesRef.current.filter((node) => node.type !== "comment" && node.selected);
		if (selected.length === 0) {
			setNotice({ tone: "warn", text: "Select one or more nodes, then press C to group them." });
			return;
		}
		const boxes = selected.map((node) => ({
			x: node.position.x,
			y: node.position.y,
			w: node.measured?.width ?? node.width ?? 240,
			h: node.measured?.height ?? node.height ?? 120,
		}));
		const minX = Math.min(...boxes.map((box) => box.x));
		const minY = Math.min(...boxes.map((box) => box.y));
		const maxX = Math.max(...boxes.map((box) => box.x + box.w));
		const maxY = Math.max(...boxes.map((box) => box.y + box.h));
		pushHistory();
		const id = nextNodeId(nodesRef.current.map((node) => node.id), "group");
		const comment: GraphFlowNode = {
			id,
			type: "comment",
			position: { x: Math.round(minX - 24), y: Math.round(minY - 52) },
			data: { nodeType: COMMENT_NODE_TYPE, params: {}, text: "Group" },
			width: Math.round(maxX - minX + 48),
			height: Math.round(maxY - minY + 76),
			zIndex: -10,
			selectable: true,
			selected: true,
		};
		setNodes((current) => [...current.map((node) => ({ ...node, selected: false })), comment]);
		setNotice({ tone: "ok", text: `Grouped ${selected.length} node(s). Rename it in the details panel.` });
	}, [pushHistory, setNodes]);

	const updateParam = useCallback(
		(nodeId: string, name: string, value: string) => {
			setNodes((current) =>
				current.map((node) =>
					node.id === nodeId
						? { ...node, data: { ...node.data, params: { ...node.data.params, [name]: value } } }
						: node,
				),
			);
			setReport(null);
		},
		[setNodes],
	);

	const updateComment = useCallback(
		(nodeId: string, text: string) => {
			setNodes((current) =>
				current.map((node) =>
					node.id === nodeId ? { ...node, data: { ...node.data, text } } : node,
				),
			);
		},
		[setNodes],
	);

	const nodeActions = useMemo(
		() => (catalogue ? { catalogue, setParam: updateParam, setCommentText: updateComment } : null),
		[catalogue, updateComment, updateParam],
	);

	// ---------------------------------------------------------------- API actions
	const validateNow = useCallback(async (): Promise<ValidationReport | null> => {
		setValidating(true);
		const document = toDocument(snapshot());
		const result = await validateGraph(mode, document);
		setValidating(false);
		if (!result.ok) {
			const failure: ValidationReport = {
				ok: false,
				errors: [
					{
						code: result.error.code,
						message: result.error.message,
						node: null,
						port: null,
					},
				],
				warnings: [],
				warmup_bars: {},
			};
			const next = result.error.report ?? failure;
			setReport(next);
			return next;
		}
		setReport(result.data);
		setLastValidated(new Date().toISOString().slice(11, 19));
		return result.data;
	}, [mode, snapshot]);

	const doDryRun = useCallback(async () => {
		if (!meta.token_address.trim()) {
			setNotice({ tone: "warn", text: "Enter the token address before a dry run." });
			return;
		}
		setBusy("dryrun");
		setNotice(null);
		const checked = await validateNow();
		if (!checked?.ok) {
			setBusy(null);
			setNotice({
				tone: "err",
				text: `Dry run blocked: ${checked?.errors.length ?? 0} validation error(s). Fix the problems first.`,
			});
			return;
		}
		const result = await dryRunGraph(mode, {
			graph: toDocument(snapshot()),
			chain: meta.chain,
			token_address: meta.token_address.trim(),
			max_position_usd: meta.max_position_usd.trim() || undefined,
		});
		setBusy(null);
		if (!result.ok) {
			setNotice({ tone: "err", text: `${result.error.code}: ${result.error.message}` });
			return;
		}
		setDryRun(result.data);
		setTrace(result.data.trace);
		setDrawer("dryrun");
		setNotice({
			tone: "ok",
			text: `Dry run complete: ${result.data.decision.action} · no order was placed.`,
		});
	}, [meta, mode, snapshot, validateNow]);

	const doBacktest = useCallback(async () => {
		if (!meta.token_address.trim()) {
			setNotice({ tone: "warn", text: "Enter the token address before a backtest." });
			return;
		}
		setBusy("backtest");
		setNotice(null);
		const checked = await validateNow();
		if (!checked?.ok) {
			setBusy(null);
			setNotice({
				tone: "err",
				text: `Backtest blocked: ${checked?.errors.length ?? 0} validation error(s). Fix the problems first.`,
			});
			return;
		}
		const document = toDocument(snapshot());
		const candles = document.nodes.find((node) => node.type === "data.candles");
		const timeframe = typeof candles?.params.timeframe === "string" ? candles.params.timeframe : "5m";
		// The API accepts exactly one of graph/strategy_id. The builder sends the current
		// document so unsaved edits are what get tested; saved-strategy backtests use the
		// strategy id path elsewhere.
		const result = await runBacktest(mode, {
			graph: document,
			chain: meta.chain,
			token_address: meta.token_address.trim(),
			timeframe,
			bars: 480,
			fee_bps: 10,
			slippage_bps: 5,
			starting_cash_usd: "10000",
		});
		setBusy(null);
		if (!result.ok) {
			setNotice({ tone: "err", text: `${result.error.code}: ${result.error.message}` });
			return;
		}
		setBacktest(result.data);
		setDrawer("backtest");
		setNotice({ tone: "ok", text: "Backtest complete · labelled BACKTEST, never PAPER." });
	}, [meta, mode, snapshot, validateNow]);

	const save = useCallback(async () => {
		const errors: Record<string, string> = {};
		const name = meta.name.trim();
		const token = meta.token_address.trim();
		const symbol = meta.token_symbol.trim().toUpperCase();
		const interval = Number(meta.interval_minutes);
		const maxPosition = Number(meta.max_position_usd);
		if (!name) errors.name = "Name is required.";
		if (token.length < 8) errors.token_address = "Enter the full token address.";
		if (!symbol) errors.token_symbol = "Symbol is required.";
		if (!Number.isFinite(interval) || interval < 1) errors.interval_minutes = "At least 1 minute.";
		if (!Number.isFinite(maxPosition) || maxPosition <= 0) {
			errors.max_position_usd = "Greater than zero.";
		}
		setFieldErrors(errors);
		if (Object.keys(errors).length > 0) {
			setNotice({ tone: "err", text: "Not saved: the strategy header is incomplete." });
			return;
		}

		setBusy("save");
		const checked = await validateNow();
		if (!checked?.ok) {
			setBusy(null);
			setSaveState({ tone: "err", text: "blocked by validation" });
			setNotice({
				tone: "err",
				text: `Not saved: ${checked?.errors.length ?? 0} validation error(s) in the graph.`,
			});
			return;
		}

		const params = {
			graph: toDocument(snapshot()),
			interval_minutes: interval,
			max_position_usd: meta.max_position_usd.trim(),
		};

		try {
			const saved = strategy
				? await patchGraphStrategy(strategy.id, { name, params })
				: await createGraphStrategy({
						name,
						chain: meta.chain,
						token_address: token,
						token_symbol: symbol,
						kind: "graph",
						params,
						status: "active",
					});
			setStrategy({ id: saved.id, name: saved.name ?? name });
			setSaveState({ tone: "ok", text: `saved ${new Date().toISOString().slice(11, 19)} UTC` });
			setNotice({
				tone: "ok",
				text: `Saved “${saved.name ?? name}” (${saved.id}). The engine owns evaluation.`,
			});
		} catch (error) {
			const apiError = error instanceof GraphApiError ? error : null;
			if (apiError?.report) setReport(apiError.report);
			setSaveState({ tone: "err", text: apiError?.message ?? "save failed" });
			setNotice({
				tone: "err",
				text: `${apiError?.code ?? "SAVE_FAILED"}: ${apiError?.message ?? "the strategy could not be saved"}`,
			});
		} finally {
			setBusy(null);
		}
	}, [meta, snapshot, strategy, validateNow]);

	const fitAll = useCallback(() => {
		void flow.fitView({ padding: 0.15, duration: 300 });
	}, [flow]);

	// ---------------------------------------------------------------- keyboard
	const actionsRef = useRef({
		validate: () => void validateNow(),
		save: () => void save(),
		deleteSelection,
		copy: () => {
			const text = copyText();
			setDialog({ mode: "copy", text });
			void navigator.clipboard?.writeText(text).catch(() => undefined);
		},
		paste: () => {
			const clipboard = navigator.clipboard;
			if (!clipboard?.readText) {
				setDialog({ mode: "paste", text: "" });
				return;
			}
			clipboard
				.readText()
				.then((text) => (text.trim() ? applyPaste(text) : setDialog({ mode: "paste", text: "" })))
				.catch(() => setDialog({ mode: "paste", text: "" }));
		},
		undo,
		redo,
		group: groupSelection,
		fit: fitAll,
		openMenu: (point: { x: number; y: number }) => openMenuAt(point, {}),
	});
	actionsRef.current = {
		validate: () => void validateNow(),
		save: () => void save(),
		deleteSelection,
		copy: () => {
			const text = copyText();
			setDialog({ mode: "copy", text });
			void navigator.clipboard?.writeText(text).catch(() => undefined);
		},
		paste: () => {
			const clipboard = navigator.clipboard;
			if (!clipboard?.readText) {
				setDialog({ mode: "paste", text: "" });
				return;
			}
			clipboard
				.readText()
				.then((text) => (text.trim() ? applyPaste(text) : setDialog({ mode: "paste", text: "" })))
				.catch(() => setDialog({ mode: "paste", text: "" }));
		},
		undo,
		redo,
		group: groupSelection,
		fit: fitAll,
		openMenu: (point: { x: number; y: number }) => openMenuAt(point, {}),
	};

	useEffect(() => {
		const onKeyDown = (event: KeyboardEvent) => {
			const target = event.target as HTMLElement | null;
			const typing =
				target?.closest("input, textarea, select, [contenteditable='true']") !== null &&
				target !== null;
			const mod = event.ctrlKey || event.metaKey;
			const actions = actionsRef.current;
			if (event.key === "F7") {
				event.preventDefault();
				actions.validate();
				return;
			}
			if (mod && event.key.toLowerCase() === "s") {
				event.preventDefault();
				actions.save();
				return;
			}
			if (mod && event.key.toLowerCase() === "z") {
				event.preventDefault();
				if (event.shiftKey) actions.redo();
				else actions.undo();
				return;
			}
			if (typing) return;
			if (mod && event.key.toLowerCase() === "c") {
				event.preventDefault();
				actions.copy();
				return;
			}
			if (mod && event.key.toLowerCase() === "v") {
				event.preventDefault();
				actions.paste();
				return;
			}
			if (event.key === "Delete" || event.key === "Backspace") {
				event.preventDefault();
				actions.deleteSelection();
				return;
			}
			if (event.key === "Escape") {
				setMenu(null);
				setDialog(null);
				return;
			}
			if (event.key === "Home") {
				event.preventDefault();
				actions.fit();
				return;
			}
			if (event.key.toLowerCase() === "c" && !mod) {
				event.preventDefault();
				actions.group();
				return;
			}
			if (event.key === " " && !mod) {
				const rect = wrapperRef.current?.getBoundingClientRect();
				if (!rect) return;
				const pointer = pointerRef.current;
				const inside =
					pointer.x >= rect.left &&
					pointer.x <= rect.right &&
					pointer.y >= rect.top &&
					pointer.y <= rect.bottom;
				event.preventDefault();
				actions.openMenu(
					inside ? pointer : { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 },
				);
			}
		};
		window.addEventListener("keydown", onKeyDown);
		return () => window.removeEventListener("keydown", onKeyDown);
	}, []);

	// ---------------------------------------------------------------- render
	if (loading) {
		return (
			<section className={styles.statePanel}>
				<h2 className={styles.stateTitle}>Visual strategy builder</h2>
				<div className={styles.empty}>Loading the catalogue…</div>
			</section>
		);
	}

	if (loadError) {
		return (
			<section className={styles.statePanel} data-testid="builder-load-error">
				<h2 className={styles.stateTitle}>Builder unavailable</h2>
				<Badge tone="err">{loadError.code}</Badge>
				<div className={styles.empty}>
					<strong>Nothing is being guessed.</strong>
					{loadError.message}
					<div className="row mt-12">
						<Link className="btn" href={strategyId ? `/strategies/${strategyId}/builder` : "/strategies/builder"}>
							Retry
						</Link>
						<Link className="btn" href="/strategies">
							All strategies
						</Link>
					</div>
				</div>
			</section>
		);
	}

	if (foreign) {
		return (
			<section className={styles.statePanel} data-testid="builder-not-graph">
				<h2 className={styles.stateTitle}>{foreign.name}</h2>
				<Badge tone="warn">kind: {foreign.kind}</Badge>
				<div className={styles.empty}>
					<strong>This strategy is not a visual graph strategy.</strong>
					Only <span className="mono">kind: &quot;graph&quot;</span> strategies open in the builder. The
					declarative kinds keep their own form.
					<div className="row mt-12">
						<Link className="btn" href={`/strategies/${foreign.id}`}>
							Open strategy
						</Link>
						<Link className="btn primary" href="/strategies/builder">
							New visual strategy
						</Link>
					</div>
				</div>
			</section>
		);
	}

	const paletteNodes = catalogue ? searchNodes(catalogue, paletteQuery) : [];
	const groups = catalogue
		? catalogue.categories
				.map((category) => ({
					...category,
					nodes: paletteNodes.filter((node) => node.category === category.id),
				}))
				.filter((group) => group.nodes.length > 0)
		: [];

	return (
		<NodeActionsContext.Provider value={nodeActions}>
			<div className={styles.builder} data-mode={mode} data-testid="graph-builder">
				<header className={styles.bar}>
					<div className={styles.barField}>
						<label className={styles.barLabel} htmlFor="graph-name">
							Name
						</label>
						<input
							id="graph-name"
							className={`input ${styles.barInput}`}
							value={meta.name}
							placeholder="BONK EMA cross"
							autoComplete="off"
							onChange={(event) => setMeta({ ...meta, name: event.target.value })}
						/>
						{fieldErrors.name && <span className="error">{fieldErrors.name}</span>}
					</div>
					<div className={styles.barField}>
						<label className={styles.barLabel} htmlFor="graph-chain">
							Chain
						</label>
						<select
							id="graph-chain"
							className={`select ${styles.barSelect}`}
							value={meta.chain}
							onChange={(event) => setMeta({ ...meta, chain: event.target.value })}
						>
							{strategyChains.map((chain) => (
								<option value={chain.id} key={chain.id}>
									{chain.name}
								</option>
							))}
						</select>
					</div>
					<div className={`${styles.barField} ${styles.wide}`}>
						<label className={styles.barLabel} htmlFor="graph-token">
							Token address
						</label>
						<input
							id="graph-token"
							className={`input mono ${styles.barInput}`}
							value={meta.token_address}
							placeholder="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
							autoComplete="off"
							spellCheck={false}
							onChange={(event) => setMeta({ ...meta, token_address: event.target.value })}
						/>
						{fieldErrors.token_address && <span className="error">{fieldErrors.token_address}</span>}
					</div>
					<div className={styles.barField}>
						<label className={styles.barLabel} htmlFor="graph-symbol">
							Symbol
						</label>
						<input
							id="graph-symbol"
							className={`input mono ${styles.barInputSm}`}
							value={meta.token_symbol}
							placeholder="BONK"
							autoComplete="off"
							onChange={(event) => setMeta({ ...meta, token_symbol: event.target.value })}
						/>
						{fieldErrors.token_symbol && <span className="error">{fieldErrors.token_symbol}</span>}
					</div>
					<div className={styles.barField}>
						<label className={styles.barLabel} htmlFor="graph-interval">
							Every (min)
						</label>
						<input
							id="graph-interval"
							className={`input mono ${styles.barInputSm}`}
							type="number"
							min={1}
							value={meta.interval_minutes}
							onChange={(event) => setMeta({ ...meta, interval_minutes: event.target.value })}
						/>
						{fieldErrors.interval_minutes && <span className="error">{fieldErrors.interval_minutes}</span>}
					</div>
					<div className={styles.barField}>
						<label className={styles.barLabel} htmlFor="graph-max">
							Max position (USD)
						</label>
						<input
							id="graph-max"
							className={`input mono ${styles.barInputSm}`}
							inputMode="decimal"
							value={meta.max_position_usd}
							onChange={(event) => setMeta({ ...meta, max_position_usd: event.target.value })}
						/>
						{fieldErrors.max_position_usd && <span className="error">{fieldErrors.max_position_usd}</span>}
					</div>

					<div className={styles.barActions}>
						<Badge tone="ghost">PAPER</Badge>
						{mode === "fixture" && <Badge tone="ghost">FIXTURE</Badge>}
						<button
							className="btn sm"
							type="button"
							onClick={() => void validateNow()}
							disabled={validating}
							title="Validate (F7)"
							data-testid="validate"
						>
							{validating ? "Validating…" : "Validate"}
							{problemCount > 0 ? ` (${problemCount})` : ""}
						</button>
						<button
							className="btn sm"
							type="button"
							onClick={() => void doDryRun()}
							disabled={busy !== null}
							data-testid="dry-run"
						>
							{busy === "dryrun" ? "Running…" : "Dry run"}
						</button>
						<button
							className="btn sm"
							type="button"
							onClick={() => void doBacktest()}
							disabled={busy !== null}
							data-testid="backtest"
						>
							{busy === "backtest" ? "Running…" : "Backtest"}
						</button>
						<button
							className="btn sm primary"
							type="button"
							onClick={() => void save()}
							disabled={busy !== null}
							title="Save (Ctrl+S)"
							data-testid="save"
						>
							{busy === "save" ? "Saving…" : "Save"}
						</button>
						<span className={styles.saveState} data-tone={saveState.tone} data-testid="save-state">
							{saveState.text}
						</span>
					</div>
				</header>

				{notice && (
					<div className={`${styles.notice} ${notice.tone === "err" ? "err" : notice.tone === "warn" ? "warn" : "ok"}`} role="status">
						{notice.text}
					</div>
				)}

				<div className={styles.stage}>
					<aside className={styles.palette} aria-label="Node palette">
						<div className={styles.paletteHead}>
							<input
								className={styles.paletteSearch}
								placeholder="Search the catalogue"
								aria-label="Search the node catalogue"
								value={paletteQuery}
								onChange={(event) => setPaletteQuery(event.target.value)}
								onKeyDown={(event) => {
									if (event.key !== "Enter") return;
									const first = groups[0]?.nodes[0];
									if (first) addAtCentre(first);
								}}
							/>
							<span className="micro">{paletteNodes.length} node types</span>
						</div>
						<div className={styles.paletteList} data-testid="palette">
							{groups.map((group) => (
								<div key={group.id} className={styles.paletteGroup} data-testid={`palette-cat-${group.id}`}>
									<div className={styles.paletteCat}>{group.title}</div>
									{group.nodes.map((node) => (
										<button
											type="button"
											key={node.type}
											className={styles.paletteItem}
											draggable
											data-testid={`palette-${node.type}`}
											onDragStart={(event) => {
												event.dataTransfer.setData("application/ezchain-node", node.type);
												event.dataTransfer.effectAllowed = "move";
											}}
											onClick={() => addAtCentre(node)}
											title={node.description}
										>
											<span className={styles.paletteItemTitle}>{node.title}</span>
											<span className={styles.paletteItemType}>{node.type}</span>
											<span className={styles.paletteSig}>{pinSignature(catalogue as GraphCatalogue, node)}</span>
										</button>
									))}
								</div>
							))}
							{groups.length === 0 && <div className={styles.empty}>No node matches that search.</div>}
						</div>
						<div className={styles.paletteFoot}>
							<div className={styles.legend}>
								<span className={styles.legendItem}>
									<i className={`${styles.swatch} ${styles.sw_exec}`} /> exec
								</span>
								<span className={styles.legendItem}>
									<i className={`${styles.swatch} ${styles.sw_bool}`} /> bool
								</span>
								<span className={styles.legendItem}>
									<i className={`${styles.swatch} ${styles.sw_number}`} /> number
								</span>
								<span className={styles.legendItem}>
									<i className={`${styles.swatch} ${styles.sw_series}`} /> series
								</span>
							</div>
							<div className="micro">drag · click · Enter adds at centre</div>
						</div>
					</aside>

					<section
						className={styles.canvasWrap}
						ref={wrapperRef}
						onDrop={handleDrop}
						onDragOver={(event) => {
							event.preventDefault();
							event.dataTransfer.dropEffect = "move";
						}}
						onMouseMove={(event) => {
							pointerRef.current = { x: event.clientX, y: event.clientY };
						}}
						aria-label="Strategy graph canvas"
					>
						{wide && (
						<ReactFlow<GraphFlowNode, Edge>
							nodes={nodes}
							edges={edges}
							onNodesChange={onNodesChange as OnNodesChange<GraphFlowNode>}
							onEdgesChange={onEdgesChange}
							onConnect={handleConnect}
							onConnectEnd={handleConnectEnd}
							isValidConnection={isValidConnection}
							onPaneContextMenu={handlePaneContextMenu}
							onPaneClick={() => setMenu(null)}
							nodeTypes={nodeTypes}
							deleteKeyCode={null}
							minZoom={0.15}
							maxZoom={2.5}
							fitView
							colorMode="dark"
							proOptions={{ hideAttribution: false }}
						>
							<Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#1d2531" />
							<MiniMap
								pannable
								zoomable
								ariaLabel="Graph minimap"
								maskColor="rgba(5,8,12,0.72)"
								nodeColor="#3a4653"
								nodeStrokeColor="#5b6b7d"
								style={{ background: "#0b0f14", border: "1px solid #232b36" }}
							/>
							<Controls showInteractive={false} />
						</ReactFlow>
						)}
						{nodes.length === 0 && (
							<div className={styles.canvasHint}>
								<strong>Empty graph</strong>
								<span>
									Drag a node from the palette, press <kbd>Space</kbd> for the node menu, or
									press <kbd>F7</kbd> to see what the validator expects.
								</span>
							</div>
						)}
						<div className={styles.canvasTools}>
							<button
								className="btn ghost sm"
								type="button"
								onClick={() => {
									const text = copyText();
									setDialog({ mode: "copy", text });
									void navigator.clipboard?.writeText(text).catch(() => undefined);
								}}
								data-testid="copy-json"
							>
								Copy graph JSON
							</button>
							<button
								className="btn ghost sm"
								type="button"
								onClick={() => setDialog({ mode: "paste", text: "" })}
								data-testid="paste-json"
							>
								Paste JSON
							</button>
							<button className="btn ghost sm" type="button" onClick={fitAll} title="Fit view (Home)">
								Fit
							</button>
						</div>
					</section>

					<aside className={styles.dock} aria-label="Node details and problems">
						<DetailsPanel
							catalogue={catalogue}
							node={selectedNode}
							selectedCount={selectedCount}
							trace={trace}
						/>
						<ProblemsPanel
							report={report}
							validating={validating}
							lastValidated={lastValidated}
							onFocus={(nodeId) => {
								const node = nodeById.get(nodeId);
								if (!node) return;
								setNodes((current) =>
									current.map((entry) => ({ ...entry, selected: entry.id === nodeId })),
								);
								const width = node.measured?.width ?? node.width ?? 240;
								const height = node.measured?.height ?? node.height ?? 120;
								void flow.setCenter(node.position.x + width / 2, node.position.y + height / 2, {
									zoom: 1.1,
									duration: 400,
								});
							}}
						/>
					</aside>
				</div>

				<section className={styles.drawer} aria-label="Results">
					<div className="tabs">
						<button
							type="button"
							className={`tab${drawer === "dryrun" ? " on" : ""}`}
							onClick={() => setDrawer("dryrun")}
							data-testid="drawer-tab-dryrun"
						>
							Dry run
							{dryRun ? <span className="count">1</span> : null}
						</button>
						<button
							type="button"
							className={`tab${drawer === "backtest" ? " on" : ""}`}
							onClick={() => setDrawer("backtest")}
							data-testid="drawer-tab-backtest"
						>
							Backtest
							{backtest ? <span className="count">1</span> : null}
						</button>
					</div>
					<div className={styles.drawerBody}>
						{drawer === "dryrun" ? (
							<DryRunPanel result={dryRun} mode={mode} catalogue={catalogue} />
						) : (
							<BacktestPanel result={backtest} mode={mode} catalogue={catalogue} />
						)}
					</div>
				</section>

				<div className={styles.narrow} data-testid="builder-narrow">
					<div className={styles.narrowCard}>
						<h2>The builder needs a wider screen</h2>
						<p className="small muted">
							The canvas, palette and details dock need at least 1024 px. This summary is
							read-only; nothing is hidden and nothing is edited behind your back.
						</p>
						<dl className={styles.narrowFacts}>
							<div>
								<dt>Strategy</dt>
								<dd>{meta.name || "Untitled graph"}</dd>
							</div>
							<div>
								<dt>Nodes</dt>
								<dd className="mono">{nodes.filter((node) => node.type !== "comment").length}</dd>
							</div>
							<div>
								<dt>Wires</dt>
								<dd className="mono">{edges.length}</dd>
							</div>
							<div>
								<dt>Groups</dt>
								<dd className="mono">{nodes.filter((node) => node.type === "comment").length}</dd>
							</div>
							<div>
								<dt>Validation</dt>
								<dd>
									{report
										? `${report.errors.length} error(s) · ${report.warnings.length} warning(s)`
										: "not validated"}
								</dd>
							</div>
							<div>
								<dt>Last dry run</dt>
								<dd>{dryRun ? `${dryRun.decision.action} · DRY_RUN` : "never"}</dd>
							</div>
						</dl>
						<div className={styles.narrowNodes}>
							{nodes
								.filter((node) => node.type !== "comment")
								.map((node) => (
									<span className={styles.narrowNode} key={node.id}>
										{node.data.nodeType}
									</span>
								))}
							{nodes.filter((node) => node.type !== "comment").length === 0 && (
								<span className="small muted">No nodes yet.</span>
							)}
						</div>
						<div className="row mt-12">
							<Link className="btn" href="/strategies">
								All strategies
							</Link>
						</div>
					</div>
				</div>

				{menu && catalogue && (
					<NodeMenu
						catalogue={catalogue}
						request={menu}
						onPick={(spec) => {
							if (!menu) return;
							applyConnectedPick(spec, menu);
							setMenu(null);
						}}
						onClose={() => setMenu(null)}
					/>
				)}

				{dialog && (
					<div className={styles.dialogBackdrop} onClick={() => setDialog(null)}>
						<div
							className={styles.dialog}
							onClick={(event) => event.stopPropagation()}
							role="dialog"
							aria-label={dialog.mode === "copy" ? "Graph JSON" : "Paste graph JSON"}
						>
							<div className={styles.dialogHead}>
								<strong>{dialog.mode === "copy" ? "Graph JSON" : "Paste graph JSON"}</strong>
								<span className="micro">schema {`ezchain.graph/1`}</span>
							</div>
							<textarea
								className={`textarea mono ${styles.dialogText}`}
								value={dialog.text}
								readOnly={dialog.mode === "copy"}
								spellCheck={false}
								aria-label="Graph document JSON"
								onChange={(event) => setDialog({ ...dialog, text: event.target.value })}
								data-testid="json-text"
							/>
							<div className={styles.dialogActions}>
								{dialog.mode === "copy" ? (
									<>
										<button
											className="btn sm"
											type="button"
											onClick={() => {
												void navigator.clipboard?.writeText(dialog.text).catch(() => undefined);
												setNotice({ tone: "ok", text: "Graph JSON copied to the clipboard." });
											}}
										>
											Copy to clipboard
										</button>
										<button
											className="btn ghost sm"
											type="button"
											onClick={() => setDialog(null)}
										>
											Close
										</button>
									</>
								) : (
									<>
										<button
											className="btn sm primary"
											type="button"
											onClick={() => applyPaste(dialog.text)}
											data-testid="apply-paste"
										>
											Apply
										</button>
										<button
											className="btn ghost sm"
											type="button"
											onClick={() => setDialog(null)}
										>
											Cancel
										</button>
									</>
								)}
							</div>
						</div>
					</div>
				)}
			</div>
		</NodeActionsContext.Provider>
	);
}

/* ------------------------------------------------------------------ right-hand dock */

function DetailsPanel({
	catalogue,
	node,
	selectedCount,
	trace,
}: {
	catalogue: GraphCatalogue | null;
	node: GraphFlowNode | null;
	selectedCount: number;
	trace: GraphTrace | null;
}) {
	const actions = useContext(NodeActionsContext);
	if (!catalogue) return null;
	if (!node) {
		return (
			<section className={styles.dockSection}>
				<div className={styles.dockHead}>Details</div>
				<div className={styles.dockBody}>
					<p className="small muted">
						Select a node to edit its parameters. {selectedCount > 1 ? `${selectedCount} selected.` : ""}
					</p>
				</div>
			</section>
		);
	}
	if (node.type === "comment") {
		return (
			<section className={styles.dockSection} data-testid="details-comment">
				<div className={styles.dockHead}>Group box</div>
				<div className={styles.dockBody}>
					<label className="label" htmlFor="group-label">
						Label
					</label>
					<input
						id="group-label"
						className="input"
						value={node.data.text ?? ""}
						onChange={(event) => actions?.setCommentText(node.id, event.target.value)}
					/>
					<p className="small muted mt-8">
						{Math.round(node.width ?? 0)} × {Math.round(node.height ?? 0)} px · stored in
						document.comments with ui x/y/w/h.
					</p>
				</div>
			</section>
		);
	}

	const spec = findNodeSpec(catalogue, node.data.nodeType);
	if (!spec) return null;
	const values = trace && node.id in trace.values ? trace.values[node.id] : undefined;
	return (
		<section className={styles.dockSection} data-testid="details-node">
			<div className={styles.dockHead}>
				<span>{spec.title}</span>
				<span className="micro">{spec.type}</span>
			</div>
			<div className={styles.dockBody}>
				<p className="small muted">{spec.description}</p>
				{spec.params.length > 0 && (
					<div className={styles.detailParams}>
						{spec.params.map((param) => (
							<div key={param.name} className="field">
								<label htmlFor={`detail-${param.name}`}>
									{param.title}
									{param.unit ? <span className="muted"> ({param.unit})</span> : null}
								</label>
								{param.kind === "select" && param.choices ? (
									<select
										id={`detail-${param.name}`}
										className="select"
										value={node.data.params[param.name] ?? String(param.default ?? "")}
										onChange={(event) =>
											actions?.setParam(node.id, param.name, event.target.value)
										}
									>
										{param.choices.map((choice) => (
											<option value={choice} key={choice}>
												{choice}
											</option>
										))}
									</select>
								) : (
									<input
										id={`detail-${param.name}`}
										className="input mono"
										type={param.kind === "number" ? "number" : "text"}
										step="any"
										min={param.min ?? undefined}
										max={param.max ?? undefined}
										value={node.data.params[param.name] ?? ""}
										placeholder={param.default === null || param.default === undefined ? "—" : String(param.default)}
										onChange={(event) =>
											actions?.setParam(node.id, param.name, event.target.value)
										}
									/>
								)}
								<span className="hint">
									{param.min !== null || param.max !== null
										? `range ${param.min ?? "—"} … ${param.max ?? "—"}`
										: param.required
											? "required"
											: "optional"}
								</span>
							</div>
						))}
					</div>
				)}
				<div className={styles.pinTable}>
					<div className={styles.pinTableHead}>
						<span>Inputs</span>
						<span>Outputs</span>
					</div>
					<div className={styles.pinTableRow}>
						<ul>
							{spec.inputs.map((input) => (
								<li key={input.name}>
									<i className={styles.pinDot} style={{ background: `var(--graph-${input.type})` }} /> {input.name}
									<span className="muted"> {input.type}</span>
									{!input.required && <span className="muted"> · optional</span>}
								</li>
							))}
							{spec.inputs.length === 0 && <li className="muted">none</li>}
						</ul>
						<ul>
							{spec.outputs.map((output) => (
								<li key={output.name}>
									<i className={styles.pinDot} style={{ background: `var(--graph-${output.type})` }} /> {output.name}
									<span className="muted"> {output.type}</span>
								</li>
							))}
							{spec.outputs.length === 0 && <li className="muted">none</li>}
						</ul>
					</div>
				</div>
				<div className={styles.dockFoot}>
					Last value <span className="mono">{plain(values ?? null)}</span>
				</div>
			</div>
		</section>
	);
}

function ProblemsPanel({
	report,
	validating,
	lastValidated,
	onFocus,
}: {
	report: ValidationReport | null;
	validating: boolean;
	lastValidated: string | null;
	onFocus: (nodeId: string) => void;
}) {
	return (
		<section className={styles.dockSection} data-testid="problems">
			<div className={styles.dockHead}>
				<span>Problems</span>
				<span className="micro">
					{validating
						? "validating…"
						: report
							? `${report.errors.length} error(s) · ${report.warnings.length} warning(s)`
							: "not validated"}
				</span>
			</div>
			<div className={styles.dockBody}>
				{!report ? (
					<p className="small muted">
						Press <kbd>F7</kbd> to validate. Unknown is never treated as pass.
					</p>
				) : (
					<>
						{Object.keys(report.warmup_bars).length > 0 && (
							<div className={styles.warmup}>
								{warmupText(report.warmup_bars)}
							</div>
						)}
						{report.errors.length === 0 && report.warnings.length === 0 && (
							<p className="small ok">
								No problems. Warnings would still not block a run.
								{lastValidated ? ` Validated ${lastValidated} UTC.` : ""}
							</p>
						)}
						<ul className={styles.problemList}>
							{report.errors.map((issue, index) => (
								<li key={`e-${issue.code}-${issue.node ?? "doc"}-${index}`}>
									<button
										type="button"
										className={`${styles.problem} ${styles.problemErr}`}
										onClick={() => issue.node && onFocus(issue.node)}
										disabled={!issue.node}
										data-testid={`problem-${issue.code}`}
									>
										<span className={styles.problemCode}>{issue.code}</span>
										<span>{issue.message}</span>
										{issue.node && <span className="micro">{issueLabel(issue)}</span>}
									</button>
								</li>
							))}
							{report.warnings.map((issue, index) => (
								<li key={`w-${issue.code}-${issue.node ?? "doc"}-${index}`}>
									<button
										type="button"
										className={`${styles.problem} ${styles.problemWarn}`}
										onClick={() => issue.node && onFocus(issue.node)}
										disabled={!issue.node}
										data-testid={`warning-${issue.code}`}
									>
										<span className={styles.problemCode}>{issue.code}</span>
										<span>{issue.message}</span>
										{issue.node && <span className="micro">{issueLabel(issue)}</span>}
									</button>
								</li>
							))}
						</ul>
					</>
				)}
			</div>
		</section>
	);
}

function warmupText(bars: Record<string, number>): string {
	return Object.entries(bars)
		.map(([timeframe, count]) => `${timeframe}: ${count} bars`)
		.join(" · ");
}

/* ---------------------------------------------------------------------- bottom drawer */

function EffectsTable({ effects }: { effects: GraphEffect[] }) {
	if (effects.length === 0) {
		return <p className="small muted">No effects: the exec path reached no action.</p>;
	}
	return (
		<div className="table-wrap">
			<table className="table compact">
				<thead>
					<tr>
						<th scope="col">#</th>
						<th scope="col">Effect</th>
						<th scope="col">Node</th>
						<th scope="col">Detail</th>
					</tr>
				</thead>
				<tbody>
					{effects.map((effect, index) => (
						<tr key={`${effect.node}-${index}`}>
							<td className="mono">{index + 1}</td>
							<td>{effect.kind}</td>
							<td className="mono">{effect.node}</td>
							<td className="mono">
								{effect.amount_usd ? `amount ${money(effect.amount_usd)}` : null}
								{effect.sell_fraction ? `sell ${effect.sell_fraction}` : null}
								{effect.message ? effect.message : null}
								{effect.name ? `var ${effect.name}` : null}
								{effect.suppressed ? " · SUPPRESSED_ONE_ORDER_PER_TICK" : null}
							</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function DryRunPanel({
	result,
	mode,
	catalogue,
}: {
	result: DryRunResult | null;
	mode: GraphMode;
	catalogue: GraphCatalogue | null;
}) {
	if (!result) {
		return (
			<p className="small muted">
				No dry run yet. A dry run evaluates one tick against live data and never places an
				order.
			</p>
		);
	}
	const decision = result.decision;
	return (
		<div className={styles.resultGrid} data-testid="dry-run-result">
			<div>
				<div className={styles.resultHead}>
					<Badge tone="info">DRY_RUN</Badge>
					{mode === "fixture" && <Badge tone="ghost">FIXTURE</Badge>}
					<Badge tone={decision.action === "hold" ? "ghost" : "ok"}>{decision.action}</Badge>
					<span className="small muted">{stamp(result.context.now)}</span>
				</div>
				<p className={styles.reason}>{decision.reason}</p>
				<dl className={styles.resultFacts}>
					<div>
						<dt>Amount</dt>
						<dd className="mono">{money(decision.amount_usd)}</dd>
					</div>
					<div>
						<dt>Sell fraction</dt>
						<dd className="mono">{plain(decision.sell_fraction)}</dd>
					</div>
					<div>
						<dt>Price</dt>
						<dd className="mono">{money(result.context.price_usd)}</dd>
					</div>
					<div>
						<dt>Candles</dt>
						<dd className="mono">
							{Object.entries(result.context.candles)
								.map(([timeframe, candles]) =>
									`${timeframe}: ${candles.bars}${candles.stale ? " (stale)" : ""}`,
								)
								.join(" · ") || "—"}
						</dd>
					</div>
				</dl>
				<div className={styles.execPath}>
					<div className="micro">Exec path</div>
					<div className={styles.execList}>
						{result.trace.exec.length === 0 && <span className="small muted">—</span>}
						{result.trace.exec.map((id, index) => (
							<span className={styles.execChip} key={`${id}-${index}`}>
								{index + 1}. {id}
							</span>
						))}
					</div>
				</div>
			</div>
			<div>
				<div className="micro mb-8">Effects</div>
				<EffectsTable effects={decision.effects} />
				<div className="micro mt-12 mb-8">Trace values</div>
				<TraceTable values={result.trace.values} catalogue={catalogue} />
			</div>
		</div>
	);
}

function TraceTable({
	values,
	catalogue,
}: {
	values: GraphTraceValues;
	catalogue: GraphCatalogue | null;
}) {
	const entries = Object.entries(values);
	if (entries.length === 0) return <p className="small muted">—</p>;
	return (
		<div className={`table-wrap ${styles.traceTable}`}>
			<table className="table compact">
				<thead>
					<tr>
						<th scope="col">Node</th>
						<th scope="col">Type</th>
						<th scope="col">Last value</th>
					</tr>
				</thead>
				<tbody>
					{entries.map(([id, value]) => (
						<tr key={id}>
							<td className="mono">{id}</td>
							<td className="small muted">
								{catalogue?.nodes.find((node) => node.type === id)?.title ?? "—"}
							</td>
							<td className="mono">{plain(value)}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function EquityChart({ points }: { points: BacktestEquityPoint[] }) {
	const usable = points
		.map((point) => ({ ...point, value: Number(point.equity_usd), dd: Number(point.drawdown_pct ?? 0) }))
		.filter((point) => Number.isFinite(point.value));
	if (usable.length < 2) return <div className="small muted">—</div>;
	const values = usable.map((point) => point.value);
	const min = Math.min(...values);
	const max = Math.max(...values);
	const span = max - min || 1;
	const width = 640;
	const height = 150;
	const top = 10;
	const bottom = 110;
	const coords = usable.map((point, index) => {
		const x = (index / (usable.length - 1)) * width;
		const y = bottom - ((point.value - min) / span) * (bottom - top);
		return `${x.toFixed(1)},${y.toFixed(1)}`;
	});
	const worstDrawdown = Math.min(-0.01, ...usable.map((point) => point.dd));
	const drawdown = usable.map((point, index) => {
		const x = (index / (usable.length - 1)) * width;
		const y = 138 - (Math.abs(Math.min(0, point.dd)) / Math.abs(worstDrawdown)) * 22;
		return `${x.toFixed(1)},${y.toFixed(1)}`;
	});
	return (
		<svg
			className={styles.equity}
			viewBox={`0 0 ${width} ${height}`}
			role="img"
			aria-label="Equity curve with drawdown"
			data-testid="equity-chart"
		>
			<line x1={0} y1={bottom} x2={width} y2={bottom} className={styles.axis} />
			<polyline points={drawdown.join(" ")} className={styles.ddLine} />
			<polyline points={coords.join(" ")} className={styles.equityLine} />
			<text x={0} y={top + 4} className={styles.axisText}>
				{money(max.toFixed(2))}
			</text>
			<text x={0} y={bottom - 2} className={styles.axisText}>
				{money(min.toFixed(2))}
			</text>
			<text x={width - 4} y={146} textAnchor="end" className={styles.axisText}>
				drawdown ≤ {worstDrawdown.toFixed(2)}%
			</text>
		</svg>
	);
}

function BacktestPanel({
	result,
	mode,
	catalogue,
}: {
	result: Backtest | null;
	mode: GraphMode;
	catalogue: GraphCatalogue | null;
}) {
	if (!result) {
		return (
			<p className="small muted">
				No backtest yet. A backtest replays closed bars with a declared fill model, fee and
				slippage.
			</p>
		);
	}
	const metrics: Array<[string, string]> = [
		["Net PnL", money(result.metrics.net_pnl_usd)],
		["Return", result.metrics.return_pct === null ? "—" : `${plain(result.metrics.return_pct)}%`],
		[
			"Max drawdown",
			result.metrics.max_drawdown_pct === null ? "—" : `${plain(result.metrics.max_drawdown_pct)}%`,
		],
		[
			"Win rate",
			result.metrics.win_rate_pct === null ? "—" : `${plain(result.metrics.win_rate_pct)}%`,
		],
		["Profit factor", plain(result.metrics.profit_factor)],
		["Trades", plain(result.metrics.trades)],
		["Exposure", result.metrics.exposure_pct === null ? "—" : `${plain(result.metrics.exposure_pct)}%`],
		["Sharpe", plain(result.metrics.sharpe)],
	];
	return (
		<div className={styles.backtest} data-testid="backtest-result">
			<div className={styles.backtestTop}>
				<div className={styles.resultHead}>
					<Badge tone="info">BACKTEST</Badge>
					{mode === "fixture" && <Badge tone="ghost">FIXTURE</Badge>}
					<span className="small muted">
						{result.fill_model ?? "fill model —"} · {result.data_source ?? "data source —"}
					</span>
				</div>
				<div className={styles.metrics}>
					{metrics.map(([label, value]) => (
						<div className={styles.metric} key={label}>
							<span className="micro">{label}</span>
							<span className="mono">{value}</span>
						</div>
					))}
				</div>
				<EquityChart points={result.equity} />
			</div>
			<div className={styles.backtestBottom}>
				<div>
					<div className="micro mb-8">Trades</div>
					<TradesTable trades={result.trades} />
				</div>
				<div>
					<div className="micro mb-8">Node hits</div>
					<NodeHits hits={result.node_hits} catalogue={catalogue} />
				</div>
			</div>
		</div>
	);
}

function TradesTable({ trades }: { trades: BacktestTrade[] }) {
	if (trades.length === 0) return <p className="small muted">No trades in the sample.</p>;
	return (
		<div className={`table-wrap ${styles.trades}`}>
			<table className="table compact">
				<thead>
					<tr>
						<th scope="col">Entry</th>
						<th scope="col">Exit</th>
						<th scope="col">Qty</th>
						<th scope="col" className="num">
							In
						</th>
						<th scope="col" className="num">
							Out
						</th>
						<th scope="col" className="num">
							PnL
						</th>
						<th scope="col">Reason</th>
					</tr>
				</thead>
				<tbody>
					{trades.map((trade, index) => (
						<tr key={`${trade.entry_time}-${index}`}>
							<td className="mono">{trade.entry_time.slice(5, 16).replace("T", " ")}</td>
							<td className="mono">{trade.exit_time ? trade.exit_time.slice(5, 16).replace("T", " ") : "—"}</td>
							<td className="mono">{trade.qty}</td>
							<td className="mono num">{trade.entry_price}</td>
							<td className="mono num">{trade.exit_price}</td>
							<td className={`mono num ${Number(trade.pnl_usd) < 0 ? "err" : "ok"}`}>
								{money(trade.pnl_usd)}
							</td>
							<td className="small muted">{trade.reason ?? "—"}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function NodeHits({
	hits,
	catalogue,
}: {
	hits: Record<string, number>;
	catalogue: GraphCatalogue | null;
}) {
	const entries = Object.entries(hits).sort((a, b) => b[1] - a[1]);
	if (entries.length === 0) return <p className="small muted">—</p>;
	return (
		<ul className={styles.hits}>
			{entries.map(([id, count]) => (
				<li key={id}>
					<span className="mono">{id}</span>
					<span className="muted small">
						{catalogue?.nodes.find((node) => node.type === id)?.title ?? ""}
					</span>
					<span className="mono">{count}</span>
				</li>
			))}
		</ul>
	);
}
