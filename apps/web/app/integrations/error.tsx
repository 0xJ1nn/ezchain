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
				<div className="crumbs">System / Integrations</div>
				<h1 id="route-error-title" ref={heading} tabIndex={-1}>
					Integrations failed to render
				</h1>
				<p className="sub">
					This view failed to render. Nothing was retried automatically.
				</p>
				<div className="route-error-reason">
					<span className="badge err">Reason</span>
					<code className="mono">{error.digest ?? "RENDER_FAILED"}</code>
				</div>
				<p className="note">
					Check Chains before retrying, then try again. No provider is listed without a
					real capability row.
				</p>
				<div className="row">
					<button type="button" className="btn primary" onClick={() => reset()}>
						Try again
					</button>
					<Link className="btn" href="/">
						Dashboard
					</Link>
					<Link className="btn" href="/chains">
						Chains
					</Link>
				</div>
			</section>
		</main>
	);
}
