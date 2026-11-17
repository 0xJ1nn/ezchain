import type { CSSProperties, ReactNode } from "react";

// Port of the board's EZ_CHAINS, ezTok and EZ_TOKEN_LOGOS. The icon files are the
// exported brand assets in public/icons; no geometry is drawn here. A chain that is
// not in the map gets no badge and no guessed icon — the caller's own label is shown.
type ChainDef = { name: string; icon: string };

const CHAINS: Record<string, ChainDef> = {
	solana: { name: "Solana", icon: "solana" },
	ethereum: { name: "Ethereum", icon: "ethereum" },
	base: { name: "Base", icon: "base" },
	bnb: { name: "BNB Chain", icon: "bnb" },
	arbitrum: { name: "Arbitrum", icon: "arbitrum" },
	polygon: { name: "Polygon", icon: "polygon" },
	avalanche: { name: "Avalanche", icon: "avalanche" },
	optimism: { name: "Optimism", icon: "optimism" },
	tron: { name: "Tron", icon: "tron" },
	sui: { name: "Sui", icon: "sui" },
	ton: { name: "TON", icon: "ton" },
	bitcoin: { name: "Bitcoin", icon: "bitcoin" },
};

// Payloads carry either the canonical chain id or the display name. Both resolve to
// the same exported mark, so callers never have to normalise chain identity first.
const CHAIN_ALIASES: Record<string, string> = {
	sol: "solana", solana: "solana",
	eth: "ethereum", ethereum: "ethereum",
	base: "base",
	bnb: "bnb", bsc: "bnb", "bnb chain": "bnb",
	arb: "arbitrum", arbitrum: "arbitrum",
	poly: "polygon", polygon: "polygon",
	avax: "avalanche", avalanche: "avalanche",
	optimism: "optimism",
	tron: "tron",
	sui: "sui",
	ton: "ton",
	btc: "bitcoin", bitcoin: "bitcoin",
};

export function chainInfo(chain?: string | null): ChainDef | null {
	if (!chain) return null;
	const id = CHAIN_ALIASES[chain.trim().toLowerCase()];
	return id ? CHAINS[id] : null;
}

export const TOKEN_LOGOS = [
	"BTC", "ETH", "SOL", "BNB", "USDC", "USDT", "XRP", "LINK", "AVAX", "DOGE",
] as const;

function hasTokenLogo(symbol: string): boolean {
	return (TOKEN_LOGOS as readonly string[]).includes(symbol.toUpperCase());
}

function cx(...parts: Array<string | false | null | undefined>) {
	return parts.filter(Boolean).join(" ");
}

export type CoinSize = "sm" | "md" | "lg";

export function Coin({
	symbol,
	name,
	chain,
	size = "md",
	color,
	className,
}: {
	symbol: string;
	name?: string;
	chain?: string | null;
	size?: CoinSize;
	color?: string;
	className?: string;
}) {
	const info = chainInfo(chain);
	const logo = hasTokenLogo(symbol);
	const face = logo ? (
		// eslint-disable-next-line @next/next/no-img-element -- exported brand mark, not an app image
		<img src={`/icons/tokens/${symbol.toUpperCase()}.svg`} alt="" />
	) : (
		symbol.charAt(0).toUpperCase()
	);
	const title = `${name ?? symbol}${info ? ` on ${info.name}` : ""}`;
	const style = { "--c": color ?? "var(--text-2)" } as CSSProperties;
	const badgeSize = size === "lg" ? 18 : size === "sm" ? 10 : 14;

	return (
		<span
			className={cx("tok", size !== "md" && size, className)}
			style={style}
			title={title}
			data-testid="coin"
			data-chain={chain ?? ""}
		>
			<span className={cx("coin", size !== "md" && size, logo && "img")} style={style}>
				{face}
			</span>
			{info && (
				<span className="chb">
					{/* eslint-disable-next-line @next/next/no-img-element -- exported brand mark */}
					<img
						className="chimg"
						src={`/icons/chains/${info.icon}.svg`}
						width={badgeSize}
						height={badgeSize}
						alt=""
					/>
				</span>
			)}
		</span>
	);
}

// Identity text used by tables and tiles that only need the coin plus its label,
// without the asset stack below.
export function CoinIdentity({
	symbol,
	name,
	chain,
	size = "sm",
	color,
	children,
}: {
	symbol: string;
	name: string;
	chain?: string | null;
	size?: CoinSize;
	color?: string;
	children?: ReactNode;
}) {
	return (
		<span className="row" data-testid="coin-identity">
			<Coin symbol={symbol} name={name} chain={chain} size={size} color={color} />
			<span>
				<span className="strong">{symbol}</span>
				<span className="sub" style={{ display: "block" }}>
					{children ?? name}
				</span>
			</span>
		</span>
	);
}
