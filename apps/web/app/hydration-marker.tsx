"use client";

import { useEffect } from "react";

/**
 * Marks the document once React has hydrated the page.
 *
 * Server-rendered controls are visible before their client handlers exist. A click at
 * that moment can be swallowed by the router and do nothing, which makes browser tests
 * flaky on a loaded machine. The attribute gives tests one honest signal to wait for;
 * it changes no content and no behaviour.
 */
export function HydrationMarker() {
	useEffect(() => {
		document.documentElement.dataset.hydrated = "true";
	}, []);
	return null;
}
