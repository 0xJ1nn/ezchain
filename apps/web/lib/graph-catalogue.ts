// Catalogue lookups shared by the validator, the connection rules and the node menu.
// Everything is derived from the catalogue the API serves (§9) — the editor never
// hardcodes a node type, a port or a param.

import type { CatalogueNode, GraphCatalogue, GraphPinType } from "./graph-types";

export function findNodeSpec(
	catalogue: GraphCatalogue,
	type: string,
): CatalogueNode | undefined {
	return catalogue.nodes.find((node) => node.type === type);
}

export function outputPinType(
	catalogue: GraphCatalogue,
	type: string,
	port: string,
): GraphPinType | null {
	const spec = findNodeSpec(catalogue, type);
	return spec?.outputs.find((output) => output.name === port)?.type ?? null;
}

export function inputPinType(
	catalogue: GraphCatalogue,
	type: string,
	port: string,
): GraphPinType | null {
	const spec = findNodeSpec(catalogue, type);
	return spec?.inputs.find((input) => input.name === port)?.type ?? null;
}

/**
 * §2: the types must match exactly, except a `series` output may feed a `number` input
 * (read as its last value). This is the only implicit cast.
 */
export function pinsCompatible(source: GraphPinType, target: GraphPinType): boolean {
	return source === target || (source === "series" && target === "number");
}

/** Can `nodeType` receive a wire of `sourceType`? (drag from an output pin) */
export function acceptsType(
	catalogue: GraphCatalogue,
	nodeType: string,
	sourceType: GraphPinType,
): boolean {
	const spec = findNodeSpec(catalogue, nodeType);
	if (!spec) return false;
	return spec.inputs.some((input) => pinsCompatible(sourceType, input.type));
}

/** Can `nodeType` feed an input of `targetType`? (drag from an input pin) */
export function producesType(
	catalogue: GraphCatalogue,
	nodeType: string,
	targetType: GraphPinType,
): boolean {
	const spec = findNodeSpec(catalogue, nodeType);
	if (!spec) return false;
	return spec.outputs.some((output) => pinsCompatible(output.type, targetType));
}

export function categoryTitle(catalogue: GraphCatalogue, id: string): string {
	return catalogue.categories.find((category) => category.id === id)?.title ?? id;
}

/** One-line wiring signature for the palette: `series → series, value`. */
export function pinSignature(catalogue: GraphCatalogue, node: CatalogueNode): string {
	const inputs = node.inputs.map((input) => `in ${input.name}:${input.type}`).join(", ");
	const outputs = node.outputs.map((output) => `${output.name}:${output.type}`).join(", ");
	if (!inputs && !outputs) return "—";
	if (!inputs) return outputs;
	return `${inputs} → ${outputs}`;
}

export function searchNodes(catalogue: GraphCatalogue, query: string): CatalogueNode[] {
	const trimmed = query.trim().toLowerCase();
	if (!trimmed) return catalogue.nodes;
	const words = trimmed.split(/\s+/);
	return catalogue.nodes.filter((node) => {
		const haystack = [
			node.type,
			node.title,
			node.description,
			categoryTitle(catalogue, node.category),
			...node.inputs.map((input) => `${input.name} ${input.type}`),
			...node.outputs.map((output) => `${output.name} ${output.type}`),
			...node.params.map((param) => `${param.name} ${param.title}`),
		]
			.join(" ")
			.toLowerCase();
		return words.every((word) => haystack.includes(word));
	});
}
