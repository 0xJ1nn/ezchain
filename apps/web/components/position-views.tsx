import Link from "next/link";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui";
import { CandleSparkline } from "@/components/token-chart";
import type { CandleLoad } from "@/lib/candle-view";
import type { PaperPosition } from "@/lib/paper-api";
import {
	POSITION_VIEWS,
	money,
	positionHref,
	positionKey,
	markAge,
	markTone,
	quantity,
	positionsHref,
	type PositionQuery,
	type PositionView,
} from "@/lib/position-view";
import styles from "./position-views.module.css";

function initials(symbol: string): string {
	return symbol.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?";
}

function TokenCell({ position, query }: { position: PaperPosition; query: PositionQuery }) {
	return (
		<Link className={styles.tokenLink} href={positionHref(position, query)}>
			<span className="token sm" aria-hidden="true">
				{initials(position.symbol)}
			</span>
			<span>
				<span className="strong">{position.symbol}</span>
				<span className={styles.tokenAddress} title={position.token_address}>
					{position.token_address.slice(0, 6)}…{position.token_address.slice(-4)}
				</span>
			</span>
		</Link>
	);
}

function SourceBadge({ position }: { position: PaperPosition }) {
	const age = markAge(position);
	const observed = position.source ? Date.parse(position.source.observed_at) : Number.NaN;
	const stamp = Number.isNaN(observed)
		? null
		: new Date(observed).toISOString().replace("T", " ").slice(0, 16);
	if (!position.source || position.mark_usd === null) {
		return (
			<span className={styles.sourceCell}>
				<Badge tone="warn">unknown mark</Badge>
				<span className="small muted">{age.unknown ? "no observation" : age.label}</span>
			</span>
		);
	}
	return (
		<span className={styles.sourceCell}>
			<Badge tone={age.stale ? "warn" : "ghost"}>
				{age.stale ? `stale · ${age.label}` : `${position.source.name} · ${age.label}`}
			</Badge>
			{stamp && (
				<span className={styles.sourceStamp} title={position.source.observed_at}>
					{stamp}Z
				</span>
			)}
		</span>
	);
}

export function PositionViewSwitcher({
	view,
	query,
}: {
	view: PositionView;
	query: PositionQuery;
}) {
	return (
		<nav className={styles.switcher} aria-label="Position view">
			{POSITION_VIEWS.map((option) => (
				<Link
					key={option.value}
					className={`${styles.switchLink}${option.value === view ? ` ${styles.switchActive}` : ""}`}
					href={positionsHref({ ...query, view: option.value })}
					aria-current={option.value === view ? "page" : undefined}
				>
					{option.label}
				</Link>
			))}
		</nav>
	);
}

export function PositionList({
	positions,
	query,
}: {
	positions: PaperPosition[];
	query: PositionQuery;
}) {
	return (
		<div className="table-wrap">
			<table className="table" data-view="list">
				<thead>
					<tr>
						<th scope="col">Token</th>
						<th scope="col" className="col-hide-sm">
							Chain
						</th>
						<th scope="col" className="num">
							Quantity
						</th>
						<th scope="col" className="num col-hide-sm">
							Avg cost
						</th>
						<th scope="col" className="num">
							Mark
						</th>
						<th scope="col" className="num col-hide-sm">
							Value
						</th>
						<th scope="col" className="num">
							Unrealized
						</th>
						<th scope="col" className="num col-hide-sm">
							Realized
						</th>
						<th scope="col">Source · age</th>
						<th scope="col">Mode</th>
						<th scope="col">Open</th>
					</tr>
				</thead>
				<tbody>
					{positions.map((position) => {
						const age = markAge(position);
						return (
							<tr
								key={positionKey(position)}
								data-position={positionKey(position)}
								className={age.stale ? styles.staleRow : undefined}
							>
								<td>
									<TokenCell position={position} query={query} />
								</td>
								<td className="col-hide-sm">{position.chain}</td>
								<td className="num mono">{quantity(position.quantity)}</td>
								<td className="num mono col-hide-sm">{money(position.avg_cost_usd)}</td>
								<td className={`num ${styles.monoCell}`}>
									{position.mark_usd === null ? (
										<span className={styles.unknown}>—</span>
									) : (
										money(position.mark_usd)
									)}
								</td>
								<td className="num mono col-hide-sm">{money(position.market_value_usd)}</td>
								<td
									className={`num ${styles.monoCell}${
										position.unrealized_usd && Number(position.unrealized_usd) < 0
											? " err"
											: ""
									}`}
								>
									{position.unrealized_usd === null ? (
										<span className={styles.unknown}>—</span>
									) : (
										money(position.unrealized_usd, true)
									)}
								</td>
								<td className="num mono col-hide-sm">
									{money(position.realized_usd, true)}
								</td>
								<td>
									<SourceBadge position={position} />
								</td>
								<td>
									<Badge tone="ghost">{position.mode}</Badge>
								</td>
								<td>
									<Link
										className={styles.openLink}
										href={positionHref(position, query)}
										aria-label={`Open ${position.symbol} position`}
									>
										Open
									</Link>
								</td>
							</tr>
						);
					})}
				</tbody>
			</table>
		</div>
	);
}

function Fact({
	label,
	value,
	tone,
}: {
	label: string;
	value: ReactNode;
	tone?: "err" | "warn";
}) {
	return (
		<div className={styles.fact}>
			<dt>{label}</dt>
			<dd className={`mono${tone ? ` ${tone}` : ""}`}>{value}</dd>
		</div>
	);
}

export function PositionGrid({
	positions,
	query,
}: {
	positions: PaperPosition[];
	query: PositionQuery;
}) {
	return (
		<div className={styles.cardGrid} data-view="grid">
			{positions.map((position) => {
				const age = markAge(position);
				const unknown = position.mark_usd === null;
				return (
					<article
						className={`${styles.card}${age.stale ? ` ${styles.cardStale}` : ""}`}
						key={positionKey(position)}
						data-position={positionKey(position)}
					>
						<header className={styles.cardHead}>
							<div className={styles.tokenLink}>
								<span className="token sm" aria-hidden="true">
									{initials(position.symbol)}
								</span>
								<span>
									<span className={styles.cardSymbol}>{position.symbol}</span>
									<span className={styles.tokenAddress}>{position.chain}</span>
								</span>
							</div>
							<Badge tone={unknown || age.stale ? "warn" : "ghost"}>
								{unknown ? "unknown mark" : age.stale ? "stale" : "fresh"}
							</Badge>
						</header>

						<div className={styles.cardMark}>
							<span className={`mono ${unknown ? styles.unknown : ""}`}>
								{unknown ? "—" : money(position.mark_usd)}
							</span>
							<span className="small muted">mark</span>
						</div>

						<dl className={styles.cardFacts}>
							<Fact label="Quantity" value={quantity(position.quantity)} />
							<Fact
								label="Value"
								value={money(position.market_value_usd)}
								tone={position.market_value_usd === null ? "warn" : undefined}
							/>
							<Fact
								label="Unrealized"
								value={position.unrealized_usd === null ? "—" : money(position.unrealized_usd, true)}
								tone={
									position.unrealized_usd === null
										? "warn"
										: Number(position.unrealized_usd) < 0
											? "err"
											: undefined
								}
							/>
							<Fact label="Avg cost" value={money(position.avg_cost_usd)} />
						</dl>

						<div className={styles.cardSource}>
							<SourceBadge position={position} />
						</div>

						<footer className={styles.cardFoot}>
							<span className="small muted">
								{position.mode} · {position.chain}
							</span>
							<Link className={styles.openLink} href={positionHref(position, query)}>
								Open position →
							</Link>
						</footer>
					</article>
				);
			})}
		</div>
	);
}

export function PositionDetailed({
	positions,
	query,
	candles,
	timeframe,
}: {
	positions: PaperPosition[];
	query: PositionQuery;
	candles: Record<string, CandleLoad>;
	timeframe: string;
}) {
	return (
		<div className={styles.detailedList} data-view="detailed">
			{positions.map((position) => {
				const age = markAge(position);
				return (
					<article
						className={`${styles.detailedRow}${age.stale ? ` ${styles.cardStale}` : ""}`}
						key={positionKey(position)}
						data-position={positionKey(position)}
					>
						<header className={styles.detailedHead}>
							<div className={styles.tokenLink}>
								<span className="token sm" aria-hidden="true">
									{initials(position.symbol)}
								</span>
								<span>
									<span className={styles.cardSymbol}>{position.symbol}</span>
									<span className={styles.tokenAddress}>
										{position.chain} · {position.token_address.slice(0, 6)}…
										{position.token_address.slice(-4)}
									</span>
								</span>
							</div>
							<div className={styles.detailedHeadRight}>
								<Badge tone={markTone(position)}>
									{position.mark_usd === null
										? "unknown mark"
										: age.stale
											? `stale · ${age.label}`
											: position.source?.name
												? `${position.source.name} · ${age.label}`
												: "unattributed"}
								</Badge>
								<Link className={styles.openLink} href={positionHref(position, query)}>
									Open full position →
								</Link>
							</div>
						</header>

						<div className={styles.detailedBody}>
							<div className={styles.sparkCell}>
								<span className="micro">Inline chart</span>
								{candles[positionKey(position)] ? (
									<CandleSparkline
										load={candles[positionKey(position)]}
										label={`${position.symbol} ${position.chain}`}
									/>
								) : (
									<span className={styles.miniEmpty}>— not requested</span>
								)}
								<span className="small muted">
									{position.source
										? `${position.source.name} · ${age.label} old`
										: "source unknown"}{" "}
									· closed {timeframe} bars · full chart on the position page
								</span>
							</div>

							<dl className={styles.detailFacts}>
								<Fact label="Quantity" value={quantity(position.quantity)} />
								<Fact label="Avg cost" value={money(position.avg_cost_usd)} />
								<Fact
									label="Mark"
									value={position.mark_usd === null ? "—" : money(position.mark_usd)}
									tone={position.mark_usd === null ? "warn" : undefined}
								/>
								<Fact
									label="Value"
									value={money(position.market_value_usd)}
									tone={position.market_value_usd === null ? "warn" : undefined}
								/>
								<Fact
									label="Unrealized"
									value={
										position.unrealized_usd === null
											? "—"
											: money(position.unrealized_usd, true)
									}
									tone={
										position.unrealized_usd === null
											? "warn"
											: Number(position.unrealized_usd) < 0
												? "err"
												: undefined
									}
								/>
								<Fact label="Realized" value={money(position.realized_usd, true)} />
							</dl>

							<div className={styles.protection}>
								<span className="micro">Protection</span>
								<div className="row">
									<Badge tone="ghost">unknown</Badge>
								</div>
								<p className="small muted">
									Protection status not reported by the positions API. No stop,
									take-profit or trailing rule is claimed as present or absent.
								</p>
							</div>
						</div>
					</article>
				);
			})}
		</div>
	);
}
