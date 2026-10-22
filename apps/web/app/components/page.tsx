import { AppShell } from "@/components/shell";
import { FixtureBadge } from "@/components/ops";
import { MarketSpark } from "@/components/market-spark";
import {
	Badge,
	ButtonLink,
	LiveDot,
	Meter,
	Panel,
	Stat,
	TokenAvatar,
} from "@/components/ui";

export default function ComponentsPage() {
	return (
		<AppShell active="components">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Reference / Components</div>
					<h1>Components</h1>
					<p className="sub">
						The primitives the operator UI actually uses. Sample values are fixture strings
						from the shared view models; the gallery shows real components, not screenshots.
					</p>
				</div>
				<div className="page-actions">
					<FixtureBadge />
					<a className="btn" href="https://github.com/0xJ1nn/ezchain/blob/main/design/wireframe/29-components.html" rel="noreferrer">
						Wireframe
					</a>
				</div>
			</header>

			<div className="split">
				<div className="stack">
					<Panel title="Badges" sub="tone vocabulary">
						<div className="row">
							<Badge>default</Badge>
							<Badge tone="ok">ok</Badge>
							<Badge tone="warn">warn</Badge>
							<Badge tone="err">err</Badge>
							<Badge tone="info">info</Badge>
							<span className="badge solid">solid</span>
							<Badge tone="ghost">ghost</Badge>
							<Badge tone="info">info</Badge>
							<Badge tone="ghost">FIXTURE</Badge>
							<Badge tone="ghost">PAPER</Badge>
						</div>
						<p className="note mt-12">
							Tone is semantic: <span className="mono">err</span> means a hard failure or a
							negative money value, not decoration. Fixture and paper labels are always
							visible in context.
						</p>
					</Panel>

					<Panel title="Stats" sub="KPI primitive" flush>
						<div className="kpi-band">
							<Stat label="Hero" value="$9,870.80" meta="paper equity" hero />
							<Stat label="Signed" value="−$129.20" tone="err" meta="unrealized" />
							<Stat label="Count" value="4" meta="open positions" />
						</div>
					</Panel>

					<Panel title="Meters" sub="bounded visual only">
						<div className="stack-sm">
							<div className="between small">
								<span>Normal</span>
								<span className="mono">60%</span>
							</div>
							<Meter width="60%" label="Normal usage" valueText="60 percent" />
							<div className="between small">
								<span>Warning</span>
								<span className="mono">82%</span>
							</div>
							<Meter
								width="82%"
								tone="warn"
								label="Warning usage"
								valueText="82 percent"
							/>
							<div className="between small">
								<span>Error</span>
								<span className="mono">97%</span>
							</div>
							<Meter width="97%" tone="err" label="Error usage" valueText="97 percent" />
						</div>
						<p className="note mt-12">
							Meters display a supplied width. They are not fed by a live limit read model;
							the risk page says so rather than inventing a cap.
						</p>
					</Panel>

					<Panel title="Tables" sub="dense data display" flush>
						<div className="table-wrap">
							<table className="table compact">
								<thead>
									<tr>
										<th>Token</th>
										<th>State</th>
										<th className="num">Value</th>
										<th>Source</th>
									</tr>
								</thead>
								<tbody>
									<tr>
										<td>
											<span className="row">
												<TokenAvatar size="sm">BO</TokenAvatar>
												<span>BONK</span>
											</span>
										</td>
										<td>
											<Badge tone="ok">riding</Badge>
										</td>
										<td className="num">$62.84</td>
										<td className="small">
											<LiveDot /> jupiter · 2s
										</td>
									</tr>
									<tr>
										<td>
											<span className="row">
												<TokenAvatar size="sm">MO</TokenAvatar>
												<span>MOCHI</span>
											</span>
										</td>
										<td>
											<Badge tone="warn">unknown mark</Badge>
										</td>
										<td className="num">—</td>
										<td className="small">
											<LiveDot tone="dead" /> no source
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</Panel>
				</div>

				<div className="stack">
					<Panel title="Tokens & live dots" sub="identity and freshness">
						<div className="row">
							<TokenAvatar>BK</TokenAvatar>
							<TokenAvatar size="sm">AE</TokenAvatar>
							<span className="chain">
								<span className="sq">S</span> Solana
							</span>
						</div>
						<div className="hr" />
						<div className="stack-sm small">
							<div>
								<LiveDot /> fresh mark
							</div>
							<div>
								<LiveDot tone="stale" /> stale mark
							</div>
							<div>
								<LiveDot tone="dead" /> unknown or dead source
							</div>
						</div>
						<p className="note mt-12">
							A dot is always paired with a source name and an age. The dot alone is never the
							only evidence of freshness.
						</p>
					</Panel>

					<Panel title="Buttons and links" sub="all targets are real routes">
						<div className="row">
							<ButtonLink href="/markets" variant="primary">
								Primary
							</ButtonLink>
							<ButtonLink href="/markets">Default</ButtonLink>
							<ButtonLink href="/markets" variant="ghost">
								Ghost
							</ButtonLink>
							<ButtonLink href="/risk" variant="danger">
								Danger
							</ButtonLink>
						</div>
						<div className="row mt-12">
							<ButtonLink href="/positions" size="sm">
								Small
							</ButtonLink>
							<ButtonLink href="/positions" block>
								Block link
							</ButtonLink>
						</div>
						<p className="note mt-12">
							There is no standalone button primitive on purpose: navigation is a link. A
							disabled tile action is a disabled button with an explanation, never a live
							control that does nothing.
						</p>
					</Panel>

					<Panel title="Chart frame" sub="fixture sparkline">
						<div className="between">
							<span className="mono">BONK · 7d</span>
							<span className="row" style={{ gap: 8 }}>
								<MarketSpark
									values={[8, 9, 8.4, 10.2, 11, 10.4, 12]}
									label="Sample upward sparkline"
								/>
								<Badge tone="ghost">FIXTURE</Badge>
							</span>
						</div>
						<div className="hr" />
						<div className="between">
							<span className="mono">AERO · 7d</span>
							<span className="row" style={{ gap: 8 }}>
								<MarketSpark
									values={[12, 11.2, 11.8, 10.4, 10.8, 9.9, 9.4]}
									tone="err"
									label="Sample downward sparkline"
								/>
								<Badge tone="ghost">FIXTURE</Badge>
							</span>
						</div>
						<p className="note mt-12">
							The series is a display fixture. Market charts on real pages come from the API
							and carry their source and freshness.
						</p>
					</Panel>

					<Panel title="Notes and annotations" sub="explanatory primitives">
						<div className="note">A plain note for supporting context.</div>
						<div className="note warn mt-12">A warning note for a live risk or stale input.</div>
						<div className="note err mt-12">An error note with the API reason.</div>
						<div className="annotation mt-12">
							<b>Annotation</b> — dashed, used for wireframe rationale. Press{" "}
							<kbd>k</kbd> to reach the kill switch from anywhere.
						</div>
					</Panel>
				</div>
			</div>

			<Panel title="Spacing and type" sub="existing tokens" className="mt-16">
				<div className="grid cols-3">
					<div>
						<div className="micro">Micro label</div>
						<div className="small">Small supporting text</div>
						<div>Body text</div>
						<div className="strong">Strong body text</div>
					</div>
					<div>
						<div className="mono">mono · 0.00003142</div>
						<div className="mono">mono · 2026-10-22T09:41:08Z</div>
						<div className="muted">muted · unknown is —</div>
					</div>
					<div>
						<div className="row">
							<span className="badge ghost">chip</span>
							<span className="tag">tag</span>
							<span className="pill badge">pill</span>
						</div>
						<div className="micro mt-8">No new visual language was introduced for this slice.</div>
					</div>
				</div>
			</Panel>
		</AppShell>
	);
}
