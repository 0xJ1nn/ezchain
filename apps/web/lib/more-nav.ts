// The More page's link groups, with the reference galleries kept out of production.
//
// The galleries (`/states`, `/components`, `/mobile`) are design documentation and their
// routes only exist in the development build (see `referenceSurfacesEnabled`). Keeping the
// filter here, as a pure function, is what the browser test asserts: a production build
// cannot render a link to a route it does not serve.

export interface MoreGroup {
	title: string;
	links: [string, string][];
}

export const MORE_GROUPS: MoreGroup[] = [
	{
		title: "Discovery",
		links: [
			["/markets", "Browse markets"],
			["/signals", "Signals"],
		],
	},
	{
		title: "Overview",
		links: [["/performance", "Performance"]],
	},
	{
		title: "Trade",
		links: [
			["/wallets", "Wallets"],
			["/ledger", "Activity / ledger"],
			["/reports", "Reports"],
		],
	},
	{
		title: "Automate",
		links: [
			["/strategies", "Strategies"],
			["/backtests", "Backtests"],
		],
	},
	{
		title: "Protect",
		links: [
			["/risk", "Risk & kill switch"],
			["/alerts", "Alerts"],
		],
	},
	{
		title: "System",
		links: [
			["/chains", "Chains & RPC"],
			["/integrations", "Integrations"],
			["/security", "Security"],
			["/developer", "Developer"],
			["/operations", "Operations"],
			["/workspace", "Workspace"],
			["/settings", "Settings"],
		],
	},
	{
		title: "Reference",
		links: [
			["/states", "State gallery"],
			["/components", "Components"],
			["/mobile", "Mobile"],
		],
	},
];

export function moreGroups(referenceEnabled: boolean): MoreGroup[] {
	return referenceEnabled
		? MORE_GROUPS
		: MORE_GROUPS.filter((group) => group.title !== "Reference");
}
