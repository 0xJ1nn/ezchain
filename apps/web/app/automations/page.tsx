import { redirect } from "next/navigation";

// Automations and strategies are one surface: a strategy is the only scheduled object this
// build stores, so the scheduler view is a tab of /strategies. The old path stays a real
// route and forwards, so bookmarks and prose links keep working.
export default function LegacyAutomationsPage() {
	redirect("/strategies?view=automations");
}
