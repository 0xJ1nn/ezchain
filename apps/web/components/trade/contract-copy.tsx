"use client";

import { useEffect, useState } from "react";
import { shortAddress } from "@/lib/live-format";
import styles from "./trade.module.css";

/** The board's contract chip: a shortened address that copies the full one. */
export function ContractCopy({ address }: { address: string }) {
	const [copied, setCopied] = useState(false);

	useEffect(() => {
		if (!copied) return;
		const timer = setTimeout(() => setCopied(false), 1200);
		return () => clearTimeout(timer);
	}, [copied]);

	return (
		<button
			type="button"
			className={styles.contractCopy}
			title={copied ? "Copied" : "Copy the contract address"}
			aria-label={copied ? "Contract address copied" : `Copy contract address ${address}`}
			data-testid="contract-copy"
			onClick={() => {
				void navigator.clipboard?.writeText(address).then(
					() => setCopied(true),
					() => setCopied(false),
				);
			}}
		>
			{shortAddress(address)}
			<svg aria-hidden="true" viewBox="0 0 24 24" width="13" height="13">
				<use href="/icons/ion.svg#copy" />
			</svg>
		</button>
	);
}
