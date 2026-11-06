"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "ezchain.theme";

type Theme = "dark" | "light";

export function ThemeToggle() {
	const [theme, setTheme] = useState<Theme>("dark");

	useEffect(() => {
		let current: Theme = "dark";
		try {
			current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
		} catch {
			current = "dark";
		}
		setTheme(current);
	}, []);

	function toggle() {
		const next: Theme = theme === "dark" ? "light" : "dark";
		setTheme(next);
		try {
			document.documentElement.dataset.theme = next;
		} catch {
			// A blocked DOM must not break the control.
		}
		try {
			window.localStorage.setItem(STORAGE_KEY, next);
		} catch {
			// A blocked storage API must not break navigation.
		}
	}

	return (
		<button
			type="button"
			className="icon-btn"
			data-testid="theme-toggle"
			aria-pressed={theme === "dark"}
			aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
			title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
			onClick={toggle}
		>
			<span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
		</button>
	);
}
