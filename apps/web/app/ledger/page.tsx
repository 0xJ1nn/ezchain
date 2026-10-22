import Link from "next/link";
import { AppShell } from "@/components/shell";
import { EmptyState, FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { formatAmount, formatUsd, formatUsdPrice, quoteKindLabel, shortAddress } from "@/lib/live-format";
import {
	getPaperFills,
	getPaperOrders,
	type PaperFillRecord,
	type PaperOrderRecord,
	type PaperResult,
} from "@/lib/paper-api";

export const dynamic = "force-dynamic";

function demoError<T>(
	mode: "fixture" | "live",
	message: string,
): PaperResult<T> {
	return { ok: false, mode, error: { code: "FIXTURE_DEMO_ERROR", message } };
}

function statusTone(status: string): "ok" | "err" | "warn" | "ghost" {
	if (status === "filled") return "ok";
	if (status === "rejected") return "err";
	if (status === "pending") return "warn";
	return "ghost";
}

function stamp(iso: string | null) {
	if (!iso) return "—";
	return `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
}

export default async function LedgerPage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string }>;
}) {
	const { state } = await searchParams;
	const mode = process.env.EZCHAIN_WEB_MODE === "fixture" ? "fixture" : "live";
	const [ordersResult, fillsResult] = await Promise.all([
		getPaperOrders(100),
		getPaperFills(100),
	]);
	const demo = mode === "fixture" ? state : undefined;
	const orders =
		demo === "error"
			? demoError<PaperOrderRecord[]>(mode, "Fixture demo: the orders request failed.")
			: demo === "empty"
				? { ok: true as const, mode, data: [] as PaperOrderRecord[] }
				: ordersResult;
	const fills =
		demo === "error"
			? demoError<PaperFillRecord[]>(mode, "Fixture demo: the fills request failed.")
			: demo === "empty"
				? { ok: true as const, mode, data: [] as PaperFillRecord[] }
				: fillsResult;

	const orderRows = orders.ok ? orders.data : [];
	const fillRows = fills.ok ? fills.data : [];
	const rejected = orderRows.filter((order) => order.status === "rejected");
	const error = !orders.ok ? orders.error : !fills.ok ? fills.error : null;
	const empty = orders.ok && fills.ok && orderRows.length === 0 && fillRows.length === 0;

	return (
		<AppShell active="ledger">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Trade / Activity</div>
					<h1>Activity</h1>
					<p className="sub">
						Paper orders and their fills from the API read models; fixture mode labels its
						sample rows. A rejected order keeps its exact reason codes. The append-only
						ledger has no public read endpoint in this build, so this page does not invent
						ledger entries.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/reports">
						Export
					</Link>
					<Link className="btn primary" href="/markets">
						New paper order
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Activity unavailable"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/ledger">
								Retry →
							</Link>
						}
					>
						No rows are substituted. Missing values stay <span className="mono">—</span>.
						{demo === "error" && " Fixture demo state ?state=error."}
					</Unavailable>
				</div>
			)}

			<section className="kpi-band mb-12" aria-label="Activity counts">
				<div className="stat">
					<div className="label">Orders</div>
					<div className="value sm">{orders.ok ? orderRows.length : "—"}</div>
					<div className="meta">{orders.ok ? "last 100 · API" : "unavailable"}</div>
				</div>
				<div className="stat">
					<div className="label">Fills</div>
					<div className="value sm">{fills.ok ? fillRows.length : "—"}</div>
					<div className="meta">{fills.ok ? "last 100 · API" : "unavailable"}</div>
				</div>
				<div className="stat">
					<div className="label">Rejected</div>
					<div className={`value sm ${rejected.length > 0 ? "err" : ""}`}>
						{orders.ok ? rejected.length : "—"}
					</div>
					<div className="meta">carry reason codes</div>
				</div>
				<div className="stat">
					<div className="label">Ledger read</div>
					<div className="value sm">—</div>
					<div className="meta">no public endpoint</div>
				</div>
			</section>

			{empty ? (
				<Panel title="No paper activity" sub="empty" flush>
					<div className="panel-body">
						<EmptyState
							title="Nothing has been ordered yet"
							links={[
								{ href: "/markets", label: "Browse markets", primary: true },
								{ href: "/performance", label: "Performance" },
							]}
						>
							Orders and fills appear here as soon as a paper order is submitted. An empty
							response is shown as empty; it is never padded with sample trades.
							{demo === "empty" && " Fixture demo state ?state=empty."}
						</EmptyState>
					</div>
				</Panel>
			) : (
				<div className="stack">
					<Panel
						title="Orders"
						sub={orders.ok ? `${orderRows.length} · newest first` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">LIVE</Badge>}
						flush
						footer="A rejected order is a real attempt recorded by the API. The reason field is rendered verbatim; it is never rewritten into a generic error."
					>
						{orders.ok ? (
							orderRows.length > 0 ? (
								<div className="table-wrap">
									<table className="table">
										<thead>
											<tr>
												<th>Created</th>
												<th>Order</th>
												<th>Token</th>
												<th>Side</th>
												<th className="num">Amount</th>
												<th>Status</th>
												<th>Reason / quote</th>
												<th className="col-hide-sm">Filled</th>
											</tr>
										</thead>
										<tbody>
											{orderRows.map((order) => (
												<tr key={order.id}>
													<td className="small mono">{stamp(order.created_at)}</td>
													<td className="mono small">{order.id}</td>
													<td>
														<Link href={`/tokens/${order.chain}/${order.token_address}`}>
															<span className="strong">{shortAddress(order.token_address)}</span>
															<span className="sub" style={{ display: "block" }}>
																{order.chain}
															</span>
														</Link>
													</td>
													<td>
														<Badge tone={order.side === "buy" ? "ok" : "warn"}>
															{order.side}
														</Badge>
													</td>
													<td className="num">{formatUsd(order.amount_usd)}</td>
													<td>
														<Badge tone={statusTone(order.status)}>{order.status}</Badge>
													</td>
													<td className="small">
														{order.reason ? (
															<span className="err mono">{order.reason}</span>
														) : (
															<span className="muted">{quoteKindLabel(order.quote_kind)}</span>
														)}
														{order.reason && order.quote_kind && (
															<span className="sub" style={{ display: "block" }}>
																{quoteKindLabel(order.quote_kind)}
															</span>
														)}
													</td>
													<td className="small mono col-hide-sm">{stamp(order.filled_at)}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="panel-body">
									<EmptyState title="No orders">The API returned an empty order list.</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<div className="note err">{orders.error.message}</div>
							</div>
						)}
					</Panel>

					<Panel
						title="Fills"
						sub={fills.ok ? `${fillRows.length} · newest first` : "unavailable"}
						action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">LIVE</Badge>}
						flush
						footer="Fills are written by the paper execution service. Prices, fees and notionals are server values; the UI does not recompute them."
					>
						{fills.ok ? (
							fillRows.length > 0 ? (
								<div className="table-wrap">
									<table className="table">
										<thead>
											<tr>
												<th>Created</th>
												<th>Fill</th>
												<th>Order</th>
												<th>Token</th>
												<th>Side</th>
												<th className="num">Quantity</th>
												<th className="num col-hide-sm">Price</th>
												<th className="num">Notional</th>
												<th className="num col-hide-sm">Fee</th>
												<th className="col-hide-sm">Quote</th>
											</tr>
										</thead>
										<tbody>
											{fillRows.map((fill) => (
												<tr key={fill.id}>
													<td className="small mono">{stamp(fill.created_at)}</td>
													<td className="mono small">{fill.id}</td>
													<td className="mono small">{fill.order_id}</td>
													<td>
														<Link href={`/tokens/${fill.chain}/${fill.token_address}`}>
															{shortAddress(fill.token_address)}
														</Link>
													</td>
													<td>
														<Badge tone={fill.side === "buy" ? "ok" : "warn"}>{fill.side}</Badge>
													</td>
													<td className="num">{formatAmount(fill.quantity)}</td>
													<td className="num col-hide-sm">{formatUsdPrice(fill.price_usd)}</td>
													<td className="num">{formatUsd(fill.notional_usd)}</td>
													<td className="num col-hide-sm">{formatUsd(fill.fee_usd)}</td>
													<td className="col-hide-sm small">{fill.quote_kind}</td>
												</tr>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="panel-body">
									<EmptyState title="No fills">The API returned an empty fill list.</EmptyState>
								</div>
							)
						) : (
							<div className="panel-body">
								<div className="note err">{fills.error.message}</div>
							</div>
						)}
					</Panel>
				</div>
			)}

			<Panel title="Why the ledger itself is not here" sub="read model boundary" className="mt-16">
				<p className="small muted">
					Orders and fills are execution records. The ledger is the accountant: cash, position
					size and realized PnL are derived from append-only entries inside the API database.
					This branch exposes no ledger-entry read endpoint, so no ledger row is shown or
					reconstructed in the browser.
				</p>
				<div className="row mt-12">
					<ButtonLink href="/reports" size="sm">
						Export orders and fills
					</ButtonLink>
					<ButtonLink href="/risk" size="sm">
						Risk and reconciliation
					</ButtonLink>
				</div>
			</Panel>
		</AppShell>
	);
}
