"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./trade.module.css";

const HOLD_MS = 650;

/**
 * A trade key that sends only after a deliberate hold. A tap, an early release, a
 * pointer leaving the key or a released keyboard key cancels before the callback runs.
 * The fill band is decoration; under reduced motion the stylesheet drops its movement
 * while the hold requirement stays.
 */
export function HoldButton({
	label,
	side,
	disabled = false,
	onConfirm,
	testId = "hold-submit",
}: {
	label: string;
	side: "buy" | "sell";
	disabled?: boolean;
	onConfirm: () => void | Promise<void>;
	testId?: string;
}) {
	const [holding, setHolding] = useState(false);
	const timer = useRef<number | null>(null);
	const armed = useRef(false);

	function clear() {
		if (timer.current !== null) {
			window.clearTimeout(timer.current);
			timer.current = null;
		}
		armed.current = false;
		setHolding(false);
	}

	useEffect(() => clear, []);

	function start() {
		if (disabled || armed.current || timer.current !== null) return;
		armed.current = true;
		setHolding(true);
		timer.current = window.setTimeout(() => {
			timer.current = null;
			armed.current = false;
			setHolding(false);
			void onConfirm();
		}, HOLD_MS);
	}

	return (
		<button
			type="button"
			className={`btn xl block ${styles.holdKey} ${side}${holding ? ` ${styles.holding}` : ""}`}
			data-testid={testId}
			aria-label={label}
			disabled={disabled}
			onPointerDown={(event) => {
				event.preventDefault();
				start();
			}}
			onPointerUp={clear}
			onPointerLeave={clear}
			onPointerCancel={clear}
			onKeyDown={(event) => {
				if (event.repeat) return;
				if (event.key === " " || event.key === "Enter") {
					event.preventDefault();
					start();
				}
			}}
			onKeyUp={(event) => {
				if (event.key === " " || event.key === "Enter") clear();
			}}
			onBlur={clear}
		>
			<span className={styles.holdFill} aria-hidden="true" />
			<span className={styles.holdLabel}>{label}</span>
		</button>
	);
}
