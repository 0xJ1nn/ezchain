"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Logo } from "./brand/logo";
import { Mark } from "./brand/mark";
import { Ion } from "./ion";

// One Ionicon, a label and an optional mono count per entry, exactly as the app board's
// side menu. The active entry is bg-3 / text-1 with no glow.
const ENTRIES = [
	{ id: "home", href: "/", icon: "home", label: "Home" },
	{ id: "markets", href: "/markets", icon: "stats-chart", label: "Markets" },
	{
		id: "trade",
		href: "/tokens/solana/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		icon: "swap-horizontal",
		label: "Trade",
	},
	{ id: "watchlist", href: "/watchlist", icon: "star", label: "Watchlist", count: 8 },
	{ id: "portfolio", href: "/portfolio", icon: "pie-chart", label: "Portfolio", count: 6 },
	{ id: "wallets", href: "/wallets", icon: "wallet", label: "Wallets", count: 5 },
	{ id: "exits", href: "/exits", icon: "shield-checkmark", label: "Exits", count: 6 },
	{ id: "automate", href: "/automate", icon: "flash", label: "Automate", count: 4 },
];

// Pages that land on a menu entry rather than owning one of their own.
const GROUPS: Record<string, string> = {
	dashboard: "home",
	launches: "markets",
	signals: "markets",
	positions: "portfolio",
	ledger: "portfolio",
	performance: "portfolio",
	reports: "portfolio",
	strategies: "automate",
	backtests: "automate",
	alerts: "automate",
	automations: "automate",
};

export function activeEntry(active: string, pathname = "") {
	if (pathname.startsWith("/tokens/")) return "trade";
	if (active === "components" || active === "states") return "";
	return GROUPS[active] ?? active;
}

export function SideNav({ active }: { active: string }) {
	const pathname = usePathname();
	const current = activeEntry(active, pathname);
	return (
		<nav className="sidenav" aria-label="Primary navigation">
			<Link className="lg" href="/" aria-label="ezCHAIN home">
				<span className="logo-full">
					<Logo size={17} />
				</span>
				<span className="logo-mark">
					<Mark size={24} />
				</span>
			</Link>
			<div className="nav-items" id="primary-navigation-items">
				{ENTRIES.map((entry) => {
					const on = entry.id === current;
					return (
						<Link
							key={entry.id}
							href={entry.href}
							className={`nav-i${on ? " on" : ""}`}
							data-tip={entry.label}
							aria-label={entry.label}
							aria-current={on ? "page" : undefined}
						>
							<Ion name={entry.icon} />
							<span>{entry.label}</span>
							{entry.count ? <span className="n">{entry.count}</span> : null}
						</Link>
					);
				})}
			</div>
			<div className="ft">
				<Link
					href="/settings"
					className={`nav-i${active === "settings" ? " on" : ""}`}
					data-tip="Settings"
					aria-label="Settings"
				>
					<Ion name="settings" />
					<span>Settings</span>
				</Link>
				<button type="button" className="nav-i risk" data-tip="Risk notice" data-dlg="risk">
					<Ion name="warning" />
					<span>Risk notice</span>
				</button>
			</div>
		</nav>
	);
}
