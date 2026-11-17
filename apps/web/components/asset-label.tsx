import type { ReactNode } from "react";
import { chainInfo, Coin, type CoinSize } from "./coin";

// Port of the board's ezWhere and ezAsset. The second line reads SYM · chain chip,
// or SYM · venue for an exchange market. A bridged token adds an amber badge.
// An unknown chain label is shown as text rather than guessed into an icon.
function cx(...parts: Array<string | false | null | undefined>) {
	return parts.filter(Boolean).join(" ");
}

export function AssetWhere({
	symbol,
	chain,
	venue,
	bridged,
	extra,
}: {
	symbol: string;
	chain?: string | null;
	venue?: string | null;
	bridged?: string | null;
	extra?: ReactNode;
}) {
	const info = chainInfo(chain);
	const where = info ? (
		<span className="chain">
			{/* eslint-disable-next-line @next/next/no-img-element -- exported brand mark */}
			<img className="chimg" src={`/icons/chains/${info.icon}.svg`} width={12} height={12} alt="" />
			{info.name}
		</span>
	) : venue ? (
		<span className="venue">{venue}</span>
	) : chain ? (
		<span className="chain">{chain}</span>
	) : null;

	return (
		<span className="where" data-testid="asset-where">
			{symbol}
			{where && " · "}
			{where}
			{bridged && <span className="badge amber bridged">Bridged · {bridged}</span>}
			{extra}
		</span>
	);
}

export function AssetLabel({
	symbol,
	name,
	chain,
	venue,
	bridged,
	size = "md",
	color,
	extra,
	className,
}: {
	symbol: string;
	name: string;
	chain?: string | null;
	venue?: string | null;
	bridged?: string | null;
	size?: CoinSize;
	color?: string;
	extra?: ReactNode;
	className?: string;
}) {
	return (
		<span className={cx("asset", className)} data-testid="asset-label">
			<Coin symbol={symbol} name={name} chain={chain} size={size} color={color} />
			<span>
				<b className="nm">{name}</b>
				<AssetWhere symbol={symbol} chain={chain} venue={venue} bridged={bridged} extra={extra} />
			</span>
		</span>
	);
}
