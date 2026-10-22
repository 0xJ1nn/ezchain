"use client";

import { useState } from "react";

export interface CsvColumn {
	key: string;
	label: string;
}

function cell(value: string | null | undefined): string {
	const text = value ?? "";
	return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function CsvExport({
	dataset,
	columns,
	rows,
	fetchedAt,
}: {
	dataset: string;
	columns: CsvColumn[];
	rows: Record<string, string | null>[];
	fetchedAt: string;
}) {
	const [status, setStatus] = useState<string | null>(null);

	function download() {
		const header = columns.map((column) => cell(column.label)).join(",");
		const body = rows
			.map((row) => columns.map((column) => cell(row[column.key])).join(","))
			.join("\r\n");
		const csv = body.length > 0 ? `${header}\r\n${body}\r\n` : `${header}\r\n`;
		const stamp = new Date().toISOString().replace(/[:.]/g, "-");
		const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const anchor = document.createElement("a");
		anchor.href = url;
		anchor.download = `ezchain-paper-${dataset}-${stamp}.csv`;
		document.body.append(anchor);
		anchor.click();
		anchor.remove();
		setTimeout(() => URL.revokeObjectURL(url), 0);
		setStatus(
			`Exported ${rows.length} row${rows.length === 1 ? "" : "s"} · fetched ${fetchedAt}`,
		);
	}

	return (
		<div>
			<button
				type="button"
				className="btn"
				onClick={download}
				aria-label={`Export ${dataset} CSV`}
			>
				Export {dataset} CSV
			</button>
			{status && (
				<div className="micro mt-8" role="status">
					{status}
				</div>
			)}
		</div>
	);
}
