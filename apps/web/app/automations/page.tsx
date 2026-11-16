import { redirect } from "next/navigation";

// Automations and strategies are one surface: a strategy is the only scheduled object this
// build stores, so the scheduler view is a tab of /strategies. The old path stays a real
// route and forwards, so bookmarks and prose links keep working. A second page here would
// be a duplicate surface with its own URL state, which docs/WIREFRAME.md rules out and
// `reference-nav.spec.ts` pins.
export default async function LegacyAutomationsPage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string }>;
}) {
	const { state } = await searchParams;
	// A fixture state demo travels with the redirect, so `/automations?state=empty` still
	// reaches the empty scheduler view.
	const query = state
		? `?view=automations&state=${encodeURIComponent(state)}`
		: "?view=automations";
	redirect(`/strategies${query}`);
}
