"use client";

import Link from "next/link";
import { ButtonLink, Badge } from "@/components/ui";
import { useKillSwitchState } from "@/app/risk/kill-switch";
import {
	KILL_SWITCH_LEVELS,
	KILL_SWITCH_MANUAL_ORDER_NOTE,
	killSwitchLevels,
} from "@/lib/kill-switch-copy";
import type { KillSwitchState } from "@/lib/paper-api";

// The dashboard's switch card. One persisted flag governs two levels, so the card names
// both and says what each gates instead of printing a single "Risk" verdict that reads as
// the whole risk picture. The state is the API's answer: the server snapshot first, then
// whatever any control publishes, so the topbar and this card cannot disagree after a
// mutation. Without a state it says unknown and promises nothing.
export function DashboardKillSwitchCard({
	initial,
	initialError,
}: {
	initial: KillSwitchState | null;
	initialError: string | null;
}) {
	const state = useKillSwitchState(initial);
	const armed = state?.active === true;
	const levels = killSwitchLevels(state?.scopes);

	return (
		<div className="stack-sm" data-testid="dashboard-kill-switch">
			<div className="list-row">
				{state ? (
					<Badge tone={armed ? "err" : "ok"}>{armed ? "ARMED" : "RELEASED"}</Badge>
				) : (
					<Badge tone="warn">STATE UNKNOWN</Badge>
				)}
				<span className="grow">
					<span className="title">Kill switch</span>
					<span className="sub">
						one persisted flag ·{" "}
						{levels.length > 0
							? `${levels.join(" and ")} levels`
							: "levels not reported by the API"}
					</span>
				</span>
				<ButtonLink href="/risk" size="sm">
					Open
				</ButtonLink>
			</div>

			{state ? (
				levels.length > 0 ? (
					levels.map((level) => (
						<div
							className="between small"
							key={level}
							data-testid={`kill-switch-level-${level}`}
						>
							<span>
								<span className="strong">{KILL_SWITCH_LEVELS[level].label}</span>{" "}
								<span className="muted">{KILL_SWITCH_LEVELS[level].gates}</span>
							</span>
							<span className={armed ? "err mono" : "mono"}>
								{armed ? "BLOCKS" : "OPEN"}
							</span>
						</div>
					))
				) : (
					<div className="note">
						The API returned the flag without its levels, so this card names neither.
					</div>
				)
			) : (
				<div className="note">
					{initialError ?? "The API did not return the persisted switch."}
				</div>
			)}

			<p className="small muted">
				{KILL_SWITCH_MANUAL_ORDER_NOTE} <Link href="/risk">What it does not do →</Link>
			</p>
		</div>
	);
}
