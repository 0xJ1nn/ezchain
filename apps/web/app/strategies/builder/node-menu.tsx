"use client";

// The context menu: one index behind right-click, Space and drag-to-empty.
//
// When it is opened from a pin it only lists node types with a compatible port — the
// list is short for a reason, and the reason is printed in the header. Picking a node
// creates it at the drop point and wires it in one gesture.

import { useEffect, useMemo, useRef, useState } from "react";
import { acceptsType, producesType, searchNodes } from "@/lib/graph-catalogue";
import type { CatalogueNode, GraphCatalogue, GraphPinType } from "@/lib/graph-types";
import styles from "./builder.module.css";

export interface MenuRequest {
	/** Viewport coordinates for the panel. */
	x: number;
	y: number;
	/** Flow coordinates for the node that will be created. */
	flowX: number;
	flowY: number;
	filter: { type: GraphPinType; direction: "source" | "target" } | null;
	from: { nodeId: string; port: string } | null;
}

interface Group {
	category: string;
	title: string;
	nodes: CatalogueNode[];
}

export function NodeMenu({
	catalogue,
	request,
	onPick,
	onClose,
}: {
	catalogue: GraphCatalogue;
	request: MenuRequest;
	onPick: (node: CatalogueNode) => void;
	onClose: () => void;
}) {
	const [query, setQuery] = useState("");
	const [active, setActive] = useState(0);
	const inputRef = useRef<HTMLInputElement>(null);

	useEffect(() => {
		inputRef.current?.focus();
	}, []);

	const groups = useMemo<Group[]>(() => {
		const filter = request.filter;
		const pool = catalogue.nodes.filter((node) => {
			if (!filter) return true;
			if (filter.direction === "source") return acceptsType(catalogue, node.type, filter.type);
			return producesType(catalogue, node.type, filter.type);
		});
		const matched = new Set(searchNodes({ ...catalogue, nodes: pool }, query).map((node) => node.type));
		return catalogue.categories
			.map((category) => ({
				category: category.id,
				title: category.title,
				nodes: pool.filter((node) => node.category === category.id && matched.has(node.type)),
			}))
			.filter((group) => group.nodes.length > 0);
	}, [catalogue, query, request.filter]);

	const flat = useMemo(() => groups.flatMap((group) => group.nodes), [groups]);

	useEffect(() => {
		setActive(0);
	}, [query]);

	const filterLabel = request.filter
		? request.filter.direction === "source"
			? `Accepting a ${request.filter.type} wire`
			: `Feeding a ${request.filter.type} input`
		: "All nodes";

	const left = Math.max(8, Math.min(request.x, (globalThis.innerWidth ?? 1440) - 356));
	const top = Math.max(8, Math.min(request.y, (globalThis.innerHeight ?? 900) - 420));

	return (
		<div className={styles.menuOverlay} onClick={onClose} data-testid="node-menu">
			<div
				className={styles.menu}
				style={{ left, top }}
				onClick={(event) => event.stopPropagation()}
				role="dialog"
				aria-label="Add node"
			>
				<div className={styles.menuHead}>
					<input
						ref={inputRef}
						className={styles.menuSearch}
						placeholder="Search nodes…"
						aria-label="Search nodes"
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						onKeyDown={(event) => {
							if (event.key === "ArrowDown") {
								event.preventDefault();
								setActive((current) => Math.min(current + 1, flat.length - 1));
							} else if (event.key === "ArrowUp") {
								event.preventDefault();
								setActive((current) => Math.max(current - 1, 0));
							} else if (event.key === "Enter") {
								event.preventDefault();
								const picked = flat[active];
								if (picked) onPick(picked);
							} else if (event.key === "Escape") {
								event.preventDefault();
								onClose();
							}
						}}
					/>
					<div className={styles.menuFilter} data-testid="node-menu-filter">
						{filterLabel}
					</div>
				</div>
				<div className={styles.menuList}>
					{groups.length === 0 && (
						<div className={styles.menuEmpty}>No node type has a compatible port.</div>
					)}
					{groups.map((group) => (
						<div key={group.category}>
							<div className={styles.menuCategory}>{group.title}</div>
							{group.nodes.map((node) => {
								const index = flat.indexOf(node);
								return (
									<button
										key={node.type}
										type="button"
										className={`${styles.menuItem} ${index === active ? styles.menuItemActive : ""}`}
										onMouseEnter={() => setActive(index)}
										onClick={() => onPick(node)}
										data-testid={`menu-item-${node.type}`}
									>
										<span className={styles.menuItemTitle}>{node.title}</span>
										<span className={styles.menuItemType}>{node.type}</span>
									</button>
								);
							})}
						</div>
					))}
				</div>
			</div>
		</div>
	);
}
