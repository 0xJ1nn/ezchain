import { Suspense, type ReactNode } from "react";
import { TopbarKillSwitch } from "@/app/risk/kill-switch";
import { getKillSwitch } from "@/lib/paper-api";
import { getExecutionMode } from "@/lib/system-api";
import { SideNav } from "./sidenav";
import {
	AccountMenu,
	AgentButton,
	DonateButton,
	HamburgerButton,
	ShellFrame,
	ShellProvider,
} from "./shell-state";
import { Ion } from "./ion";

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

// Top bar over the main area and the agent panel: menu, search, mode, kill switch,
// Agent, a Donate slot (the disclaimer field fills its popover) and the account menu.
export function Topbar() {
	return (
		<header className="top">
			<HamburgerButton />
			<label className="field search">
				<Ion name="search" size={18} />
				<input
					type="search"
					name="q"
					maxLength={80}
					placeholder="Search any asset, address or command"
					aria-label="Search any asset, address or command"
				/>
				<span className="kbd">⌘K</span>
			</label>
			<span className="spacer" />
			<Suspense fallback={<ModeChipFallback />}>
				<TopbarModeChip />
			</Suspense>
			<Suspense
				fallback={
					<button type="button" className="btn sm" disabled>
						Kill switch
					</button>
				}
			>
				<TopbarKillSwitchState />
			</Suspense>
			<AgentButton />
			<DonateButton />
			<AccountMenu />
		</header>
	);
}

export function AppShell({ active, children }: { active: string; children: ReactNode }) {
	const fixture = process.env.EZCHAIN_WEB_MODE === "fixture";
	return (
		<ShellProvider>
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
			<ShellFrame
				topbar={<Topbar />}
				nav={<SideNav active={active} />}
				panel={
					<aside className="panel" id="agent-panel" aria-label="Agent panel" />
				}
				active={active}
			>
				<main id="main">{children}</main>
			</ShellFrame>
		</ShellProvider>
	);
}
