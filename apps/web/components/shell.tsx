import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import { TopbarKillSwitch } from "@/app/risk/kill-switch";
import {
	TopbarQuickTrade,
	type QuickTradeOption,
} from "@/components/topbar-quick-trade";
import { getMarkets, getQuote, type Reason } from "@/lib/api";
import { getKillSwitch } from "@/lib/paper-api";
import { PRODUCT_VERSION } from "@/lib/product-version";
import { getExecutionMode } from "@/lib/system-api";
import { Sidebar } from "./sidebar";
import { ThemeToggle } from "./theme-toggle";

// The switch state is read from the API on each render. It streams inside its own
// boundary so a slow or unreachable API never holds the rest of the shell hostage.
async function TopbarKillSwitchState() {
	const result = await getKillSwitch();
	return (
		<TopbarKillSwitch
			initial={result.ok ? result.data : null}
			initialError={result.ok ? null : result.error.message}
		/>
	);
}

// The quick-trade panel gets its rows and quotes from the server, which knows the data
// mode. A failed quote becomes a typed reason, never a guessed price or a hidden row.
async function TopbarQuickTradeState() {
	const list = await getMarkets({ limit: 12, sort: "trending" }).catch(() => null);
	const options: QuickTradeOption[] = await Promise.all(
		(list?.rows ?? []).map(async (row) => {
			try {
				const quote = await getQuote({
					chain: row.chain,
					token: row.base_token,
					side: "buy",
					amountUsd: "100",
				});
				return { row, quote, reason: null };
			} catch (error) {
				const apiError = error as { code?: string; message?: string; retryable?: boolean };
				const reason: Reason = {
					code: apiError.code ?? "QUOTE_UNAVAILABLE",
					message: apiError.message ?? "No quote was returned for this market.",
					retryable: apiError.retryable ?? false,
				};
				return { row, quote: null, reason };
			}
		}),
	);
	return <TopbarQuickTrade options={options} />;
}

// The execution mode is the value the API derived, never a literal. A read that fails or
// answers without one renders UNKNOWN and says why, so the chip cannot claim a mode the
// deployment might not be in.
async function TopbarModeChip() {
	const result = await getExecutionMode();
	if (!result.ok) {
		return (
			<span
				className="mode unknown"
				title={`Execution mode unavailable · ${result.error.code}`}
			>
				<span className="dot" />
				UNKNOWN
			</span>
		);
	}
	const live = result.data === "live";
	return (
		<span
			className={live ? "mode live" : "mode paper"}
			title={`Execution mode · ${result.data} · derived by the API`}
		>
			<span className="dot" />
			{result.data.toUpperCase()}
		</span>
	);
}

function ModeChipFallback() {
	return (
		<span className="mode pending" title="Reading the execution mode from the API">
			<span className="dot" />
			…
		</span>
	);
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
			<Link className="ws-chip" href="/workspace" title="Workspace">
				<span className="avatar" style={{ width: 18, height: 18, fontSize: 8 }}>
					OP
				</span>
				Personal workspace
			</Link>
			<form className="search" role="search" action="/markets" method="get">
				<input
					type="search"
					name="q"
					maxLength={80}
					placeholder="Search markets (symbol, name or address)"
					aria-label="Search markets (symbol, name or address)"
				/>
			</form>
			<Suspense
				fallback={
					<button type="button" className="btn sm" disabled>
						⚡ Quick trade
					</button>
				}
			>
				<TopbarQuickTradeState />
			</Suspense>
			<Suspense fallback={<ModeChipFallback />}>
				<TopbarModeChip />
			</Suspense>
			<span className="source chip" title="Market data source">
				{fixture ? "⟳ fixture data" : "⟳ live · dexscreener"}
			</span>
			<span className="spacer" />
			<Link className="icon-btn" href="/alerts" title="Alerts" aria-label="Alerts">
				◉
			</Link>
			<ThemeToggle />
			<Suspense
				fallback={
					<span className="kill pending" aria-hidden="true">
						<span className="kill-label">
							<span className="kill-name">Kill switch</span>
							<span className="kill-state">…</span>
						</span>
					</span>
				}
			>
				<TopbarKillSwitchState />
			</Suspense>
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
	"watchlist",
	"performance",
	"wallets",
	"ledger",
	"reports",
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
					{fixture ? "Fixture mode" : "DexScreener + multi-chain quotes"}
				</span>
			</div>
			<Topbar />
			<Sidebar active={active} version={PRODUCT_VERSION} />
			<main id="main">{children}</main>
			<MobileNav active={active} />
		</>
	);
}
