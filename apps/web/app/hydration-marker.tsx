"use client";

import { useEffect } from "react";

/**
 * Marks the document once React has hydrated the page.
 *
 * Server-rendered controls are visible before their client handlers exist. A click at
 * that moment can be swallowed by the router and do nothing, which makes browser tests
 * flaky on a loaded machine. The attribute gives tests one honest signal to wait for;
 * it changes no content and no behaviour.
 *
 * It marks the initial tree only. The shell streams - the top-bar kill switch arrives in
 * its own Suspense boundary with its own chunk - so a control inside a streamed boundary
 * can attach its listeners after this attribute appears. A test that needs one of those
 * controls must wait for that control, not for this marker.
 */
export function HydrationMarker() {
	useEffect(() => {
		document.documentElement.dataset.hydrated = "true";
	}, []);
	return null;
}
