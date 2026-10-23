"use client";

import Link from "next/link";
import { useEffect, useState, type FocusEvent, type MouseEvent } from "react";

const STORAGE_KEY = "ezchain.sidebar.collapsed";

// Runs while the server HTML is parsed, before first paint, so a persisted rail never
// flashes back to the expanded shell on reload. The React state catches up on mount.
const BOOTSTRAP = `try{if(window.localStorage.getItem(${JSON.stringify(STORAGE_KEY)})==="1"){document.body.classList.add("sidebar-collapsed")}}catch(e){}`;

const nav = [
	{
		label: "Discovery",
		items: [
			{ id: "markets", href: "/markets", label: "Browse", glyph: "◫" },
			{ id: "signals", href: "/signals", label: "Signals", glyph: "⌁" },
		],
	},
	{
		label: "Overview",
		items: [
			{ id: "dashboard", href: "/", label: "Dashboard", glyph: "▤" },
			{ id: "positions", href: "/positions", label: "Positions", glyph: "▥" },
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
			{ id: "automations", href: "/automations", label: "Automations", glyph: "⟳" },
			{ id: "strategies", href: "/strategies", label: "Strategies", glyph: "◇" },
			{ id: "backtests", href: "/backtests", label: "Backtests", glyph: "∿" },
		],
	},
	{
		label: "Protect",
		items: [
			{ id: "risk", href: "/risk", label: "Risk", glyph: "◈" },
			{ id: "alerts", href: "/alerts", label: "Alerts", glyph: "◉" },
		],
	},
	{
		label: "System",
		items: [
			{ id: "chains", href: "/chains", label: "Chains & RPC", glyph: "⬡" },
			{ id: "integrations", href: "/integrations", label: "Integrations", glyph: "⇄" },
			{ id: "settings", href: "/settings", label: "Settings", glyph: "⚙\uFE0E" },
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

interface Tip {
	label: string;
	top: number;
	left: number;
}

export function Sidebar({ active }: { active: string }) {
	const [collapsed, setCollapsed] = useState(false);
	const [tip, setTip] = useState<Tip | null>(null);

	useEffect(() => {
		let stored = false;
		try {
			stored = window.localStorage.getItem(STORAGE_KEY) === "1";
		} catch {
			stored = false;
		}
		setCollapsed(stored);
		document.body.classList.toggle("sidebar-collapsed", stored);
	}, []);

	function toggle() {
		const next = !collapsed;
		setCollapsed(next);
		setTip(null);
		document.body.classList.toggle("sidebar-collapsed", next);
		try {
			window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
		} catch {
			// A blocked storage API must not break navigation.
		}
	}

	function showTip(
		event: MouseEvent<HTMLAnchorElement> | FocusEvent<HTMLAnchorElement>,
		label: string,
	) {
		if (!collapsed) return;
		const rect = event.currentTarget.getBoundingClientRect();
		setTip({ label, top: Math.round(rect.top + rect.height / 2), left: Math.round(rect.right + 10) });
	}

	return (
		<nav className="sidebar" id="primary-navigation" aria-label="Primary navigation">
			<script dangerouslySetInnerHTML={{ __html: BOOTSTRAP }} />
			<button
				type="button"
				className="sidebar-toggle"
				onClick={toggle}
				aria-expanded={!collapsed}
				aria-controls="primary-navigation"
				aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
				title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
			>
				<span className="g" aria-hidden="true">
					{collapsed ? "»" : "«"}
				</span>
				<span className="t">Collapse</span>
			</button>
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
								title={item.label}
								aria-label={item.label}
								aria-current={on ? "page" : undefined}
								onMouseEnter={(event) => showTip(event, item.label)}
								onMouseLeave={() => setTip(null)}
								onFocus={(event) => showTip(event, item.label)}
								onBlur={() => setTip(null)}
								onClick={() => setTip(null)}
							>
								<span className="g" aria-hidden="true">
									{item.glyph}
								</span>
								<span className="t">{item.label}</span>
							</Link>
						);
					})}
				</div>
			))}
			{tip && (
				<span
					className="nav-tip"
					data-testid="nav-tooltip"
					aria-hidden="true"
					style={{ top: tip.top, left: tip.left }}
				>
					{tip.label}
				</span>
			)}
			<div className="sidebar-foot">
				<div className="micro">PAPER · no keys</div>
			</div>
		</nav>
	);
}
