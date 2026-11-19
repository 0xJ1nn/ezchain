"use client";

import { useEffect, useRef, useState } from "react";
import { fmt } from "./format";
import styles from "./trade.module.css";

// Port of the brand rocket and all-time-high moments with their motion values.
// A moment fires on a real transition in the data — a 5-minute
// move through +20% or a close through the all-time high — never on load. Each
// event plays once; reduced motion turns the show off.

function reduced(): boolean {
	return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function plotPath(values: number[], w: number, h: number): string {
	if (values.length < 2) return "";
	const lo = Math.min(...values) * 0.98;
	const hi = Math.max(...values) * 1.06;
	const x = (i: number) => (i / (values.length - 1)) * (w - 20);
	const y = (v: number) => h - 40 - ((v - lo) / (hi - lo || 1)) * (h - 60);
	return values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
}

export function Moments({
	symbol,
	price,
	change5m,
	ath,
	recent,
}: {
	symbol: string;
	price: number | null;
	change5m: number | null;
	ath: number | null;
	recent: number[];
}) {
	const [rocketOn, setRocketOn] = useState(false);
	const [rocketTick, setRocketTick] = useState(0);
	const [athOn, setAthOn] = useState(false);
	const [athTick, setAthTick] = useState(0);
	const [confetti, setConfetti] = useState<Array<Record<string, string>>>([]);
	const previous = useRef<{ price: number | null; change5m: number | null; ath: number | null } | null>(null);
	const rocketActive = useRef(false);
	const athActive = useRef(false);

	function playRocket() {
		if (reduced()) return;
		rocketActive.current = true;
		setRocketTick((n) => n + 1);
		setRocketOn(true);
		setTimeout(() => setRocketOn(false), 2000);
	}

	function playAth() {
		if (reduced()) return;
		athActive.current = true;
		setAthTick((n) => n + 1);
		setAthOn(true);
		const pieces = Array.from({ length: 44 }, (_, i) => {
			const angle = Math.random() * Math.PI * 2;
			const distance = 90 + Math.random() * 220;
			const colors = ["var(--amber)", "var(--green)", "var(--green-hi)", "var(--blue)", "var(--violet)", "#fff"];
			return {
				"--x": "50%",
				"--y": "40%",
				"--dx": `${Math.cos(angle) * distance}px`,
				"--dy": `${Math.sin(angle) * distance * 0.7 + 120}px`,
				"--r": `${(Math.random() * 2 - 1) * 540}deg`,
				"--d": `${Math.random() * 120}ms`,
				"--c": colors[i % colors.length] ?? "#fff",
			};
		});
		setConfetti(pieces);
		setTimeout(() => setConfetti([]), 2200);
	}

	useEffect(() => {
		const before = previous.current;
		previous.current = { price, change5m, ath };
		if (!before) return; // the first observation is the baseline, never a moment
		if (change5m !== null && before.change5m !== null) {
			if (change5m > 20 && before.change5m <= 20 && !rocketActive.current) playRocket();
			if (change5m <= 20) rocketActive.current = false;
		}
		if (price !== null && ath !== null && before.price !== null && before.ath !== null) {
			if (price > before.ath && before.price <= before.ath && !athActive.current) playAth();
			if (price <= ath) athActive.current = false;
		}
	}, [price, change5m, ath]);

	const streaks = Array.from({ length: 22 }, (_, i) => ({
		left: `${(i * 37) % 100}%`,
		["--h" as string]: `${24 + ((i * 29) % 50)}px`,
		["--t" as string]: `${520 + ((i * 53) % 500)}ms`,
		["--d" as string]: `-${(i * 97) % 900}ms`,
	}));

	return (
		<div className={styles.moments} data-testid="moments">
			<section className={styles.rocket} data-testid="rocket-stage" style={{ ["--spd" as string]: rocketOn ? "1" : "0" }}>
				<div className={styles.streaks} aria-hidden="true">
					{streaks.map((s, i) => <i key={i} style={s} />)}
				</div>
				<div className={styles.ticketHd}>
					<div>
						<b>{symbol} · rocket</b>
						<small>Streaks and speed appear above +20% in five minutes.</small>
					</div>
					<button type="button" className={`${styles.btn} ${styles.sm}`} data-testid="rocket-replay" onClick={playRocket}>Replay</button>
				</div>
				<div className={styles.ticketHd} style={{ marginTop: 12 }}>
					<span key={`px-${rocketTick}`} className={`${styles.up} ${rocketTick ? styles.tickup : ""}`} style={{ font: "500 40px/1 var(--display, var(--f-display))" }}>
						{price === null ? "—" : `$${fmt(price)}`}
					</span>
					{rocketOn && change5m !== null && (
						<span className={`${styles.speedchip} ${styles.speedchipHot}`} data-testid="speed-chip">
							+{change5m.toFixed(0)}% in 5m
						</span>
					)}
				</div>
				<svg viewBox="0 0 600 200" width="100%" height="160" role="img" aria-label={`${symbol} recent price line`}>
					<path d={plotPath(recent, 600, 200)} fill="none" stroke="var(--green, var(--f-green))" strokeWidth={2} />
				</svg>
			</section>

			<section className={`${styles.athstage} ${athOn ? styles.athHit : ""}`} key={`ath-${athTick}`} data-testid="ath-stage">
				<div className={styles.athflash} />
				<div className={styles.shock} />
				<div className={`${styles.shock} ${styles.s2}`} />
				<div className={`${styles.shock} ${styles.s3}`} />
				<div className={styles.athbanner} data-testid="ath-banner">
					<b>NEW ALL-TIME HIGH</b>
					<span>{symbol} · {price === null ? "—" : `$${fmt(price)}`}</span>
				</div>
				{confetti.length > 0 && (
					<div className={styles.confetti} aria-hidden="true">
						{confetti.map((piece, i) => <i key={i} style={piece} />)}
					</div>
				)}
				<div className={styles.ticketHd}>
					<div>
						<b>{symbol} · all-time high</b>
						<small>The show plays only on a real new high for this asset.</small>
					</div>
					<button type="button" className={`${styles.btn} ${styles.sm}`} data-testid="ath-replay" onClick={playAth}>Replay</button>
				</div>
				<div className={styles.ticketHd} style={{ marginTop: 12 }}>
					<span style={{ font: "500 40px/1 var(--display, var(--f-display))" }}>{price === null ? "—" : `$${fmt(price)}`}</span>
					<span className={`${styles.badge} ${styles.badgeAmber}`}>{ath === null ? "ATH —" : `ATH $${fmt(ath)}`}</span>
				</div>
				<svg viewBox="0 0 600 200" width="100%" height="160" role="img" aria-label={`${symbol} price against its all-time high`}>
					<path d={plotPath(recent, 600, 200)} fill="none" stroke="var(--text-2, var(--f-t2))" strokeWidth={2} />
				</svg>
			</section>
		</div>
	);
}
