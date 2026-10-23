"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

export default function Error({
	error,
	reset,
}: {
	error: Error & { digest?: string };
	reset: () => void;
}) {
	const heading = useRef<HTMLHeadingElement>(null);

	useEffect(() => {
		heading.current?.focus();
	}, [error]);

	return (
		<main className="route-error" aria-labelledby="route-error-title">
			<section className="panel route-error-card">
				<div className="crumbs">System / Render error</div>
				<h1 id="route-error-title" ref={heading} tabIndex={-1}>
					Something went wrong
				</h1>
				<p className="sub">
					This surface could not be rendered. No order was placed and no ledger entry
					changed.
				</p>
				<div className="route-error-reason">
					<span className="badge err">Reason</span>
					<code className="mono">{error.digest ?? "RENDER_FAILED"}</code>
				</div>
				<p className="note">
					{error.message || "The render failed without a message."} Retrying calls the
					server again; if it keeps failing, open the dashboard or the market browser.
				</p>
				<div className="row">
					<button type="button" className="btn primary" onClick={() => reset()}>
						Try again
					</button>
					<Link className="btn" href="/">
						Dashboard
					</Link>
					<Link className="btn" href="/markets">
						Browse markets
					</Link>
					<Link className="btn" href="/signals">
						Signals
					</Link>
				</div>
			</section>
		</main>
	);
}
