import Link from "next/link";
import type { ReactNode } from "react";
import { Sidebar } from "./sidebar";

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
			<Link className="ws-chip" href="/workspace" title="Workspace">
				<span className="avatar" style={{ width: 18, height: 18, fontSize: 8 }}>
					OP
				</span>
				Personal workspace
			</Link>
			<form className="search" role="search" action="/markets">
				<input
					type="search"
					name="q"
					placeholder="Search markets (symbol, name or address)"
					aria-label="Search markets (symbol, name or address)"
				/>
			</form>
			<Link
				className="chain chip"
				href="/markets"
				title="Open the multi-chain market browser"
			>
				<span aria-hidden="true">⬡</span> All chains
			</Link>
			<span className="mode paper" title="Execution mode">
				<span className="dot" />
				PAPER
			</span>
			<span className="source chip" title="Market data source">
				{fixture ? "⟳ fixture data" : "⟳ live · dexscreener"}
			</span>
			<span className="spacer" />
			<Link className="icon-btn" href="/alerts" title="Alerts" aria-label="Alerts">
				◉
			</Link>
			<Link className="kill" href="/risk" title="Kill switch: halt new risk (k)">
				KILL SWITCH
			</Link>
			<Link className="avatar" href="/workspace" title="Workspace">
				OP
			</Link>
		</header>
	);
}

const mobileNav = [
	{ id: "dashboard", href: "/", label: "Home", glyph: "▤" },
	{ id: "markets", href: "/markets", label: "Browse", glyph: "◫" },
	{ id: "signals", href: "/signals", label: "Signals", glyph: "⌁" },
	{ id: "positions", href: "/positions", label: "Positions", glyph: "▥" },
	{ id: "more", href: "/more", label: "More", glyph: "⋯" },
];

// Home, Browse, Signals and Positions are one tap away; every other surface is kept
// under More. This mirrors the desktop groups without duplicating Signals.
const moreSections = new Set([
	"more",
	"performance",
	"wallets",
	"ledger",
	"reports",
	"automations",
	"strategies",
	"backtests",
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
				const current =
					item.id === "more"
						? active === "more"
							? "page"
							: on
								? "true"
								: undefined
						: on
							? "page"
							: undefined;
				return (
					<Link
						key={item.id}
						href={item.href}
						className={on ? "on" : undefined}
						aria-current={current}
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
	const fixture = process.env.EZCHAIN_WEB_MODE === "fixture";
	return (
		<>
			<a className="skip" href="#main">
				Skip to content
			</a>
			<div className={fixture ? "fixture-banner" : "live-banner"}>
				<strong>{fixture ? "Fixture preview" : "Live paper slice"}</strong>
				<span>
					{fixture
						? "sample data · no API · no chain calls"
						: "live markets · paper execution · no keys"}
				</span>
				<span className="spacer" />
				<span className="meta">
					{fixture ? "P0 · fixture mode" : "P0 · DexScreener + multi-chain quotes"}
				</span>
			</div>
			<Topbar />
			<Sidebar active={active} />
			<main id="main">{children}</main>
			<MobileNav active={active} />
		</>
	);
}
