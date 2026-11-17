"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

function cx(...parts: Array<string | false | null | undefined>) {
	return parts.filter(Boolean).join(" ");
}

// Port of ezHold from the brand board. The fill grows over `ms` while the key is
// held; releasing early drains it and confirms nothing. The default is 650 ms.
export function useHold(ms = 650, onDone?: () => void) {
	const [holding, setHolding] = useState(false);
	const [done, setDone] = useState(false);
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const reset = useRef<ReturnType<typeof setTimeout> | null>(null);
	const callback = useRef(onDone);
	callback.current = onDone;

	const stop = useCallback(() => {
		if (timer.current) clearTimeout(timer.current);
		timer.current = null;
		setHolding(false);
	}, []);

	useEffect(() => {
		return () => {
			if (timer.current) clearTimeout(timer.current);
			if (reset.current) clearTimeout(reset.current);
		};
	}, []);

	const start = useCallback(
		(event: ReactPointerEvent<HTMLButtonElement>) => {
			const button = event.currentTarget;
			if (button.disabled) return;
			button.setPointerCapture?.(event.pointerId);
			setHolding(true);
			timer.current = setTimeout(() => {
				timer.current = null;
				setHolding(false);
				setDone(true);
				callback.current?.();
				reset.current = setTimeout(() => setDone(false), 1600);
			}, ms);
		},
		[ms],
	);

	return {
		holding,
		done,
		bind: {
			onPointerDown: start,
			onPointerUp: stop,
			onPointerCancel: stop,
			onPointerLeave: stop,
		},
	};
}

export type ButtonVariant =
	| "default"
	| "primary"
	| "buy"
	| "sell"
	| "agent"
	| "ghost"
	| "danger";

export type ButtonSize = "sm" | "md" | "lg" | "xl";

function classes(
	variant: ButtonVariant,
	size: ButtonSize,
	block: boolean,
	extra?: string,
) {
	return cx(
		"btn",
		variant !== "default" && variant,
		size !== "md" && size,
		block && "block",
		extra,
	);
}

export function HoldButton({
	children,
	sub,
	variant = "buy",
	size = "xl",
	block = false,
	disabled = false,
	ms = 650,
	onConfirm,
	className,
}: {
	children: ReactNode;
	sub?: ReactNode;
	variant?: ButtonVariant;
	size?: ButtonSize;
	block?: boolean;
	disabled?: boolean;
	ms?: number;
	onConfirm?: () => void;
	className?: string;
}) {
	const { holding, done, bind } = useHold(ms, onConfirm);
	return (
		<button
			type="button"
			disabled={disabled}
			className={classes(variant, size, block, cx(holding && "holding", done && "done", className))}
			style={{ "--hold": `${ms}ms` } as React.CSSProperties}
			{...bind}
		>
			<i className="fill" aria-hidden="true" />
			{done ? (
				<>
					<svg className="check" viewBox="0 0 24 24" aria-hidden="true">
						<path
							d="m5 12.5 4.5 4.5L19 7.5"
							fill="none"
							stroke="currentColor"
							strokeWidth="2.4"
							strokeLinecap="round"
							strokeLinejoin="round"
						/>
					</svg>
					Filled
				</>
			) : (
				<>
					<b>{children}</b>
					{sub && <span className="sub">{sub}</span>}
				</>
			)}
		</button>
	);
}

// First press arms a four-second drain; a second press inside it acts.
export function ConfirmButton({
	children,
	onConfirm,
	variant = "danger",
	size = "lg",
	block = false,
	disabled = false,
}: {
	children: ReactNode;
	onConfirm: () => void;
	variant?: ButtonVariant;
	size?: ButtonSize;
	block?: boolean;
	disabled?: boolean;
}) {
	const [armed, setArmed] = useState(false);
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

	useEffect(() => {
		return () => {
			if (timer.current) clearTimeout(timer.current);
		};
	}, []);

	function click() {
		if (armed) {
			if (timer.current) clearTimeout(timer.current);
			timer.current = null;
			setArmed(false);
			onConfirm();
			return;
		}
		setArmed(true);
		timer.current = setTimeout(() => {
			timer.current = null;
			setArmed(false);
		}, 4000);
	}

	return (
		<button
			type="button"
			disabled={disabled}
			className={classes(variant, size, block, armed ? "armed" : undefined)}
			onClick={click}
		>
			{armed ? "Press again to confirm · 4 s" : children}
		</button>
	);
}
