"use client";

import { useState } from "react";

// Port of the board's .toggle control. A value supplied by the caller is
// controlled; without one the control keeps its own state, so a static gallery
// entry behaves like the real setting it documents.
export function Toggle({
	on,
	defaultOn = false,
	onChange,
	disabled = false,
	label,
	className,
}: {
	on?: boolean;
	defaultOn?: boolean;
	onChange?: (next: boolean) => void;
	disabled?: boolean;
	label: string;
	className?: string;
}) {
	const [internal, setInternal] = useState(defaultOn);
	const active = on ?? internal;
	return (
		<button
			type="button"
			role="switch"
			aria-checked={active}
			aria-label={label}
			disabled={disabled}
			className={["toggle", active && "on", className].filter(Boolean).join(" ")}
			onClick={() => {
				if (on === undefined) setInternal(!active);
				onChange?.(!active);
			}}
		/>
	);
}
