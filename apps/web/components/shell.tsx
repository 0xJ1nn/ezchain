import Link from "next/link";
import type { ReactNode } from "react";

const nav = [
	{
		label: "Overview",
		items: [
			{ id: "dashboard", href: "/", label: "Dashboard", glyph: "▤" },
			{ id: "positions", href: "/positions", label: "Positions", glyph: "▥", badge: "4" },
			{ id: "markets", href: "/markets", label: "Markets", glyph: "◫" },
			{ id: "performance", href: "/performance", label: "Performance", glyph: "◔" },
		],
	},
	{
		label: "Trade",
		items: [
			{ id: "wallets", href: "/wallets", label: "Wallets", glyph: "▣" },
			{ id: "ledger", href: "/ledger", label: "Activity", glyph: "≡" },
			{ id: "reports", href: "/reports", label: "Reports", glyph: "▦" },
		],
	},
	{
		label: "Automate",
		items: [
			{ id: "automations", href: "/automations", label: "Automations", glyph: "⟳", badge: "3" },
			{ id: "strategies", href: "/strategies", label: "Strategies", glyph: "◇" },
			{ id: "signals", href: "/signals", label: "Signals", glyph: "⌁" },
			{ id: "backtests", href: "/backtests", label: "Backtests", glyph: "∿" },
		],
	},
	{
		label: "Protect",
		items: [
			{ id: "risk", href: "/risk", label: "Risk", glyph: "◈" },
			{ id: "alerts", href: "/alerts", label: "Alerts", glyph: "◉", badge: "2" },
		],
	},
	{
		label: "System",
		items: [
			{ id: "chains", href: "/chains", label: "Chains & RPC", glyph: "⬡" },
			{ id: "integrations", href: "/integrations", label: "Integrations", glyph: "⇄" },
			{ id: "settings", href: "/settings", label: "Settings", glyph: "⚙" },
			{ id: "security", href: "/security", label: "Security", glyph: "⌂" },
			{ id: "developer", href: "/developer", label: "Developer", glyph: "⌘" },
			{ id: "operations", href: "/operations", label: "Operations", glyph: "◍" },
			{ id: "workspace", href: "/workspace", label: "Workspace", glyph: "◎" },
		],
	},
];

function isActive(href: string, active: string) {
	return href === "/" ? active === "dashboard" : active === href.slice(1);
}

export function Topbar() {
	const fixture = process.env.EZCHAIN_WEB_MODE === "fixture";
	return (
		<header className="topbar">
			<div className="brand">
				<Link className="brand-name" href="/">
					ezCHAIN
				</Link>
				<span className="brand-tag">Workstation</span>
			</div>
			<button className="ws-chip" type="button" title="Switch workspace">
				<span className="avatar" style={{ width: 18, height: 18, fontSize: 8 }}>
					JW
				</span>
				Personal workspace <span className="muted">▾</span>
			</button>
			<form className="search" role="search" action="/markets">
				<input
					type="search"
					name="q"
					placeholder="Search token, wallet, strategy, position…"
					aria-label="Search token, wallet, strategy or position"
				/>
			</form>
			<span className="chain chip" title="Active chain filter">
				⬡ All chains <span className="muted">▾</span>
			</span>
			<span className="mode paper" title="Execution mode">
				<span className="dot" />
				PAPER
			</span>
			<span className="chip" title="Market data source">
				{fixture ? "⟳ fixture data" : "⟳ live · dexscreener"}
			</span>
			<span className="spacer" />
			<Link className="icon-btn" href="/alerts" title="Alerts" aria-label="Alerts">
				◉<span className="count">2</span>
			</Link>
			<Link className="kill" href="/risk" title="Kill switch: halt new risk (k)">
				KILL SWITCH
			</Link>
			<span className="avatar" title="Account menu">
				JW
			</span>
		</header>
	);
}

export function Sidebar({ active }: { active: string }) {
	return (
		<nav className="sidebar" aria-label="Primary navigation">
			{nav.map((group) => (
				<div className="nav-group" key={group.label}>
					<div className="nav-label">{group.label}</div>
					{group.items.map((item) => {
						const on = isActive(item.href, active);
						return (
							<Link
								className={`nav-item${on ? " active" : ""}`}
								href={item.href}
								key={item.id}
								aria-label={item.label}
								aria-current={on ? "page" : undefined}
							>
								<span className="g" aria-hidden="true">
									{item.glyph}
								</span>
								<span className="t">{item.label}</span>
								{item.badge && <span className="badge">{item.badge}</span>}
							</Link>
						);
					})}
				</div>
			))}
			<div className="sidebar-foot">
				<Link href="/states">State gallery</Link>
				<Link href="/components">Components</Link>
				<Link href="/mobile">Mobile</Link>
				<div className="micro" style={{ marginTop: 8 }}>
					v0.1.0 · P0
				</div>
			</div>
		</nav>
	);
}

const mobileNav = [
	{ id: "dashboard", href: "/", label: "Home", glyph: "▤" },
	{ id: "positions", href: "/positions", label: "Positions", glyph: "▥" },
	{ id: "markets", href: "/markets", label: "Markets", glyph: "◫" },
	{ id: "automations", href: "/automations", label: "Automate", glyph: "⟳" },
	{ id: "more", href: "/more", label: "More", glyph: "⋯" },
];

const moreSections = new Set([
	"more",
	"risk",
	"alerts",
	"chains",
	"integrations",
	"settings",
	"security",
	"developer",
	"operations",
	"workspace",
	"states",
	"mobile",
	"components",
]);

function mobileActive(id: string, active: string) {
	return id === "more" ? moreSections.has(active) : active === id;
}

export function MobileNav({ active }: { active: string }) {
	return (
		<nav className="mobile-nav" aria-label="Primary mobile navigation">
			{mobileNav.map((item) => {
				const on = mobileActive(item.id, active);
				const current = item.id === "more" ? active === "more" : on;
				return (
					<Link
						key={item.id}
						href={item.href}
						className={on ? "on" : undefined}
						aria-current={current ? "page" : undefined}
					>
						<span className="g" aria-hidden="true">
							{item.glyph}
						</span>
						{item.label}
					</Link>
				);
			})}
		</nav>
	);
}

export function AppShell({ active, children }: { active: string; children: ReactNode }) {
	return (
		<>
			<a className="skip" href="#main">
				Skip to content
			</a>
			<div className="fixture-banner">
				<strong>Fixture preview</strong>
				<span>sample data · no API · no chain calls</span>
				<span className="spacer" />
				<span className="meta">M1 · shell + dashboard</span>
			</div>
			<Topbar />
			<Sidebar active={active} />
			<main id="main">{children}</main>
			<MobileNav active={active} />
		</>
	);
}
