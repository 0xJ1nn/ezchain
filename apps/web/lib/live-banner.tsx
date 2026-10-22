import { Badge } from "@/components/ui";
import { isLiveMode } from "./api";
import { ageLabel, sourceLabel } from "./live-format";

/**
 * Page-level data-source banner. The shell banner is shared and lead-owned; this row is
 * the page's own claim about where its numbers came from. Live says LIVE, fixture says
 * FIXTURE — there is no in-between state.
 */
export function ModeBanner({
	source,
	observedAt,
	stale = false,
	detail,
}: {
	source?: string | null;
	observedAt?: string | null;
	stale?: boolean;
	detail?: string;
}) {
	const live = isLiveMode();
	const src = sourceLabel(source);
	const observed = observedAt ? `observed ${ageLabel(observedAt)} ago` : "observation age unknown";
	return (
		<div className="row mb-12" role="status" aria-label="Data mode">
			<Badge tone={live ? "ok" : "ghost"}>{live ? `LIVE · ${src}` : "FIXTURE"}</Badge>
			{stale && <Badge tone="warn">STALE</Badge>}
			<span className="small muted">
				{live
					? `${src} · ${observed}${stale ? " · the API marked this snapshot stale" : ""}`
					: "local sample data · no API request · not a live feed"}
			</span>
			{detail && <span className="small muted">· {detail}</span>}
		</div>
	);
}
