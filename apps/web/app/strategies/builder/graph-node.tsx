"use client";

// The custom node: a compact instrument panel with typed pins.
//
// Shape encodes the pin kind (exec = pentagon, bool = diamond, number = circle,
// series = square) and colour encodes the type, so the graph survives greyscale and a
// zoomed-out screenshot. Filled = connected, hollow = dangling — the free "what is still
// unconnected" audit from the Blueprint brief.

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { createContext, useContext, type CSSProperties } from "react";
import { categoryTitle, findNodeSpec } from "@/lib/graph-catalogue";
import { COMMENT_NODE_TYPE, type GraphParamValue } from "@/lib/graph-doc";
import type { CatalogueNode, CatalogueParam, GraphCatalogue, GraphPinType } from "@/lib/graph-types";
import styles from "./builder.module.css";

export type GraphNodeData = {
	nodeType: string;
	params: Record<string, GraphParamValue>;
	/** Comment boxes carry their label here. */
	text?: string;
	connectedIn?: string[];
	connectedOut?: string[];
	error?: string | null;
	warning?: string | null;
	/** Output pin values from the last trace (§4), `null` when the value is unknown. */
	values?: Record<string, string | null>;
	traced?: boolean;
	/** Cheap comparison so the editor only replaces a node when its look really changed. */
	signature?: string;
};

export type GraphFlowNode = Node<GraphNodeData>;

export interface NodeActions {
	catalogue: GraphCatalogue;
	setParam: (nodeId: string, name: string, value: string) => void;
	setCommentText: (nodeId: string, text: string) => void;
}

export const NodeActionsContext = createContext<NodeActions | null>(null);

export function formatPinValue(value: string | null | undefined): string {
	if (value === null || value === undefined || value === "") return "—";
	return value;
}

/**
 * Pin appearance is inline so it cannot lose a specificity fight with React Flow's base
 * stylesheet: shape carries the kind, hue carries the type, fill carries the state.
 * Exec/bool are drawn with an inset shadow because clip-path would clip a real border.
 */
function pinStyle(type: GraphPinType, connected: boolean): CSSProperties {
	const colour = `var(--graph-${type})`;
	if (type === "exec") {
		return {
			width: 13,
			height: 11,
			border: "none",
			borderRadius: 0,
			clipPath: "polygon(0 0, 62% 0, 100% 50%, 62% 100%, 0 100%)",
			background: connected ? colour : "var(--graph-surface)",
			boxShadow: `inset 0 0 0 1.5px ${colour}`,
		};
	}
	if (type === "bool") {
		return {
			width: 11,
			height: 11,
			border: "none",
			borderRadius: 0,
			clipPath: "polygon(50% 0, 100% 50%, 50% 100%, 0 50%)",
			background: connected ? colour : "var(--graph-surface)",
			boxShadow: `inset 0 0 0 1.5px ${colour}`,
		};
	}
	return {
		width: 10,
		height: 10,
		border: `1.5px solid ${colour}`,
		borderRadius: type === "number" ? "50%" : 1,
		background: connected ? colour : "var(--graph-surface)",
	};
}

function ParamEditor({
	nodeId,
	spec,
	param,
	value,
	setParam,
}: {
	nodeId: string;
	spec: CatalogueNode;
	param: CatalogueParam;
	value: GraphParamValue;
	setParam: (nodeId: string, name: string, value: string) => void;
}) {
	const label = (
		<label className={styles.paramLabel} htmlFor={`${nodeId}-${spec.type}-${param.name}`}>
			{param.title}
			{param.unit ? <span className={styles.unit}> {param.unit}</span> : null}
		</label>
	);
	const id = `${nodeId}-${spec.type}-${param.name}`;
	if (param.kind === "select" && param.choices) {
		return (
			<div className={styles.param}>
				{label}
				<select
					className={`${styles.paramInput} nodrag`}
					id={id}
					value={String(value || param.default || "")}
					onChange={(event) => setParam(nodeId, param.name, event.target.value)}
				>
					{param.choices.map((choice) => (
						<option value={choice} key={choice}>
							{choice}
						</option>
					))}
				</select>
			</div>
		);
	}
	return (
		<div className={styles.param}>
			{label}
			<input
				className={`${styles.paramInput} nodrag`}
				id={id}
				type={param.kind === "number" ? "number" : "text"}
				inputMode={param.kind === "number" ? "decimal" : undefined}
				min={param.min ?? undefined}
				max={param.max ?? undefined}
				step="any"
				value={String(value ?? "")}
				placeholder={param.default === null || param.default === undefined ? "—" : String(param.default)}
				onChange={(event) => setParam(nodeId, param.name, event.target.value)}
			/>
		</div>
	);
}

export function GraphNodeView({ id, data, selected }: NodeProps<GraphFlowNode>) {
	const actions = useContext(NodeActionsContext);
	if (!actions) return null;
	const spec = findNodeSpec(actions.catalogue, data.nodeType);
	if (!spec) {
		return (
			<div className={`${styles.node} ${styles.nodeUnknown}`} data-testid={`node-${data.nodeType}`}>
				<div className={styles.nodeHead}>
					<span className={styles.nodeTitle}>Unknown</span>
				</div>
				<div className={styles.nodeBody}>{data.nodeType}</div>
			</div>
		);
	}

	const connectedIn = new Set(data.connectedIn ?? []);
	const connectedOut = new Set(data.connectedOut ?? []);
	const firstDataOutput = spec.outputs.find((output) => output.type !== "exec")?.name ?? null;
	const rowCount = Math.max(spec.inputs.length, spec.outputs.length);

	return (
		<div
			className={[
				styles.node,
				selected ? styles.nodeSelected : "",
				data.error ? styles.nodeError : "",
				data.traced ? styles.nodeTraced : "",
			].join(" ")}
			data-testid={`node-${spec.type}`}
			data-node-id={id}
			data-params={JSON.stringify(data.params)}
		>
			<div className={`${styles.nodeHead} ${styles[`cat_${spec.category}`] ?? ""}`}>
				<span className={styles.nodeCategory}>{categoryTitle(actions.catalogue, spec.category)}</span>
				<span className={styles.nodeTitle}>{spec.title}</span>
				<span className={styles.nodeType}>{spec.type}</span>
			</div>

			{data.error ? (
				<div className={`${styles.nodeIssue} ${styles.nodeIssueError}`} role="note">
					<span aria-hidden="true">!</span> {data.error}
				</div>
			) : data.warning ? (
				<div className={`${styles.nodeIssue} ${styles.nodeIssueWarn}`} role="note">
					<span aria-hidden="true">△</span> {data.warning}
				</div>
			) : null}

			{rowCount > 0 && (
				<div className={styles.pinRows}>
					{Array.from({ length: rowCount }).map((_, index) => {
						const input = spec.inputs[index];
						const output = spec.outputs[index];
						const inputConnected = input ? connectedIn.has(input.name) : false;
						const outputConnected = output ? connectedOut.has(output.name) : false;
						return (
							<div className={styles.pinRow} key={index}>
								<span className={styles.pinSide}>
									{input ? (
										<>
											<Handle
												id={input.name}
												type="target"
												position={Position.Left}
												style={{
													...pinStyle(input.type, inputConnected),
													left: -8,
													top: "50%",
													transform: "translateY(-50%)",
												}}
												title={`${input.name}: ${input.type}${input.required ? " (required)" : ""}`}
												aria-label={`${spec.title} input ${input.name} ${input.type}`}
											/>
											<span className={styles.pinName}>{input.name}</span>
											{!input.required && <span className={styles.optional}>opt</span>}
										</>
									) : null}
								</span>
								<span className={`${styles.pinSide} ${styles.pinSideOut}`}>
									{output ? (
										<>
											<span className={styles.pinName}>{output.name}</span>
											{firstDataOutput === output.name && data.values?.[output.name] !== undefined && (
												<span className={styles.valueChip} data-testid={`chip-${id}-${output.name}`}>
													{formatPinValue(data.values?.[output.name] ?? null)}
												</span>
											)}
											<Handle
												id={output.name}
												type="source"
												position={Position.Right}
												style={{
													...pinStyle(output.type, outputConnected),
													right: -8,
													top: "50%",
													transform: "translateY(-50%)",
												}}
												title={`${output.name}: ${output.type}`}
												aria-label={`${spec.title} output ${output.name} ${output.type}`}
											/>
										</>
									) : null}
								</span>
							</div>
						);
					})}
				</div>
			)}

			{spec.params.length > 0 && (
				<div className={styles.params}>
					{spec.params.map((param) => (
						<ParamEditor
							key={param.name}
							nodeId={id}
							spec={spec}
							param={param}
							value={data.params[param.name] ?? (param.default === null || param.default === undefined ? "" : String(param.default))}
							setParam={actions.setParam}
						/>
					))}
				</div>
			)}
		</div>
	);
}

export function CommentNodeView({ id, data, selected }: NodeProps<GraphFlowNode>) {
	return (
		<div
			className={`${styles.comment} ${selected ? styles.commentSelected : ""}`}
			data-testid="comment-node"
			data-node-id={id}
		>
			<div className={styles.commentLabel}>{data.text || "Group"}</div>
		</div>
	);
}

export { COMMENT_NODE_TYPE };
