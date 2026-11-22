import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Fact, ModeBadge, Unavailable } from "@/components/ops";
import { Badge, Panel } from "@/components/ui";
import { webMode } from "@/lib/runtime";
import { getBuildSource } from "./build-source-api";

export const dynamic = "force-dynamic";

const DOCS = "https://github.com/0xJ1nn/ezchain/blob/main";

export default async function GeneralSettingsPage() {
	const mode = webMode();
	const result = await getBuildSource();
	const official = result.ok && result.data.official;

	return (
		<AppShell active="settings">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Settings / General</div>
					<h1>General</h1>
					<p className="sub">
						How this installation identifies itself. The badge appears only when the running
						build&apos;s signature verifies against the published release key; every other case
						states its reason instead.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/settings">
						Settings
					</Link>
				</div>
			</header>

			<div className="grid cols-2">
				<Panel
					title="About this build"
					sub="signed provenance"
					action={
						official ? (
							<Badge tone="ok">Official build</Badge>
						) : (
							<Badge tone="warn">Unverified build</Badge>
						)
					}
					footer="The API checks the attestation on disk. A fork, a source checkout or a missing release key cannot produce a positive result."
				>
					{!result.ok ? (
						<Unavailable
							title="Build source unavailable"
							code={result.error.code}
							message={result.error.message}
							action={
								<Link className="small" href="/settings/general">
									Retry →
								</Link>
							}
						>
							The page renders only the API verdict. It never asserts a build is genuine on its
							own.
						</Unavailable>
					) : (
						<div className="stack-sm">
							<Fact
								label="Build source"
								value={
									official ? (
										<Badge tone="ok">Official build</Badge>
									) : (
										<Badge tone="warn">Unverified build</Badge>
									)
								}
							/>
							<Fact label="Why" value={<span className="small">{result.data.message}</span>} />
							<Fact label="Check" value={<span className="mono">{result.data.status}</span>} />
							{result.data.tag && (
								<Fact label="Release" value={<span className="mono">{result.data.tag}</span>} />
							)}
						</div>
					)}
				</Panel>

				<Panel
					title="Official channels"
					sub="how to tell a copy apart"
					footer="A fork that is distributed or hosted needs its own name and logo. The name and logo are not covered by the code licence."
				>
					<div className="stack-sm">
						<Fact
							label="Source"
							value={
								<a
									className="mono"
									href="https://github.com/0xJ1nn/ezchain"
									rel="noreferrer"
								>
									github.com/0xJ1nn/ezchain
								</a>
							}
						/>
						<Fact label="Release assets" value={<span className="small">tagged releases in that repository</span>} />
						<Fact
							label="Signature steps"
							value={
								<a className="small" href={`${DOCS}/docs/RELEASES.md`} rel="noreferrer">
									docs/RELEASES.md ↗
								</a>
							}
						/>
						<Fact
							label="Trademark rules"
							value={
								<a className="small" href={`${DOCS}/TRADEMARKS.md`} rel="noreferrer">
									TRADEMARKS.md ↗
								</a>
							}
						/>
					</div>
					<p className="note mt-12">
						No other domain, download site or hosted service is an official channel. Verify a
						download against the published release key before running it.
					</p>
				</Panel>
			</div>

			<Panel title="What the badge does not say" sub="honest boundary" className="mt-16">
				<p className="small muted">
					The verdict covers the build&apos;s own attestation, not the behaviour of the software or
					the safety of a strategy. It stays absent when the signature cannot be checked, and it
					never turns a paper deployment into a live one.
				</p>
			</Panel>
		</AppShell>
	);
}
