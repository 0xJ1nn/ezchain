// The graph document — one schema for the editor, the clipboard and persistence.
//
// `toDocument` / `fromDocument` are pure: no React, no I/O, no evaluation. The persisted
// shape is exactly docs/STRATEGY-GRAPH.md §1 — `{schema, nodes, edges, comments}` with
// `nodes[] = {id, type, params, ui}` and `edges[] = {from: {node, port}, to: {node, port}}`.
// `ui` and `comments` are presentation only; the evaluator ignores them.
//
// Unknown values never appear here: a param that the user cleared is omitted so the
// catalogue default (or a required-param error) applies, rather than sending an empty
// string that a validator would have to guess about.

export const GRAPH_SCHEMA = "ezchain.graph/1" as const;
export const COMMENT_NODE_TYPE = "comment" as const;
export const NODE_ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

/** §1 limits. Enforced by the validator; the editor also refuses to exceed them. */
export const GRAPH_LIMITS = {
	nodes: 200,
	edges: 400,
	comments: 50,
	bytes: 262_144,
} as const;

export type GraphParamValue = string | number;

export interface GraphNodeDoc {
	id: string;
	type: string;
	params: Record<string, GraphParamValue>;
	ui: { x: number; y: number };
}

export interface GraphEdgeDoc {
	from: { node: string; port: string };
	to: { node: string; port: string };
}

export interface GraphCommentDoc {
	id: string;
	text: string;
	ui: { x: number; y: number; w: number; h: number };
}

export interface GraphDocument {
	schema: typeof GRAPH_SCHEMA;
	nodes: GraphNodeDoc[];
	edges: GraphEdgeDoc[];
	comments: GraphCommentDoc[];
}

/**
 * Editor-side node. Param values keep the JSON type they came in with (a number stays a
 * number until the user edits it, then it is the string they typed), so a document that is
 * loaded and saved again is byte-identical.
 */
export interface BuilderNode {
	id: string;
	/** A catalogue node type, or `comment` for a labelled group box. */
	type: string;
	params: Record<string, GraphParamValue>;
	position: { x: number; y: number };
	text?: string;
	width?: number;
	height?: number;
}

export interface BuilderEdge {
	id: string;
	source: string;
	sourcePort: string;
	target: string;
	targetPort: string;
}

export interface FlowState {
	nodes: BuilderNode[];
	edges: BuilderEdge[];
}

export const COMMENT_DEFAULT_SIZE = { w: 420, h: 260 } as const;

export function edgeId(
	source: string,
	sourcePort: string,
	target: string,
	targetPort: string,
): string {
	return `${source}.${sourcePort}->${target}.${targetPort}`;
}

/** Round to whole pixels: React Flow positions are fractional during a drag. */
function coordinate(value: number | undefined): number {
	return Number.isFinite(value) ? Math.round(value as number) : 0;
}

function paramEntries(params: Record<string, GraphParamValue>): Record<string, GraphParamValue> {
	const out: Record<string, GraphParamValue> = {};
	for (const [key, value] of Object.entries(params)) {
		if (value === undefined || value === null) continue;
		// A cleared field is omitted: the catalogue default (or a required-param error)
		// applies, rather than sending an empty string the validator would have to guess at.
		if (typeof value === "string" && value.trim() === "") continue;
		out[key] = value;
	}
	return out;
}

/** Editor state → §1 document. Comments become `document.comments`, never nodes. */
export function toDocument(state: FlowState): GraphDocument {
	const nodes: GraphNodeDoc[] = [];
	const comments: GraphCommentDoc[] = [];
	for (const node of state.nodes) {
		if (node.type === COMMENT_NODE_TYPE) {
			comments.push({
				id: node.id,
				text: node.text ?? "",
				ui: {
					x: coordinate(node.position.x),
					y: coordinate(node.position.y),
					w: coordinate(node.width ?? COMMENT_DEFAULT_SIZE.w),
					h: coordinate(node.height ?? COMMENT_DEFAULT_SIZE.h),
				},
			});
			continue;
		}
		nodes.push({
			id: node.id,
			type: node.type,
			params: paramEntries(node.params),
			ui: { x: coordinate(node.position.x), y: coordinate(node.position.y) },
		});
	}
	const edges: GraphEdgeDoc[] = state.edges.map((edge) => ({
		from: { node: edge.source, port: edge.sourcePort },
		to: { node: edge.target, port: edge.targetPort },
	}));
	return { schema: GRAPH_SCHEMA, nodes, edges, comments };
}

/** §1 document → editor state. The inverse of `toDocument` for a well-formed document. */
export function fromDocument(document: GraphDocument): FlowState {
	const nodes: BuilderNode[] = document.nodes.map((node) => ({
		id: node.id,
		type: node.type,
		params: { ...node.params },
		position: { x: node.ui?.x ?? 0, y: node.ui?.y ?? 0 },
	}));
	for (const comment of document.comments ?? []) {
		nodes.push({
			id: comment.id,
			type: COMMENT_NODE_TYPE,
			params: {},
			position: { x: comment.ui?.x ?? 0, y: comment.ui?.y ?? 0 },
			text: comment.text ?? "",
			width: comment.ui?.w ?? COMMENT_DEFAULT_SIZE.w,
			height: comment.ui?.h ?? COMMENT_DEFAULT_SIZE.h,
		});
	}
	const edges: BuilderEdge[] = (document.edges ?? []).map((edge) => ({
		id: edgeId(edge.from.node, edge.from.port, edge.to.node, edge.to.port),
		source: edge.from.node,
		sourcePort: edge.from.port,
		target: edge.to.node,
		targetPort: edge.to.port,
	}));
	return { nodes, edges };
}

export type ParseResult =
	| { ok: true; document: GraphDocument }
	| { ok: false; message: string };

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Parse pasted text or an API payload into a document. Structural checks only — the
 * semantic checks (types, cycles, reachability) belong to the validator. A document that
 * fails here is never loaded as a half-graph.
 */
export function parseGraphDocument(text: string | unknown): ParseResult {
	let raw: unknown = text;
	if (typeof text === "string") {
		try {
			raw = JSON.parse(text);
		} catch {
			return { ok: false, message: "The text is not valid JSON." };
		}
	}
	if (!isRecord(raw)) return { ok: false, message: "The document must be a JSON object." };
	if (raw.schema !== GRAPH_SCHEMA) {
		return { ok: false, message: `The document schema must be "${GRAPH_SCHEMA}".` };
	}
	if (!Array.isArray(raw.nodes)) return { ok: false, message: "The document has no nodes array." };
	if (!Array.isArray(raw.edges)) return { ok: false, message: "The document has no edges array." };
	if (raw.comments !== undefined && !Array.isArray(raw.comments)) {
		return { ok: false, message: "The comments field must be an array." };
	}

	const nodes: GraphNodeDoc[] = [];
	for (const entry of raw.nodes) {
		if (!isRecord(entry)) return { ok: false, message: "Every node must be an object." };
		if (typeof entry.id !== "string" || !NODE_ID_PATTERN.test(entry.id)) {
			return { ok: false, message: `Invalid node id: ${String(entry.id)}` };
		}
		if (typeof entry.type !== "string" || entry.type === "") {
			return { ok: false, message: `Node ${entry.id} has no type.` };
		}
		const params = isRecord(entry.params) ? entry.params : {};
		const ui = isRecord(entry.ui) ? entry.ui : {};
		nodes.push({
			id: entry.id,
			type: entry.type,
			params: params as Record<string, GraphParamValue>,
			ui: { x: Number(ui.x ?? 0), y: Number(ui.y ?? 0) },
		});
	}

	const edges: GraphEdgeDoc[] = [];
	for (const entry of raw.edges) {
		if (!isRecord(entry) || !isRecord(entry.from) || !isRecord(entry.to)) {
			return { ok: false, message: "Every edge must have from/to objects." };
		}
		const from = entry.from;
		const to = entry.to;
		if (typeof from.node !== "string" || typeof from.port !== "string") {
			return { ok: false, message: "Every edge needs from.node and from.port." };
		}
		if (typeof to.node !== "string" || typeof to.port !== "string") {
			return { ok: false, message: "Every edge needs to.node and to.port." };
		}
		edges.push({
			from: { node: from.node, port: from.port },
			to: { node: to.node, port: to.port },
		});
	}

	const comments: GraphCommentDoc[] = [];
	for (const entry of (raw.comments ?? []) as unknown[]) {
		if (!isRecord(entry)) return { ok: false, message: "Every comment must be an object." };
		if (typeof entry.id !== "string" || !NODE_ID_PATTERN.test(entry.id)) {
			return { ok: false, message: `Invalid comment id: ${String(entry.id)}` };
		}
		const ui = isRecord(entry.ui) ? entry.ui : {};
		comments.push({
			id: entry.id,
			text: typeof entry.text === "string" ? entry.text : "",
			ui: {
				x: Number(ui.x ?? 0),
				y: Number(ui.y ?? 0),
				w: Number(ui.w ?? COMMENT_DEFAULT_SIZE.w),
				h: Number(ui.h ?? COMMENT_DEFAULT_SIZE.h),
			},
		});
	}

	return {
		ok: true,
		document: { schema: GRAPH_SCHEMA, nodes, edges, comments },
	};
}

/** A fresh, unique node id. Kept short and readable so the JSON stays reviewable. */
export function nextNodeId(existing: Iterable<string>, seed: string): string {
	const taken = new Set(existing);
	const base =
		seed
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "_")
			.replace(/^_+|_+$/g, "")
			.slice(0, 24) || "node";
	if (!taken.has(base)) return base;
	for (let i = 2; i < 1000; i += 1) {
		const candidate = `${base}_${i}`.slice(0, 40);
		if (!taken.has(candidate)) return candidate;
	}
	return `n_${Date.now().toString(36)}`.slice(0, 40);
}

/** The subset of the graph that is selected, plus the edges entirely inside it. */
export function selectionToDocument(state: FlowState, selection: Iterable<string>): GraphDocument {
	const ids = new Set(selection);
	const subset: FlowState = {
		nodes: state.nodes.filter((node) => ids.has(node.id)),
		edges: state.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
	};
	return toDocument(subset);
}

export interface MergeResult {
	nodes: BuilderNode[];
	edges: BuilderEdge[];
	ids: string[];
}

/**
 * Merge a document into the current graph without clobbering live ids. Colliding ids are
 * suffixed, positions are offset so the pasted block is visible, and internal edges are
 * re-pointed at the new ids.
 */
export function mergeDocument(
	state: FlowState,
	document: GraphDocument,
	offset = { x: 48, y: 48 },
): MergeResult {
	const incoming = fromDocument(document);
	const taken = new Set(state.nodes.map((node) => node.id));
	const map = new Map<string, string>();

	for (const node of incoming.nodes) {
		let id = node.id;
		if (taken.has(id)) {
			const base = id.slice(0, 30);
			let counter = 2;
			while (taken.has(`${base}_${counter}`.slice(0, 40))) counter += 1;
			id = `${base}_${counter}`.slice(0, 40);
		}
		taken.add(id);
		map.set(node.id, id);
	}

	const nodes = incoming.nodes.map((node) => ({
		...node,
		id: map.get(node.id) ?? node.id,
		position: { x: node.position.x + offset.x, y: node.position.y + offset.y },
		params: { ...node.params },
	}));
	const edges = incoming.edges.map((edge) => {
		const source = map.get(edge.source) ?? edge.source;
		const target = map.get(edge.target) ?? edge.target;
		return {
			id: edgeId(source, edge.sourcePort, target, edge.targetPort),
			source,
			sourcePort: edge.sourcePort,
			target,
			targetPort: edge.targetPort,
		};
	});
	return { nodes, edges, ids: nodes.map((node) => node.id) };
}

