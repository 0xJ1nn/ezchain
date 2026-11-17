import Link from "next/link";
import type { ReactNode } from "react";
import type { Tone } from "@/lib/dashboard-fixtures";

export { useHold, HoldButton, ConfirmButton } from "./ui-hold";
export type { ButtonVariant, ButtonSize } from "./ui-hold";
export { Toggle } from "./ui-toggle";
import type { ButtonVariant, ButtonSize } from "./ui-hold";

function cx(...parts: Array<string | false | null | undefined>) {
	return parts.filter(Boolean).join(" ");
}

export function Panel({
	title,
	sub,
	action,
	children,
	flush = false,
	className,
	footer,
}: {
	title?: ReactNode;
	sub?: ReactNode;
	action?: ReactNode;
	children: ReactNode;
	flush?: boolean;
	className?: string;
	footer?: ReactNode;
}) {
	return (
		<section className={cx("panel", className)}>
			{(title || action) && (
				<div className="panel-head">
					{title && <h2>{title}</h2>}
					{sub && <span className="sub">{sub}</span>}
					{action && <div className="right">{action}</div>}
				</div>
			)}
			<div className={cx("panel-body", flush && "flush")}>{children}</div>
			{footer && <div className="panel-foot">{footer}</div>}
		</section>
	);
}

export function Badge({
	tone = "default",
	children,
	title,
}: {
	tone?: Tone;
	children: ReactNode;
	title?: string;
}) {
	return (
		<span className={cx("badge", tone !== "default" && tone)} title={title}>
			{children}
		</span>
	);
}

export function Stat({
	label,
	value,
	meta,
	hero = false,
	tone,
	title,
}: {
	label: ReactNode;
	value: ReactNode;
	meta?: ReactNode;
	hero?: boolean;
	tone?: "ok" | "err" | "warn";
	title?: string;
}) {
	return (
		<div className={cx("stat", hero && "hero-stat")}>
			<div className="label">{label}</div>
			<div className={cx("value", !hero && "sm", tone)} title={title}>
				{value}
			</div>
			{meta && <div className="meta">{meta}</div>}
		</div>
	);
}

export function Meter({
	width,
	tone,
	label,
	valueText,
}: {
	width: string;
	tone?: "ok" | "warn" | "err";
	label: string;
	valueText: string;
}) {
	return (
		<div
			className="meter"
			role="progressbar"
			aria-label={label}
			aria-valuetext={valueText}
			aria-valuemin={0}
			aria-valuemax={100}
			aria-valuenow={Number.parseInt(width, 10)}
		>
			<div className={cx("fill", tone)} style={{ width }} />
		</div>
	);
}

export function LiveDot({ tone = "live" }: { tone?: "live" | "stale" | "dead" }) {
	return <span className={cx("dot-live", tone !== "live" && tone)} />;
}

export function TokenAvatar({
	children,
	size,
}: {
	children: ReactNode;
	size?: "sm";
}) {
	return <span className={cx("token", size === "sm" && "sm")}>{children}</span>;
}

export function ButtonLink({
	href,
	children,
	variant = "default",
	size,
	block = false,
}: {
	href: string;
	children: ReactNode;
	variant?: ButtonVariant;
	size?: ButtonSize;
	block?: boolean;
}) {
	return (
		<Link
			className={cx(
				"btn",
				variant !== "default" && variant,
				size !== "md" && size,
				block && "block",
			)}
			href={href}
		>
			{children}
		</Link>
	);
}

export function Button({
	children,
	variant = "default",
	size = "md",
	block = false,
	onClick,
	disabled,
	title,
	type = "button",
}: {
	children: ReactNode;
	variant?: ButtonVariant;
	size?: ButtonSize;
	block?: boolean;
	onClick?: () => void;
	disabled?: boolean;
	title?: string;
	type?: "button" | "submit";
}) {
	return (
		<button
			type={type}
			className={cx(
				"btn",
				variant !== "default" && variant,
				size !== "md" && size,
				block && "block",
			)}
			onClick={onClick}
			disabled={disabled}
			title={title}
		>
			{children}
		</button>
	);
}

export function ModePill({
	mode,
	children,
}: {
	mode: "paper" | "live" | "unknown" | "pending";
	children?: ReactNode;
}) {
	return (
		<span className={cx("mode", mode)}>
			<span className="dot" aria-hidden="true" />
			{children ?? (mode === "paper" ? "PAPER" : mode === "live" ? "LIVE" : mode === "unknown" ? "UNKNOWN" : "PENDING")}
		</span>
	);
}

export function KillSwitch({
	armed,
	unknown = false,
	pending = false,
	children,
	onClick,
}: {
	armed?: boolean;
	unknown?: boolean;
	pending?: boolean;
	children: ReactNode;
	onClick?: () => void;
}) {
	return (
		<button
			type="button"
			className={cx("kill", armed && "armed", unknown && "unknown", pending && "pending")}
			onClick={onClick}
		>
			{children}
		</button>
	);
}

export function Delta({
	tone,
	children,
	pill = false,
}: {
	tone: "up" | "down" | "flat";
	children: ReactNode;
	pill?: boolean;
}) {
	return <span className={cx("delta", tone, pill && "pill")}>{children}</span>;
}

export function Segmented<T extends string>({
	options,
	value,
	onChange,
	side = false,
	block = false,
}: {
	options: ReadonlyArray<{ value: T; label: ReactNode }>;
	value: T;
	onChange: (value: T) => void;
	side?: boolean;
	block?: boolean;
}) {
	return (
		<div className={cx("seg", side && "side", block && "block")} role="group">
			{options.map((option) => (
				<button
					type="button"
					key={option.value}
					className={cx(
						value === option.value && (side ? (option.value === "buy" ? "on-buy" : "on-sell") : "on"),
					)}
					aria-pressed={value === option.value}
					onClick={() => onChange(option.value)}
				>
					{option.label}
				</button>
			))}
		</div>
	);
}

export function Chips({
	options,
	value,
	onChange,
}: {
	options: ReadonlyArray<string>;
	value?: string;
	onChange?: (value: string) => void;
}) {
	return (
		<div className="chips" role="group">
			{options.map((option) => (
				<button
					type="button"
					key={option}
					className={cx(value === option && "on")}
					aria-pressed={value === option}
					onClick={() => onChange?.(option)}
				>
					{option}
				</button>
			))}
		</div>
	);
}

export function Toast({
	tone = "ok",
	icon,
	title,
	children,
	time,
	action,
}: {
	tone?: "ok" | "win" | "ask" | "err";
	icon?: ReactNode;
	title: ReactNode;
	children: ReactNode;
	time?: string;
	action?: ReactNode;
}) {
	return (
		<div className={cx("toast", tone)} role="status">
			<span className="ic" aria-hidden="true">{icon}</span>
			<div>
				<b>{title}</b>
				<span>{children}</span>
			</div>
			{action ?? (time && <span className="t">{time}</span>)}
		</div>
	);
}
