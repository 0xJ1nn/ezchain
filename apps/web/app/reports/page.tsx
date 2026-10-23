import Link from "next/link";
import { AppShell } from "@/components/shell";
import { FixtureBadge, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel } from "@/components/ui";
import { getPaperFills, getPaperOrders, getPaperSnapshot } from "@/lib/paper-api";
import { CsvExport, type CsvColumn } from "./csv-export";

export const dynamic = "force-dynamic";

const ORDER_COLUMNS: CsvColumn[] = [
	{ key: "id", label: "order_id" },
	{ key: "created_at", label: "created_at" },
	{ key: "filled_at", label: "filled_at" },
	{ key: "chain", label: "chain" },
	{ key: "token_address", label: "token_address" },
	{ key: "side", label: "side" },
	{ key: "amount_usd", label: "amount_usd" },
	{ key: "status", label: "status" },
	{ key: "reason", label: "reason" },
	{ key: "quote_kind", label: "quote_kind" },
];

const FILL_COLUMNS: CsvColumn[] = [
	{ key: "id", label: "fill_id" },
	{ key: "order_id", label: "order_id" },
	{ key: "created_at", label: "created_at" },
	{ key: "chain", label: "chain" },
	{ key: "token_address", label: "token_address" },
	{ key: "side", label: "side" },
	{ key: "quantity", label: "quantity" },
	{ key: "price_usd", label: "price_usd" },
	{ key: "notional_usd", label: "notional_usd" },
	{ key: "fee_usd", label: "fee_usd" },
	{ key: "quote_kind", label: "quote_kind" },
];

const POSITION_COLUMNS: CsvColumn[] = [
	{ key: "chain", label: "chain" },
	{ key: "token_address", label: "token_address" },
	{ key: "symbol", label: "symbol" },
	{ key: "quantity", label: "quantity" },
	{ key: "avg_cost_usd", label: "avg_cost_usd" },
	{ key: "mark_usd", label: "mark_usd" },
	{ key: "market_value_usd", label: "market_value_usd" },
	{ key: "unrealized_usd", label: "unrealized_usd" },
	{ key: "realized_usd", label: "realized_usd" },
	{ key: "mark_source", label: "mark_source" },
	{ key: "mark_observed_at", label: "mark_observed_at" },
];

export default async function ReportsPage() {
	const snapshot = await getPaperSnapshot();
	const mode = snapshot.mode;
	const [ordersResult, fillsResult] = await Promise.all([
		getPaperOrders(200),
		getPaperFills(200),
	]);
	const fetchedAt = new Date().toISOString();

	const accounts = snapshot.account.ok ? snapshot.account.data : null;
	const positions = snapshot.positions.ok ? snapshot.positions.data : [];
	const orders = ordersResult.ok ? ordersResult.data : [];
	const fills = fillsResult.ok ? fillsResult.data : [];
	const error = !snapshot.account.ok
		? snapshot.account.error
		: !ordersResult.ok
			? ordersResult.error
			: !fillsResult.ok
				? fillsResult.error
				: null;

	const orderRows = orders.map((order) => ({
		id: order.id,
		created_at: order.created_at,
		filled_at: order.filled_at,
		chain: order.chain,
		token_address: order.token_address,
		side: order.side,
		amount_usd: order.amount_usd,
		status: order.status,
		reason: order.reason,
		quote_kind: order.quote_kind,
	}));
	const fillRows = fills.map((fill) => ({
		id: fill.id,
		order_id: fill.order_id,
		created_at: fill.created_at,
		chain: fill.chain,
		token_address: fill.token_address,
		side: fill.side,
		quantity: fill.quantity,
		price_usd: fill.price_usd,
		notional_usd: fill.notional_usd,
		fee_usd: fill.fee_usd,
		quote_kind: fill.quote_kind,
	}));
	const positionRows = positions.map((position) => ({
		chain: position.chain,
		token_address: position.token_address,
		symbol: position.symbol,
		quantity: position.quantity,
		avg_cost_usd: position.avg_cost_usd,
		mark_usd: position.mark_usd,
		market_value_usd: position.market_value_usd,
		unrealized_usd: position.unrealized_usd,
		realized_usd: position.realized_usd,
		mark_source: position.source?.name ?? null,
		mark_observed_at: position.source?.observed_at ?? null,
	}));

	return (
		<AppShell active="reports">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Trade / Reports</div>
					<h1>Reports</h1>
					<p className="sub">
						CSV exports built in your browser from the rows the API already returned. No
						export job, no server-side secret and no accounting math: the columns are the
						server values, labelled by population and fetch time.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/ledger">
						Activity
					</Link>
				</div>
			</header>

			{error && (
				<div className="mb-12">
					<Unavailable
						title="Some report data is unavailable"
						code={error.code}
						message={error.message}
						action={
							<Link className="small" href="/reports">
								Retry →
							</Link>
						}
					>
						Exports contain only the rows that were actually read. A failed set stays empty
						and is labelled unavailable.
					</Unavailable>
				</div>
			)}

			<div className="note mb-12">
				<strong>Population and timestamp.</strong>{" "}
				{mode === "fixture" ? (
					<>
						Sample generated <span className="mono">{fetchedAt}</span> in fixture mode; no API
						was called.
					</>
				) : (
					<>
						Fetched <span className="mono">{fetchedAt}</span> from the paper API.
					</>
				)}{" "}
				Orders and fills are the latest 200 rows across all chains. Positions are the current
				paper account view. The CSV is generated at click time in this browser; nothing is
				uploaded.
			</div>

			<div className="grid cols-3">
				<Panel
					title="Orders"
					sub={ordersResult.ok ? `${orders.length} rows` : "unavailable"}
					action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
					footer={
						<>
							Model: <span className="mono">/api/v1/paper/orders?limit=200</span>
						</>
					}
				>
					<div className="small muted mb-12">
						Order id, timestamps, chain, token, side, USD amount, status, reason codes and
						quote kind. Rejected orders are included with their reason.
					</div>
					<CsvExport
						dataset="orders"
						columns={ORDER_COLUMNS}
						rows={orderRows}
						fetchedAt={fetchedAt}
					/>
				</Panel>

				<Panel
					title="Fills"
					sub={fillsResult.ok ? `${fills.length} rows` : "unavailable"}
					action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
					footer={
						<>
							Model: <span className="mono">/api/v1/paper/fills?limit=200</span>
						</>
					}
				>
					<div className="small muted mb-12">
						Fill id, order id, timestamp, chain, token, side, quantity, price, notional, fee
						and quote kind. Values are copied from the API.
					</div>
					<CsvExport
						dataset="fills"
						columns={FILL_COLUMNS}
						rows={fillRows}
						fetchedAt={fetchedAt}
					/>
				</Panel>

				<Panel
					title="Positions"
					sub={snapshot.positions.ok ? `${positions.length} rows` : "unavailable"}
					action={mode === "fixture" ? <FixtureBadge /> : <Badge tone="ghost">PAPER</Badge>}
					footer={
						<>
							Model: <span className="mono">/api/v1/paper/positions</span>
						</>
					}
				>
					<div className="small muted mb-12">
						Current open positions with average cost, mark, market value, unrealized and
						realized values, plus the mark source and observation time.
					</div>
					<CsvExport
						dataset="positions"
						columns={POSITION_COLUMNS}
						rows={positionRows}
						fetchedAt={fetchedAt}
					/>
				</Panel>
			</div>

			<div className="split mt-16">
				<Panel title="What is inside" sub="read model only" flush>
					<div className="table-wrap">
						<table className="table compact">
							<thead>
								<tr>
									<th>Set</th>
									<th className="num">Rows</th>
									<th>Source</th>
									<th>Notes</th>
								</tr>
							</thead>
							<tbody>
								<tr>
									<td>Orders</td>
									<td className="num">{ordersResult.ok ? orders.length : "—"}</td>
									<td className="mono small">/paper/orders</td>
									<td className="small muted">includes rejected attempts</td>
								</tr>
								<tr>
									<td>Fills</td>
									<td className="num">{fillsResult.ok ? fills.length : "—"}</td>
									<td className="mono small">/paper/fills</td>
									<td className="small muted">execution records, fees included</td>
								</tr>
								<tr>
									<td>Positions</td>
									<td className="num">{snapshot.positions.ok ? positions.length : "—"}</td>
									<td className="mono small">/paper/positions</td>
									<td className="small muted">server PositionView</td>
								</tr>
								<tr>
									<td>Account</td>
									<td className="num">1</td>
									<td className="mono small">/paper/account</td>
									<td className="small muted">
										{accounts ? "read; used by the performance page" : "unavailable"}
									</td>
								</tr>
							</tbody>
						</table>
					</div>
				</Panel>

				<Panel title="Not in this export" sub="honest scope">
					<div className="list">
						<div className="list-row">
							<Badge tone="warn">NO LEDGER</Badge>
							<span className="grow">
								<span className="title">Raw ledger entries</span>
								<span className="sub">No public ledger-entry read endpoint in this build.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO SERVER JOB</Badge>
							<span className="grow">
								<span className="title">Scheduled or emailed reports</span>
								<span className="sub">There is no report worker, storage bucket or delivery job.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO TAX MODEL</Badge>
							<span className="grow">
								<span className="title">Tax lots and statements</span>
								<span className="sub">
									No FIFO lot engine or tax statement is exposed; the UI must not compute
									one.
								</span>
							</span>
						</div>
					</div>
				</Panel>
			</div>
		</AppShell>
	);
}
