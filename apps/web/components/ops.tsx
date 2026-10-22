import Link from "next/link";
import type { ReactNode } from "react";
import { Badge, Panel } from "@/components/ui";

/** Explicit source label used on every page that can render sample data. */
export function ModeBadge({ mode }: { mode: "fixture" | "live" }) {
	return (
		<Badge tone={mode === "fixture" ? "ghost" : "info"} title="Data source">
			{mode === "fixture" ? "FIXTURE" : "LIVE API"}
		</Badge>
	);
}

export function FixtureBadge() {
	return (
		<Badge tone="ghost" title="Sample data, not an API response">
			FIXTURE
		</Badge>
	);
}

/** A real error block: the API reason is shown unchanged, never replaced by sample rows. */
export function Unavailable({
	title,
	code,
	message,
	children,
	action,
}: {
	title: string;
	code?: string | null;
	message: string;
	children?: ReactNode;
	action?: ReactNode;
}) {
	return (
		<Panel
			title={title}
			action={code ? <Badge tone="err">{code}</Badge> : undefined}
			footer={
				<div className="between">
					<span>Reason codes and messages come from the API unchanged.</span>
					{action}
				</div>
			}
		>
			<div className="note err">{message}</div>
			{children && <div className="mt-8 small muted">{children}</div>}
		</Panel>
	);
}

export function EmptyState({
	title,
	children,
	links,
}: {
	title: string;
	children: ReactNode;
	links?: { href: string; label: string; primary?: boolean }[];
}) {
	return (
		<div>
			<div className="strong">{title}</div>
			<p className="small muted mt-8" style={{ maxWidth: "80ch" }}>
				{children}
			</p>
			{links && links.length > 0 && (
				<div className="row mt-12">
					{links.map((link) => (
						<Link
							key={link.href}
							className={`btn${link.primary ? " primary" : ""}`}
							href={link.href}
						>
							{link.label}
						</Link>
					))}
				</div>
			)}
		</div>
	);
}

/** A small read-only fact row. Values are strings the caller already formatted. */
export function Fact({ label, value, mono = false }: { label: string; value: ReactNode; mono?: boolean }) {
	return (
		<div className="between small" style={{ padding: "3px 0" }}>
			<span className="muted">{label}</span>
			<span className={mono ? "mono" : undefined}>{value}</span>
		</div>
	);
}
