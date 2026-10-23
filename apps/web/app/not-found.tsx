import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Panel } from "@/components/ui";

export default function NotFound() {
	return (
		<AppShell active="">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">404</div>
					<h1>Not found</h1>
					<p className="sub">
						This page or position was not found. Check the address or go back to the
						dashboard.
					</p>
				</div>
			</header>
			<Panel title="Nothing here" sub="404">
				<div className="row">
					<Link className="btn primary" href="/">
						Dashboard
					</Link>
					<Link className="btn" href="/states">
						State gallery
					</Link>
				</div>
			</Panel>
		</AppShell>
	);
}
