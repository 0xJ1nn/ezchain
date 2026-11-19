"use client";

import { useState } from "react";
import { createProtectionArm, type PaperApiMode } from "@/lib/protection-api";
import { fmt, pct, readPrice, usd } from "./format";
import type { ExitLines } from "./types";
import styles from "./trade.module.css";

// Port of the brand exits panel and its trading styles.
// Every number shown here is derived from the user's own exit inputs and the
// server-supplied book value; nothing is read from a client-side position scalar.

const TRACE = [10, 12, 11, 18, 26, 24, 33, 41, 38, 47, 55, 52, 60, 57, 50, 44];
const VW = 320;
const VH = 78;
const trailX = (i: number) => (i / (TRACE.length - 1)) * VW;
const trailY = (v: number) => VH - 10 - (v / 64) * (VH - 16);

function trailPaths() {
	let best = 0;
	const stops: Array<number | null> = TRACE.map((v) => {
		best = Math.max(best, v);
		return v >= 24 ? best - 8 : null;
	});
	const price = TRACE.map((v, i) => `${i ? "L" : "M"}${trailX(i)} ${trailY(v)}`).join("");
	let stop = "";
	let open = false;
	stops.forEach((v, i) => {
		if (v === null) return;
		stop += `${open ? `L${trailX(i)} ${trailY(stops[i - 1] ?? v)}L` : "M"}${trailX(i)} ${trailY(v)}`;
		open = true;
	});
	const hit = TRACE.findIndex((v, i) => i > 12 && stops[i] !== null && v <= (stops[i] ?? 0));
	return { price, stop, hit };
}

function Toggle({
	on,
	label,
	disabled,
	onChange,
}: {
	on: boolean;
	label: string;
	disabled?: boolean;
	onChange?: () => void;
}) {
	return (
		<button
			type="button"
			className={`${styles.toggle} ${on ? styles.toggleOn : ""}`}
			aria-label={label}
			aria-pressed={on}
			disabled={disabled}
			onClick={onChange}
		/>
	);
}

export function Exits({
	chain,
	tokenAddress,
	symbol,
	lines,
	onChange,
	size,
	bookUsd,
	last,
	mode,
}: {
	chain: string;
	tokenAddress: string;
	symbol: string;
	lines: ExitLines;
	onChange: (lines: ExitLines) => void;
	/** Position size in base units. Null keeps every derived money value "—". */
	size: number | null;
	/** Server equity. Null renders as "—" so risk share is never invented. */
	bookUsd: number | null;
	last: number;
	mode: PaperApiMode;
}) {
	const [breakeven] = useState(true);
	const [trailOn, setTrailOn] = useState(true);
	const [activation, setActivation] = useState("");
	const [distance, setDistance] = useState("1.5");
	const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
	const [busy, setBusy] = useState(false);
	const trail = trailPaths();

	const entry = lines.entry;
	const stop = lines.sl;
	const targets = lines.tp;
	const risk = entry !== null && stop !== null && size !== null ? (entry - stop) * size : null;
	const reward =
		entry === null || size === null
			? null
			: targets.reduce((sum, t) => sum + (t.p - entry) * size * (t.q / 100), 0);
	const ratio = risk !== null && reward !== null && risk > 0 ? reward / risk : null;
	const riskShare = risk !== null && bookUsd !== null && bookUsd > 0 ? (risk / bookUsd) * 100 : null;
	const totalQ = targets.reduce((sum, t) => sum + t.q, 0);

	function setStop(raw: string) {
		if (entry === null) return;
		const next = readPrice(raw);
		onChange({ ...lines, sl: next === null ? null : Math.min(next, entry * 0.999) });
	}

	function setTarget(index: number, raw: string) {
		if (entry === null) return;
		const next = readPrice(raw);
		if (next === null) return;
		onChange({
			...lines,
			tp: lines.tp.map((t, i) => (i === index ? { ...t, p: Math.max(next, entry * 1.001) } : t)),
		});
	}

	function setQuantity(index: number, raw: string) {
		const value = Number(raw);
		if (!Number.isFinite(value) || value < 0 || value > 100) return;
		onChange({
			...lines,
			tp: lines.tp.map((t, i) => (i === index ? { ...t, q: value } : t)),
		});
	}

	async function arm() {
		if (entry === null || stop === null) {
			setNotice({ tone: "err", text: "Set an entry and a stop before arming exits." });
			return;
		}
		setBusy(true);
		setNotice(null);
		try {
			const arms = [];
			arms.push(
				await createProtectionArm({
					chain,
					token_address: tokenAddress,
					token_symbol: symbol,
					kind: "stop_loss",
					params: { price_usd: String(stop) },
				}, mode),
			);
			if (targets.length > 0) {
				arms.push(
					await createProtectionArm({
						chain,
						token_address: tokenAddress,
						token_symbol: symbol,
						kind: "take_profit",
						params: {
							levels: targets.map((t) => ({
								price_usd: String(t.p),
								fraction: String(t.q / 100),
							})),
						},
					}, mode),
				);
			}
			const dist = Number(distance);
			if (trailOn && Number.isFinite(dist) && dist > 0 && dist < 100) {
				arms.push(
					await createProtectionArm({
						chain,
						token_address: tokenAddress,
						token_symbol: symbol,
						kind: "trailing_stop",
						params: { trailing_pct: String(dist) },
					}, mode),
				);
			}
			setNotice({ tone: "ok", text: `Armed ${arms.length} exit${arms.length === 1 ? "" : "s"} on paper.` });
		} catch (error) {
			setNotice({ tone: "err", text: error instanceof Error ? error.message : "The exits could not be armed." });
		} finally {
			setBusy(false);
		}
	}

	return (
		<section className={styles.exits} data-testid="exits-panel" aria-label="Exits">
			<div className={styles.exitsHd}>
				<h3>Exits</h3>
				<span className={styles.pos}>
					{entry === null ? "No entry —" : `Long ${fmt(size)} ${symbol} @ ${fmt(entry)}`}
				</span>
			</div>

			<div className={`${styles.xs} ${styles.xsSl}`}>
				<div className={styles.xTitle}>
					<span className={styles.xDot} /> Stop loss
					<Toggle on={stop !== null} label="Stop loss enabled" onChange={() => {
						if (stop !== null) onChange({ ...lines, sl: null });
						else if (entry !== null) onChange({ ...lines, sl: entry * 0.97 });
					}} />
				</div>
				<div className={styles.xrow}>
					<span className={styles.xlab}>SL</span>
					<label className={styles.xin}>
						<input
							id="in-sl"
							data-testid="stop-input"
							inputMode="decimal"
							value={stop === null ? "" : fmt(stop)}
							onChange={(e) => setStop(e.target.value)}
							aria-label="Stop loss price"
						/>
						<small>USD</small>
					</label>
					<span className={styles.xpc}>{entry === null || stop === null ? "—" : pct((stop / entry - 1) * 100)}</span>
					<span className={styles.xusd}>{risk === null ? "—" : usd(-risk)}</span>
				</div>
				<div className={styles.xopt}>
					Move stop to entry once TP1 fills
					<span className={styles.badgeAmber} title="The protection API has no breakeven-on-target parameter">not available</span>
					<Toggle on={breakeven} label="Move stop to entry once TP1 fills" disabled />
				</div>
				<div className={styles.xopt}>
					Trigger on candle close, not a wick
					<span className={styles.badgeAmber} title="The protection API evaluates on ticks, not candle close">not available</span>
					<Toggle on={false} label="Trigger on candle close" disabled />
				</div>
			</div>

			<div className={`${styles.xs} ${styles.xsTp}`}>
				<div className={styles.xTitle}>
					<span className={styles.xDot} /> Take profit
					<span className={styles.xNote}>{targets.length} targets · {totalQ}%</span>
					<Toggle on={targets.length > 0} label="Take profit enabled" onChange={() => {
						if (targets.length > 0) onChange({ ...lines, tp: [] });
						else if (entry !== null) onChange({ ...lines, tp: [
							{ p: entry * 1.04, q: 40 }, { p: entry * 1.07, q: 35 }, { p: entry * 1.1, q: 25 },
						] });
					}} />
				</div>
				<div className={styles.ladder} aria-hidden="true">
					{targets.map((t, i) => (
						<i key={i} style={{ ["--q" as string]: String(t.q), ["--o" as string]: String(1 - i * 0.22) }} />
					))}
				</div>
				{targets.map((t, i) => (
					<div className={`${styles.xrow} ${styles.xrowQ}`} key={i}>
						<span className={styles.xlab}>TP{i + 1}</span>
						<label className={styles.xin}>
							<input
								data-testid={`tp-input-${i}`}
								inputMode="decimal"
								value={fmt(t.p)}
								onChange={(e) => setTarget(i, e.target.value)}
								aria-label={`Take profit ${i + 1} price`}
							/>
							<small>USD</small>
						</label>
						<span className={styles.xpc}>{entry === null ? "—" : pct((t.p / entry - 1) * 100)}</span>
						<div className={styles.qty}>
							<div className={styles.qtyBar}><i style={{ ["--q" as string]: `${t.q}%` }} /></div>
							<span>
								<input
									className={styles.qtyInput}
									type="number"
									min={0}
									max={100}
									step={1}
									value={t.q}
									onChange={(e) => setQuantity(i, e.target.value)}
									aria-label={`Take profit ${i + 1} share in percent`}
									data-testid={`tp-qty-${i}`}
								/>
								{" "}· {usd(entry === null || size === null ? null : (t.p - entry) * size * (t.q / 100))}
							</span>
						</div>
					</div>
				))}
			</div>

			<div className={`${styles.xs} ${styles.xsTr}`}>
				<div className={styles.xTitle}>
					<span className={styles.xDot} /> Trailing stop
					<Toggle on={trailOn} label="Trailing stop enabled" onChange={() => setTrailOn((v) => !v)} />
				</div>
				<div className={styles.xrow}>
					<span className={styles.xlab}>Start</span>
					<label className={styles.xin}>
						<input
							inputMode="decimal"
							value={activation}
							onChange={(e) => setActivation(e.target.value)}
							placeholder="—"
							aria-label="Trailing activation price"
						/>
						<small>USD</small>
					</label>
					<span className={styles.xpc}>—</span>
					<span className={styles.xusd}>
						<span className={styles.badgeAmber} title="The protection API has no activation-price parameter">not available</span>
					</span>
				</div>
				<div className={styles.xrow}>
					<span className={styles.xlab}>Trail</span>
					<label className={styles.xin}>
						<input
							inputMode="decimal"
							value={distance}
							onChange={(e) => setDistance(e.target.value)}
							aria-label="Trailing distance percent"
							data-testid="trail-distance"
						/>
						<small>%</small>
					</label>
					<span className={styles.xpc}>{fmt(lines.trail)}</span>
					<span className={styles.xusd}>stop now</span>
				</div>
				<div className={styles.trailviz} aria-hidden="true">
					<svg viewBox={`0 0 ${VW} ${VH}`}>
						<line className={styles.act} x1={0} x2={VW} y1={trailY(24)} y2={trailY(24)} />
						<text x={VW} y={trailY(24) + 12} textAnchor="end">trail starts</text>
						<path className={styles.pxPath} d={trail.price} />
						<path className={styles.stPath} d={trail.stop} />
						{trail.hit >= 0 && <circle className={styles.hit} cx={trailX(trail.hit)} cy={trailY(TRACE[trail.hit] ?? 0)} r={4} />}
						{trail.hit >= 0 && <text x={trailX(trail.hit) + 2} y={trailY(TRACE[trail.hit] ?? 0) + 16} textAnchor="end">closed at the trail</text>}
					</svg>
				</div>
			</div>

			<div className={styles.rr} data-testid="rr-panel">
				<div className={styles.rrTop}>
					<span className={styles.rrRatio}>{ratio === null ? "—" : `1 : ${ratio.toFixed(1)}`}</span>
					<span>reward to risk</span>
					<span className={styles.rrRisk}>
						{riskShare === null ? "Risk — of book" : `Risk ${riskShare.toFixed(1)}% of book`}
					</span>
				</div>
				<div className={styles.rrBar}>
					<i className={styles.rrR} style={{ flex: `${risk ?? 0} 0 0` }} />
					<i className={styles.rrW} style={{ flex: `${reward ?? 0} 0 0` }} />
				</div>
				<div className={styles.rrEnds}>
					<span className={styles.down}>Worst {usd(risk === null ? null : -risk)}</span>
					<span className={styles.up}>Best {usd(reward)}</span>
				</div>
			</div>

			<div className={styles.exitsFoot}>
				<span>Exits arm on the server through the protection API — paper only.</span>
				<button
					type="button"
					className={`${styles.btn} ${styles.sm} ${styles.primary}`}
					data-testid="exits-save"
					disabled={busy || entry === null || stop === null}
					onClick={arm}
				>
					{busy ? "Arming…" : "Save exits"}
				</button>
			</div>
			{notice && (
				<div className={styles.exitsFoot} role="status" data-testid="exits-status">
					{notice.text}
				</div>
			)}
			<span className={styles.srOnly}>
				Last price {fmt(last)}. Activation price is not supported by the protection API.
			</span>
		</section>
	);
}
