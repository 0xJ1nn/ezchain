import { AppShell } from "@/components/shell";
import { FixtureBadge } from "@/components/ops";
import { Badge, Panel, Stat, TokenAvatar } from "@/components/ui";

const nav = [
	{ label: "Home", glyph: "▤", on: true },
	{ label: "Browse", glyph: "◫" },
	{ label: "Signals", glyph: "⌁" },
	{ label: "Positions", glyph: "▥" },
	{ label: "More", glyph: "⋯" },
];

export default function MobilePage() {
	return (
		<AppShell active="mobile">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Reference / Mobile</div>
					<h1>Mobile</h1>
					<p className="sub">
						The real responsive shell is the same Next app at a narrow viewport: the sidebar
						collapses to a bottom nav and tables scroll instead of squeezing. This page shows
						the actual primitives at phone width plus the rules the stylesheet applies.
					</p>
				</div>
				<div className="page-actions">
					<FixtureBadge />
					<a className="btn" href="https://github.com/0xJ1nn/ezchain/blob/main/design/wireframe/28-mobile.html" rel="noreferrer">
						Wireframe
					</a>
				</div>
			</header>

			<div className="grid cols-3">
				<Panel title="Dashboard at 390px" sub="fixture composition">
					<div className="mobile-frame">
						<div className="m-top">
							<strong style={{ fontSize: 10 }}>ezCHAIN</strong>
							<span className="badge ghost" style={{ fontSize: 8 }}>
								FIXTURE
							</span>
							<span className="mode paper" style={{ marginLeft: "auto", fontSize: 8 }}>
								PAPER
							</span>
						</div>
						<div className="m-body">
							<div className="kpi-band" style={{ flexWrap: "wrap" }}>
								<Stat label="Equity" value="$9,870.80" meta="paper" />
								<Stat label="Unrealized" value="−$129.20" tone="err" meta="known marks" />
							</div>
							<div className="panel" style={{ boxShadow: "none" }}>
								<div className="panel-head">
									<h2>Open positions</h2>
									<span className="sub">4</span>
								</div>
								<div className="panel-body" style={{ display: "grid", gap: 6 }}>
									<div className="between small">
										<span className="row">
											<TokenAvatar size="sm">BO</TokenAvatar>
											<span>BONK</span>
										</span>
										<span className="ok mono">+42.6%</span>
									</div>
									<div className="between small">
										<span className="row">
											<TokenAvatar size="sm">AE</TokenAvatar>
											<span>AERO</span>
										</span>
										<span className="err mono">−8.3%</span>
									</div>
									<div className="between small">
										<span className="row">
											<TokenAvatar size="sm">MO</TokenAvatar>
											<span>MOCHI</span>
										</span>
										<Badge tone="warn">unknown mark</Badge>
									</div>
								</div>
							</div>
						</div>
						<div className="m-nav">
							{nav.map((item) => (
								<div className={item.on ? "on" : undefined} key={item.label}>
									<span aria-hidden="true">{item.glyph}</span> {item.label}
								</div>
							))}
						</div>
					</div>
					<p className="micro mt-12">
						Same tokens and primitives as the desktop shell. Values are labelled fixture
						content.
					</p>
				</Panel>

				<Panel title="Position card" sub="table → card">
					<div className="mobile-frame">
						<div className="m-top">
							<strong style={{ fontSize: 10 }}>Positions</strong>
							<span className="badge ghost" style={{ fontSize: 8 }}>
								FIXTURE
							</span>
						</div>
						<div className="m-body">
							<div className="panel" style={{ boxShadow: "none" }}>
								<div className="panel-body">
									<div className="between">
										<span className="row">
											<TokenAvatar>BO</TokenAvatar>
											<span>
												<span className="strong">BONK</span>
												<span className="sub" style={{ display: "block" }}>
													Solana · riding
												</span>
											</span>
										</span>
										<span className="mono ok">+42.6%</span>
									</div>
									<div className="hr" />
									<div className="between small">
										<span className="muted">Value</span>
										<span className="mono">$62.84</span>
									</div>
									<div className="between small">
										<span className="muted">Mark</span>
										<span className="mono">$0.00003142</span>
									</div>
									<div className="between small">
										<span className="muted">Source</span>
										<span className="mono">jupiter · 2s</span>
									</div>
									<div className="row mt-8">
										<button className="btn danger sm" type="button" disabled>
											Sell now
										</button>
										<button className="btn sm" type="button" disabled>
											Details
										</button>
									</div>
								</div>
							</div>
						</div>
						<div className="m-nav">
							{nav.map((item) => (
								<div className={item.label === "Positions" ? "on" : undefined} key={item.label}>
									<span aria-hidden="true">{item.glyph}</span> {item.label}
								</div>
							))}
						</div>
					</div>
					<p className="micro mt-12">
						Actions are disabled: execution is not wired in this build.
					</p>
				</Panel>

				<Panel title="Touch rules" sub="applied by the shell">
					<div className="list">
						<div className="list-row">
							<Badge tone="ghost">NAV</Badge>
							<span className="grow">
								<span className="title">Bottom navigation</span>
								<span className="sub">
									Five targets, 44px minimum height. The sidebar is hidden below 760px.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">TABLES</Badge>
							<span className="grow">
								<span className="title">Horizontal scroll</span>
								<span className="sub">
									Tables keep their columns and scroll rather than dropping numbers.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">CHARTS</Badge>
							<span className="grow">
								<span className="title">Scrollable chart frame</span>
								<span className="sub">
									Charts keep a 560px minimum inside a horizontal scroller.
								</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="warn">SAFE AREA</Badge>
							<span className="grow">
								<span className="title">Kill switch stays reachable</span>
								<span className="sub">
									The top bar keeps the kill-switch link visible at narrow widths.
								</span>
							</span>
						</div>
					</div>
				</Panel>
			</div>

			<Panel title="Responsive rules" sub="actual breakpoints" className="mt-16" flush>
				<div className="table-wrap">
					<table className="table compact">
						<thead>
							<tr>
								<th>Width</th>
								<th>Shell behaviour</th>
							</tr>
						</thead>
						<tbody>
							<tr>
								<td className="mono">≥1440px</td>
								<td>Full sidebar, split panes and 3–6 column grids.</td>
							</tr>
							<tr>
								<td className="mono">1100–1439px</td>
								<td>
									Sidebar stays expanded at 240px with readable labels; split panes
									become a single column.
								</td>
							</tr>
							<tr>
								<td className="mono">761–1099px</td>
								<td>
									Sidebar stays expanded; 3–6 column grids collapse to three, tables
									still show wide columns.
								</td>
							</tr>
							<tr>
								<td className="mono">≤760px</td>
								<td>
									Sidebar hidden, bottom nav shown, top bar wraps, grids to one column and
									low-priority table columns hidden.
								</td>
							</tr>
						</tbody>
					</table>
				</div>
			</Panel>

			<Panel title="Not a native app" sub="scope" className="mt-16">
				<p className="small muted">
					There is no iOS or Android build, push notification or offline cache. The product
					targets a responsive web UI first. This page is a gallery of that responsive shell,
					not a promise of a mobile client.
				</p>
			</Panel>
		</AppShell>
	);
}
