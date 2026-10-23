import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Fact, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import {
	apiHostOnly,
	apiTokenConfigured,
	publicUrlConfigured,
	webMode,
} from "@/lib/runtime";
import { getTradePolicy } from "@/lib/trade-policy";
import { TradePolicyForm } from "./trade-policy-form";

export const dynamic = "force-dynamic";

const DOCS = "https://github.com/0xJ1nn/ezchain/blob/main";

export default async function SettingsPage() {
	const mode = webMode();
	const policy = await getTradePolicy();

	return (
		<AppShell active="settings">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Settings</div>
					<h1>Settings</h1>
					<p className="sub">
						Runtime facts for this deployment plus the persisted trade-sizing policy. The
						policy is enforced by the API before a quote; this page cannot compute or
						override a size in the browser.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/operations">
						Operations
					</Link>
					<Link className="btn" href="/security">
						Security
					</Link>
				</div>
			</header>

			{policy.ok ? (
				<div className="mb-12">
					<TradePolicyForm initial={policy.data} mode={mode} />
				</div>
			) : (
				<div className="mb-12">
					<Unavailable
						title="Trade policy unavailable"
						code={policy.error.code}
						message={policy.error.message}
					/>
				</div>
			)}

			<div className="grid cols-2">
				<Panel title="Runtime" sub="read on the server">
					<div className="stack-sm">
						<Fact label="Web data mode" value={<span className="mono">{mode}</span>} />
						<Fact
							label="API host"
							value={<span className="mono">{apiHostOnly()}</span>}
						/>
						<Fact
							label="API token"
							value={
								apiTokenConfigured() ? (
									<Badge tone="ok">configured · server-side</Badge>
								) : (
									<Badge tone="warn">not configured</Badge>
								)
							}
						/>
						<Fact
							label="Public URL override"
							value={
								publicUrlConfigured() ? (
									<Badge tone="ghost">configured</Badge>
								) : (
									<span className="muted">not set; request host is used</span>
								)
							}
						/>
					</div>
					<p className="note mt-12">
						Only the API host is printed. The token value never reaches this page, the browser
						bundle or any HTML response. Mutations go through the Next proxy, which injects
						the deployment token server-side when one is configured.
					</p>
				</Panel>

				<Panel title="Data source" sub="fixture or live">
					<div className="stack-sm">
						<Fact label="EZCHAIN_WEB_MODE" value={<span className="mono">{mode}</span>} />
						<Fact
							label="Market source"
							value={
								mode === "fixture" ? (
									<span className="muted">sample fixture · no API call</span>
								) : (
									<span className="muted">API · provider named per row</span>
								)
							}
						/>
						<Fact
							label="Execution mode"
							value={<Badge tone="ghost">PAPER</Badge>}
						/>
					</div>
					<p className="note warn mt-12">
						<strong>No silent fallback.</strong> In live mode a failed read renders its reason
						code; it never swaps in fixture rows. Fixture mode labels sample data FIXTURE on
						every panel.
					</p>
				</Panel>

				<Panel title="Server-side configuration" sub="one home">
					<p className="small muted">
						These are process environment keys owned by the API. The trade policy has one
						persisted override; the values below are the boot defaults used when no row has
						been saved.
					</p>
					<div className="stack-sm small mono mt-12">
						<div>EZCHAIN_DATABASE_URL</div>
						<div>EZCHAIN_API_TOKEN</div>
						<div>EZCHAIN_DATA_MODE</div>
						<div>EZCHAIN_PAPER_FEE_BPS</div>
						<div>EZCHAIN_MAX_ORDER_USD</div>
						<div>EZCHAIN_MAX_POSITION_USD</div>
						<div>EZCHAIN_MAX_DAILY_LOSS_USD</div>
						<div>EZCHAIN_MAX_QUOTE_AGE_SECONDS</div>
						<div>EZCHAIN_MARKET_SCAN_INTERVAL_SECONDS</div>
						<div>EZCHAIN_TRADE_DEFAULT_PROFILE</div>
						<div>EZCHAIN_TRADE_MIN_AMOUNT_USD</div>
						<div>EZCHAIN_TRADE_MIN_PCT_EQUITY</div>
						<div>EZCHAIN_TRADE_MAX_AMOUNT_USD</div>
						<div>EZCHAIN_TRADE_MAX_PCT_EQUITY</div>
						<div>EZCHAIN_TRADE_&lt;PROFILE&gt;_MIN_AMOUNT_USD</div>
						<div>EZCHAIN_TRADE_&lt;PROFILE&gt;_MAX_PCT_EQUITY</div>
					</div>
					<p className="note mt-12">
						Values are validated at API boot and are intentionally not echoed as a secret.
						The persisted policy is the authoritative sizing input.
					</p>
				</Panel>

				<Panel title="What is not here" sub="honest boundary">
					<div className="list">
						<div className="list-row">
							<Badge tone="ghost">SERVER</Badge>
							<span className="grow">
								<span className="title">Order size, equity and previews</span>
								<span className="sub">
									Resolved by <span className="mono">/api/v1/settings/trade-policy</span>{" "}
									and the paper ledger. The browser only renders the response.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO APPEARANCE</Badge>
							<span className="grow">
								<span className="title">Theme and density</span>
								<span className="sub">
									The dark visual pass and user theme preference are outside this build.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">NO RESET</Badge>
							<span className="grow">
								<span className="title">Data reset or danger zone</span>
								<span className="sub">
									A reset is a documented operator action outside this UI, not a browser
									button.
								</span>
							</span>
						</div>
					</div>
					<div className="row mt-12">
						<a className="btn sm" href={`${DOCS}/docs/INFRA.md`} rel="noreferrer">
							Environment docs
						</a>
						<a className="btn sm" href={`${DOCS}/apps/web/README.md`} rel="noreferrer">
							Web README
						</a>
						<ButtonLink href="/developer" size="sm">
							Developer
						</ButtonLink>
					</div>
				</Panel>
			</div>

			<Panel title="Change summary" sub="server process" className="mt-16">
				<p className="small muted">
					The trade policy is saved to one <span className="mono">app_settings</span> row with
					an audit timestamp and principal. Other runtime keys still require a deliberate
					deployment change and a process restart. Health and migration state are on{" "}
					<Link href="/operations">Operations</Link>.
				</p>
			</Panel>
		</AppShell>
	);
}
