import Link from "next/link";
import type { ReactNode } from "react";
import type { Tone } from "@/lib/dashboard-fixtures";

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

export function Badge({ tone = "default", children }: { tone?: Tone; children: ReactNode }) {
	return <span className={cx("badge", tone !== "default" && tone)}>{children}</span>;
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
	variant?: "default" | "primary" | "ghost" | "danger";
	size?: "sm";
	block?: boolean;
}) {
	return (
		<Link
			className={cx("btn", variant !== "default" && variant, size, block && "block")}
			href={href}
		>
			{children}
		</Link>
	);
}
