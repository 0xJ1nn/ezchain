import { Badge } from "./ui";
import type { Quote } from "@/lib/api";
import { DASH, ageLabel, formatAmount, formatUsd, sourceLabel } from "@/lib/live-format";

/**
 * The full pre-trade cost sheet for one quote: one line each, with source and age,
 * so the trade is previewed — all of its known costs, and its unknowns as em
 * dashes — before the click. The values come from the quote's cost preview (the
 * API's, or the fixture model's in fixture mode); this component computes
 * nothing. Unknown is "—", never zero; the platform fee is a known zero.
 */
export function CostSheet({ quote, symbol }: { quote: Quote; symbol: string }) {
	const preview = quote.cost_preview;
	if (!preview) return null;
	const sourceAge = `${sourceLabel(preview.source.name)} · ${ageLabel(preview.source.observed_at)}`;
	const worstCase =
		preview.worst_case_out === null
			? DASH
			: quote.side === "buy"
				? `${formatAmount(preview.worst_case_out)} ${symbol}`
				: formatUsd(preview.worst_case_out);

	return (
		<section data-testid="cost-sheet" aria-label="Pre-trade cost sheet">
			<div className="row mb-8">
				<span className="micro">Pre-trade cost sheet</span>
				<span style={{ flex: 1 }} />
				<Badge tone="ghost">PAPER</Badge>
			</div>
			<table className="table compact mb-12">
				<tbody>
					<tr>
						<td className="muted">Platform fee</td>
						<td className="right">
							<span className="mono">0</span>
							<span className="sub"> self-hosted · {sourceAge}</span>
						</td>
					</tr>
					<tr>
						<td className="muted">Venue fee</td>
						<td className="right">
							<span className="mono">{formatUsd(preview.venue_fee_usd)}</span>
							<span className="sub">
								{" "}
								{quote.kind === "paper_mark" ? `paper mark · ${sourceAge}` : sourceAge}
							</span>
						</td>
					</tr>
					<tr>
						<td className="muted">Network / priority fee</td>
						<td className="right">
							<span className="mono">{DASH}</span>
							<span className="sub"> not reported by the venue</span>
						</td>
					</tr>
					<tr>
						<td className="muted">Estimated impact</td>
						<td className="right">
							<span className="mono">
								{preview.price_impact_pct ? `${preview.price_impact_pct}%` : DASH}
							</span>
							<span className="sub">
								{preview.price_impact_pct ? " venue reported" : " not reported"}
							</span>
						</td>
					</tr>
					<tr>
						<td className="muted">Slippage window</td>
						<td className="right">
							<span className="mono">
								{preview.slippage_bps === null
									? DASH
									: `±${(preview.slippage_bps / 100).toFixed(2)}%`}
							</span>
							<span className="sub">
								{preview.slippage_bps === null ? " no venue in P0" : " requested at quote"}
							</span>
						</td>
					</tr>
					<tr>
						<td className="muted">Receiver&apos;s worst case</td>
						<td className="right">
							<span className="mono">{worstCase}</span>
							<span className="sub">
								{preview.worst_case_out === null
									? " no venue slippage"
									: " minimum out inside the window"}
							</span>
						</td>
					</tr>
					<tr>
						<td className="muted">Fill fee (journal)</td>
						<td className="right">
							<span className="mono">{formatUsd(preview.fill_fee_usd)}</span>
							<span className="sub"> max(paper fee, venue fee) · preview = journal</span>
						</td>
					</tr>
				</tbody>
			</table>
		</section>
	);
}
