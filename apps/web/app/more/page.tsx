import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Panel } from "@/components/ui";
import { moreGroups } from "@/lib/more-nav";
import { referenceSurfacesEnabled } from "@/lib/runtime";

export default function MorePage() {
	const fixture = process.env.EZCHAIN_WEB_MODE === "fixture";
	// The reference galleries are design documentation, so they are listed only where
	// their routes exist: the development (fixture) build. Production keeps the same
	// page without them.
	const reference = referenceSurfacesEnabled();
	const sections = moreGroups(reference);
	return (
		<AppShell active="more">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">More</div>
					<h1>More</h1>
					<p className="sub">
						Browse and Signals are the primary discovery surfaces and stay one tap away in
						the bottom nav. Performance, Automate, Trade, Protect and System live here under
						More.
						{reference
							? " Reference galleries are design documentation, not production surfaces."
							: ""}
					</p>
				</div>
			</header>

			<div className="grid cols-2">
				{sections.map((group) => (
					<Panel title={group.title} key={group.title}>
						<div className="list">
							{group.links.map(([href, label]) => (
								<Link className="list-row" href={href} key={href}>
									<span className="grow">
										<span className="title">{label}</span>
									</span>
									<span className="muted" aria-hidden="true">
										→
									</span>
								</Link>
							))}
						</div>
					</Panel>
				))}
			</div>

			<Panel title="Chain scope & data source" sub="set on the page that owns it" className="mt-16">
				<div className="row">
					<Link className="btn" href="/markets">
						Browse markets by chain
					</Link>
					<span className="source chip" title="Market data source">
						{fixture ? "⟳ fixture data" : "⟳ live · dexscreener"}
					</span>
					<span className="badge">PAPER</span>
				</div>
				<p className="note mt-12">
					Chain scope is a page filter, not a shell setting. Markets, Launches and
					Positions each expose their own chain control, carried in that page&apos;s URL.
					This page does not filter any of them.
				</p>
			</Panel>
		</AppShell>
	);
}
