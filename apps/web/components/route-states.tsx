import type { ReactNode } from "react";

function cx(...parts: Array<string | false | null | undefined>) {
	return parts.filter(Boolean).join(" ");
}

function Skeleton({ className }: { className?: string }) {
	return <span className={cx("skel", className)} aria-hidden="true" />;
}

function Loading({ label, testId, children }: { label: string; testId: string; children: ReactNode }) {
	return (
		<div
			className="route-loading"
			role="status"
			aria-live="polite"
			aria-busy="true"
			data-testid={testId}
		>
			<span className="sr-only">{label}</span>
			{children}
		</div>
	);
}

function Head({ actions = 2 }: { actions?: number }) {
	return (
		<header className="page-head" aria-hidden="true">
			<div className="titles" style={{ flex: "1 1 auto", width: "100%" }}>
				<Skeleton className="skel-crumb" />
				<Skeleton className="skel-title" />
				<Skeleton className="skel-sub" />
			</div>
			<div className="page-actions">
				{Array.from({ length: actions }, (_, index) => (
					<Skeleton className="skel-btn" key={index} />
				))}
			</div>
		</header>
	);
}

function Band({ stats = 5 }: { stats?: number }) {
	return (
		<div className="kpi-band mb-12" aria-hidden="true">
			{Array.from({ length: stats }, (_, index) => (
				<div className="stat" key={index}>
					<Skeleton className="skel-k" />
					<Skeleton className="skel-v" />
					<Skeleton className="skel-m" />
				</div>
			))}
		</div>
	);
}

function Rows({ rows = 6 }: { rows?: number }) {
	return (
		<div className="skeleton-rows" aria-hidden="true">
			{Array.from({ length: rows }, (_, index) => (
				<Skeleton className="skel-row" key={index} />
			))}
		</div>
	);
}

function TablePanel({ rows = 6 }: { rows?: number }) {
	return (
		<section className="panel" aria-hidden="true">
			<div className="panel-head">
				<Skeleton className="skel-h" />
				<Skeleton className="skel-badge" />
			</div>
			<div className="panel-body flush">
				<div className="skeleton-toolbar">
					<Skeleton className="skel-input" />
					<Skeleton className="skel-input sm" />
					<Skeleton className="skel-btn" />
				</div>
				<Rows rows={rows} />
			</div>
		</section>
	);
}

export function GenericSkeleton() {
	return (
		<Loading label="Loading page…" testId="route-loading">
			<Head />
			<Band stats={4} />
			<div className="grid cols-2">
				<section className="panel" aria-hidden="true">
					<div className="panel-head">
						<Skeleton className="skel-h" />
					</div>
					<div className="panel-body">
						<Skeleton className="skel-block" />
					</div>
				</section>
				<section className="panel" aria-hidden="true">
					<div className="panel-head">
						<Skeleton className="skel-h" />
					</div>
					<div className="panel-body">
						<Rows rows={4} />
					</div>
				</section>
			</div>
		</Loading>
	);
}

export function PositionsSkeleton() {
	return (
		<Loading label="Loading positions…" testId="positions-loading">
			<Head />
			<Band stats={5} />
			<TablePanel rows={7} />
			<div className="grid cols-2 mt-16" aria-hidden="true">
				<section className="panel">
					<div className="panel-head">
						<Skeleton className="skel-h" />
					</div>
					<div className="panel-body">
						<Rows rows={3} />
					</div>
				</section>
				<section className="panel">
					<div className="panel-head">
						<Skeleton className="skel-h" />
					</div>
					<div className="panel-body">
						<Skeleton className="skel-block" />
					</div>
				</section>
			</div>
		</Loading>
	);
}

export function MarketsSkeleton() {
	return (
		<Loading label="Loading markets…" testId="markets-loading">
			<Head />
			<div className="skeleton-tabs" aria-hidden="true">
				{Array.from({ length: 7 }, (_, index) => (
					<Skeleton className="skel-tab" key={index} />
				))}
			</div>
			<TablePanel rows={8} />
		</Loading>
	);
}

export function PerformanceSkeleton() {
	return (
		<Loading label="Loading performance…" testId="performance-loading">
			<Head />
			<Band stats={6} />
			<div className="split" aria-hidden="true">
				<section className="panel">
					<div className="panel-head">
						<Skeleton className="skel-h" />
					</div>
					<div className="panel-body">
						<Skeleton className="skel-chart" />
					</div>
				</section>
				<section className="panel">
					<div className="panel-head">
						<Skeleton className="skel-h" />
					</div>
					<div className="panel-body">
						<Rows rows={6} />
					</div>
				</section>
			</div>
		</Loading>
	);
}
