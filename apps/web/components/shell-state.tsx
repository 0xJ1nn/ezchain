"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useState,
	type ReactNode,
} from "react";
import { ThemeToggle } from "./theme-toggle";
import { Ion } from "./ion";

const RAIL_KEY = "ezchain.sidenav.rail";

type ShellState = {
	rail: boolean;
	drawer: boolean;
	agentOpen: boolean;
	toggleNav: () => void;
	closeDrawer: () => void;
	toggleAgent: () => void;
};

const ShellContext = createContext<ShellState | null>(null);

export function useShell(): ShellState {
	const value = useContext(ShellContext);
	if (!value) throw new Error("Shell controls must be used inside ShellProvider");
	return value;
}

// The shell owns three independent pieces of state: the full-menu/rail choice (persisted),
// the phone drawer (transient) and the agent panel. On Trade below 1680 px the menu folds to
// the rail while the panel is open, because the chart and the ticket need the width; the
// hamburger still opens it.
export function ShellProvider({ children }: { children: ReactNode }) {
	const pathname = usePathname();
	const [storedRail, setStoredRail] = useState(false);
	const [userRail, setUserRail] = useState<boolean | null>(null);
	const [rail, setRail] = useState(false);
	const [drawer, setDrawer] = useState(false);
	const [agentOpen, setAgentOpen] = useState(false);
	const [openMenu, setOpenMenu] = useState<string | null>(null);

	useEffect(() => {
		let stored = false;
		try {
			stored = window.localStorage.getItem(RAIL_KEY) === "1";
		} catch {
			stored = false;
		}
		setStoredRail(stored);
	}, []);

	// The agent panel is not mounted until its own field lands, so the shell starts with
	// the third column closed. The slot is still part of the grid and the Agent button
	// opens it; content arrives with the agent field.
	useEffect(() => {
		const fit = () => {
			const phone = window.innerWidth < 640;
			const isTrade = pathname.startsWith("/tokens/");
			const auto = !phone && isTrade && agentOpen && window.innerWidth < 1680;
			setRail(userRail ?? (storedRail || auto));
		};
		fit();
		window.addEventListener("resize", fit);
		return () => window.removeEventListener("resize", fit);
	}, [pathname, agentOpen, storedRail, userRail]);

	const closeDrawer = useCallback(() => setDrawer(false), []);
	const toggleAgent = useCallback(() => setAgentOpen((value) => !value), []);

	const toggleNav = useCallback(() => {
		if (window.innerWidth < 640) {
			setDrawer((value) => !value);
			return;
		}
		const next = !rail;
		setUserRail(next);
		setRail(next);
		try {
			window.localStorage.setItem(RAIL_KEY, next ? "1" : "0");
		} catch {
			// A blocked storage API must not break navigation.
		}
	}, [rail]);

	useEffect(() => {
		const onKey = (event: KeyboardEvent) => {
			if (!(event.metaKey || event.ctrlKey)) return;
			const key = event.key.toLowerCase();
			if (key === "j") {
				event.preventDefault();
				setAgentOpen((value) => !value);
			} else if (key === "k") {
				event.preventDefault();
				document.querySelector<HTMLInputElement>(".top input[type='search']")?.focus();
			}
		};
		const onEscape = (event: KeyboardEvent) => {
			if (event.key !== "Escape") return;
			setDrawer(false);
			setOpenMenu(null);
		};
		window.addEventListener("keydown", onKey);
		window.addEventListener("keydown", onEscape);
		return () => {
			window.removeEventListener("keydown", onKey);
			window.removeEventListener("keydown", onEscape);
		};
	}, []);

	const value: ShellState = {
		rail,
		drawer,
		agentOpen,
		toggleNav,
		closeDrawer,
		toggleAgent,
	};

	return (
		<ShellContext.Provider value={value}>
			<MenuContext.Provider value={{ openMenu, setOpenMenu }}>
				{children}
			</MenuContext.Provider>
		</ShellContext.Provider>
	);
}

// Menus are small enough to share one "only one open at a time" rule.
const MenuContext = createContext<{
	openMenu: string | null;
	setOpenMenu: (id: string | null) => void;
} | null>(null);

function useMenu() {
	const value = useContext(MenuContext);
	if (!value) throw new Error("Shell menu must be used inside ShellProvider");
	return value;
}

// Wraps the board's grid: side menu spans both rows, the top bar spans main and panel.
export function ShellFrame({
	topbar,
	nav,
	panel,
	active,
	children,
}: {
	topbar: ReactNode;
	nav: ReactNode;
	panel: ReactNode;
	active: string;
	children: ReactNode;
}) {
	const { rail, drawer, agentOpen, closeDrawer } = useShell();
	const className = [
		"app",
		rail ? "rail" : "",
		drawer ? "drawer" : "",
		agentOpen ? "" : "noagent",
	]
		.filter(Boolean)
		.join(" ");
	return (
		<div className={className}>
			{nav}
			<div className="dscrim" onClick={closeDrawer} aria-hidden="true" />
			{topbar}
			{children}
			{panel}
			<MobileTabs active={active} />
		</div>
	);
}

export function HamburgerButton() {
	const { toggleNav } = useShell();
	return (
		<button type="button" className="hamb" aria-label="Menu" onClick={toggleNav}>
			<Ion name="menu" />
		</button>
	);
}

export function AgentButton() {
	const { agentOpen, toggleAgent } = useShell();
	return (
		<button
			type="button"
			className="agentbtn"
			aria-pressed={agentOpen}
			onClick={toggleAgent}
		>
			<span className="agent-dot" aria-hidden="true" />
			<span>Agent</span>
			<span className="kbd">⌘J</span>
		</button>
	);
}

// The disclaimer field owns the popover body; the shell only provides the trigger and slot.
export function DonateButton() {
	const { openMenu, setOpenMenu } = useMenu();
	const open = openMenu === "donate";
	return (
		<div className="menu-anchor">
			<button
				type="button"
				className="donate"
				aria-label="Donate"
				aria-expanded={open}
				onClick={() => setOpenMenu(open ? null : "donate")}
			>
				<Ion name="heart" size={16} />
				<span>Donate</span>
			</button>
			<div className={`menu dpop${open ? " on" : ""}`} id="dpop" />
		</div>
	);
}

export function AccountMenu() {
	const { openMenu, setOpenMenu } = useMenu();
	const open = openMenu === "account";
	return (
		<div className="menu-anchor">
			<button
				type="button"
				className="me"
				aria-label="Account"
				aria-expanded={open}
				onClick={() => setOpenMenu(open ? null : "account")}
			>
				A
			</button>
			<div className={`menu${open ? " on" : ""}`} id="menu">
				<div className="who">
					<span className="me" style={{ cursor: "default" }}>
						A
					</span>
					<div>
						<b>alex</b>
						<span>Personal workspace · paper</span>
					</div>
				</div>
				<Link href="/settings" data-set="general">
					Settings<span className="kbd">⌘,</span>
				</Link>
				<Link href="/settings" data-set="custody">
					Accounts and custody
				</Link>
				<Link href="/settings" data-set="security">
					Security and PIN
				</Link>
				<ThemeToggle />
				<hr />
				<Link href="/workspace">Switch workspace</Link>
				<Link href="/">Sign out</Link>
			</div>
		</div>
	);
}

const TABS = [
	{ label: "Home", href: "/", icon: "home", id: "dashboard" },
	{ label: "Markets", href: "/markets", icon: "stats-chart", id: "markets" },
	{
		label: "Trade",
		href: "/tokens/solana/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
		icon: "swap-horizontal",
		id: "trade",
	},
	{ label: "Watchlist", href: "/watchlist", icon: "star", id: "watchlist" },
	{ label: "Portfolio", href: "/portfolio", icon: "pie-chart", id: "portfolio" },
];

const TAB_GROUPS: Record<string, string> = {
	launches: "markets",
	signals: "markets",
	positions: "portfolio",
	ledger: "portfolio",
	performance: "portfolio",
	reports: "portfolio",
};

function mobileTab(pathname: string, active: string) {
	if (pathname.startsWith("/tokens/")) return "trade";
	const id = TAB_GROUPS[active] ?? active;
	return TABS.some((tab) => tab.id === id) ? id : "";
}

// Phone bottom bar: Home, Markets, Trade, Watchlist, Portfolio. The sticky Buy/Sell bar
// on Trade belongs to the trade field and is not touched here.
export function MobileTabs({ active = "" }: { active?: string }) {
	const pathname = usePathname();
	const current = mobileTab(pathname, active);
	return (
		<nav className="mtabs" aria-label="Primary mobile navigation">
			{TABS.map((tab) => {
				const on = tab.id === current;
				return (
					<Link
						key={tab.id}
						href={tab.href}
						className={on ? "on" : undefined}
						aria-current={on ? "page" : undefined}
					>
						<Ion name={tab.icon} size={20} />
						<span>{tab.label}</span>
					</Link>
				);
			})}
		</nav>
	);
}
