import Link from "next/link";
import { AppShell } from "@/components/shell";
import { ModeBadge, Unavailable } from "@/components/ops";
import { Badge, ButtonLink, Panel } from "@/components/ui";
import { getTradePolicy, type PolicyField, type PolicyProfile } from "@/lib/trade-policy-api";
import { webMode } from "@/lib/runtime";
import { SizingPreviewForm, StrategyPolicyForm } from "./trade-policy-form";

export const dynamic = "force-dynamic";

function fieldRange(field: PolicyField): string {
	if (field.kind === "ratio") {
		return `ratio ${field.minimum}–${field.maximum} · 0.05 = 5%`;
	}
	return `${field.minimum}–${field.maximum} ${field.unit}`.trim();
}

function FieldRow({ field }: { field: PolicyField }) {
	return (
		<div className="panel" data-field={field.key} style={{ boxShadow: "none" }}>
			<div className="panel-body">
				<div className="between" style={{ gap: 12, alignItems: "flex-start" }}>
					<div className="grow">
						<div className="row">
							<span className="strong">{field.label}</span>
							<Badge tone="ghost">{field.unit || "ratio"}</Badge>
						</div>
						<p className="small muted mt-8" style={{ maxWidth: "70ch" }}>
							{field.description}
						</p>
						<div className="small muted mt-8 mono">{fieldRange(field)}</div>
					</div>
					<div className="mono strong" style={{ fontSize: 18 }}>
						{String(field.value)}
					</div>
				</div>
			</div>
		</div>
	);
}

function ProfileCard({ profile }: { profile: PolicyProfile }) {
	return (
		<div className="panel" data-profile={profile.name} style={{ boxShadow: "none" }}>
			<div className="panel-body">
				<div className="row">
					<span className="strong">{profile.label}</span>
					<Badge tone="ghost">{profile.name}</Badge>
				</div>
				<p className="small muted mt-8">{profile.description}</p>
				<div className="stack-sm mt-12">
					{profile.fields.map((field) => (
						<div className="between small" key={field.key} data-profile-field={field.key}>
							<span className="muted">{field.label}</span>
							<span className="mono">
								{String(field.value)} · {fieldRange(field)}
							</span>
						</div>
					))}
				</div>
			</div>
		</div>
	);
}

export default async function TradePolicyPage() {
	const mode = webMode();
	const result = await getTradePolicy();
	const document = result.ok ? result.data : null;

	return (
		<AppShell active="settings">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Settings / Trade policy</div>
					<h1>Trade policy</h1>
					<p className="sub">
						The sizing rules the order gate applies, shown with the value, range and
						description the API serves. A strategy may carry its own profile; null inherits the
						deployment default, and the server verdict is the only verdict.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/settings">
						Settings
					</Link>
					<ButtonLink href="/risk" size="sm">
						Risk
					</ButtonLink>
				</div>
			</header>

			{!result.ok && (
				<div className="mb-12">
					<Unavailable
						title="Trade policy unavailable"
						code={result.error.code}
						message={result.error.message}
						action={
							<Link className="small" href="/settings/trade-policy">
								Retry →
							</Link>
						}
					>
						The page renders only what the API returns. No limit is taken from the web bundle.
					</Unavailable>
				</div>
			)}

			<div className="grid cols-2">
				<Panel
					title="Your active policy"
					sub={document?.active_profile ? `profile ${document.active_profile}` : "no profile configured"}
					action={<Badge tone="ghost">GET /api/v1/trade-policy</Badge>}
					footer="These are the fields the order gate reads for the active profile. A strategy may override the profile; the fields themselves come from the server registry."
				>
					{document === null ? (
						<p className="small muted">The policy needs a document from the API.</p>
					) : document.fields.length === 0 ? (
						<p className="note">
							No deployment risk profile is configured. The deployment&apos;s own limits apply
							until a profile is set.
						</p>
					) : (
						<div className="stack-sm">
							{document.fields.map((field) => (
								<FieldRow field={field} key={field.key} />
							))}
						</div>
					)}
				</Panel>

				<Panel
					title="Per-strategy overrides"
					sub={`${document?.strategies.length ?? 0} strategies`}
					action={<Badge tone="ghost">PUT /api/v1/trade-policy/strategies/&lt;id&gt;</Badge>}
					footer="A profile change that loosens a fund-affecting limit needs one acknowledgement. The server refuses it as RISK_ACK_REQUIRED, so neither surface can skip the disclosure."
				>
					{document ? (
						<StrategyPolicyForm initial={document} fixture={mode === "fixture"} />
					) : (
						<p className="small muted">The override list needs a policy document from the API.</p>
					)}
				</Panel>
			</div>

			<div className="grid cols-2 mt-16">
				<Panel title="Risk profiles" sub="one registry">
					<div className="stack-sm">
						{(document?.profiles ?? []).map((profile) => (
							<ProfileCard profile={profile} key={profile.name} />
						))}
					</div>
				</Panel>

				<Panel
					title="Sizing preview"
					sub="server verdict"
					action={<Badge tone="ghost">GET /api/v1/trade-policy/preview</Badge>}
				>
					<SizingPreviewForm
						activeProfile={document?.active_profile ?? null}
						profiles={document?.profiles ?? []}
						fixture={mode === "fixture"}
					/>
				</Panel>
			</div>

			<Panel title="Where this comes from" sub="one home" className="mt-16">
				<p className="small muted">
					The order gate and this page read the same policy. Changing a profile changes which
					minimum, maximum, percent-of-equity, per-token and slippage bounds apply; it does not
					create a second resolver or a browser-side limit.{" "}
					<Link href="/settings">Account settings</Link> hold the deployment defaults, and the{" "}
					<Link href="/risk">risk page</Link> renders the same numbers read-only.
				</p>
			</Panel>
		</AppShell>
	);
}
