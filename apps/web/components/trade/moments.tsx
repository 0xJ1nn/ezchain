"use client";

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui";
import styles from "./trade.module.css";

type MomentKind = "rocket" | "ath";

interface ActiveMoment {
	kind: MomentKind;
	key: string;
	animated: boolean;
}

function prefersReducedMotion(): boolean {
	return (
		typeof window !== "undefined" &&
		typeof window.matchMedia === "function" &&
		window.matchMedia("(prefers-reduced-motion: reduce)").matches
	);
}

/**
 * The two earned moments. Each event key plays once: a repeat of the same key shows a
 * quiet badge instead of replaying the show. Reduced motion keeps the colour and the
 * banner and drops the movement. These are presentation only; no order is sent here.
 */
export function Moments({ athPrice }: { athPrice: number | null }) {
	const played = useRef<Set<string>>(new Set());
	const timer = useRef<number | null>(null);
	const [active, setActive] = useState<ActiveMoment | null>(null);
	const [quiet, setQuiet] = useState<MomentKind | null>(null);

	function fire(kind: MomentKind, key: string) {
		if (played.current.has(key)) {
			setQuiet(kind);
			return;
		}
		played.current.add(key);
		setQuiet(null);
		setActive({ kind, key, animated: !prefersReducedMotion() });
		if (timer.current !== null) window.clearTimeout(timer.current);
		timer.current = window.setTimeout(() => {
			setActive((current) => (current?.key === key ? { ...current, animated: false } : current));
			timer.current = null;
		}, 1800);
	}

	useEffect(() => {
		if (athPrice !== null) fire("ath", `ath-${athPrice}`);
		return () => {
			if (timer.current !== null) window.clearTimeout(timer.current);
		};
		// The series ATH is fixed for the loaded bars; a new series is a new event.
	}, [athPrice]);

	return (
		<div className="stack-sm" data-testid="moments">
			<div className="row" style={{ gap: 8 }}>
				<button
					className="btn sm"
					type="button"
					data-testid="replay-rocket"
					onClick={() => fire("rocket", "rocket-replay")}
				>
					Replay rocket
				</button>
				<button
					className="btn sm"
					type="button"
					data-testid="replay-ath"
					onClick={() => fire("ath", "ath-replay")}
				>
					Replay all-time high
				</button>
			</div>

			{active?.kind === "rocket" && (
				<div
					className={`${styles.moment} ${styles.rocket}${active.animated ? ` ${styles.animated}` : ""}`}
					data-testid="rocket-moment"
					data-animated={String(active.animated)}
					role="status"
				>
					<Badge tone="ok">ROCKET</Badge>
					<span className="small">Price climbing on the loaded series.</span>
				</div>
			)}
			{active?.kind === "ath" && (
				<div
					className={`${styles.moment} ${styles.ath}${active.animated ? ` ${styles.animated}` : ""}`}
					data-testid="ath-moment"
					data-animated={String(active.animated)}
					role="status"
				>
					<Badge tone="warn">NEW ALL-TIME HIGH</Badge>
					<span className="small">A new high for the loaded series.</span>
				</div>
			)}
			{quiet && (
				<div className="small muted" data-testid="moment-quiet" role="status">
					{quiet === "rocket" ? "Rocket" : "All-time high"} already played for this event.
				</div>
			)}
		</div>
	);
}
