import Link from "next/link";
import type { TimeframeOption } from "@/lib/candle-view";
import type { CandleTimeframe } from "@/lib/api";
import styles from "./candle-timeframes.module.css";

/**
 * The candle panel's timeframe switch. Each option is a real link, so the series is
 * requested by the server for that timeframe and the choice survives a reload. The
 * active option is named with `aria-current`, not by colour alone.
 */
export function CandleTimeframes({
	options,
	active,
	hrefFor,
}: {
	options: readonly TimeframeOption[];
	active: CandleTimeframe;
	hrefFor: (timeframe: CandleTimeframe) => string;
}) {
	return (
		<nav className={styles.timeframes} aria-label="Candle timeframe">
			{options.map((option) => (
				<Link
					key={option.value}
					className={`${styles.timeframe}${
						option.value === active ? ` ${styles.timeframeActive}` : ""
					}`}
					href={hrefFor(option.value)}
					aria-current={option.value === active ? "page" : undefined}
				>
					{option.label}
				</Link>
			))}
		</nav>
	);
}
