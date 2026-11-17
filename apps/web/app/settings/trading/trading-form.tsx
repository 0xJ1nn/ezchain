"use client";

import {
	DEFAULT_PREFS,
	PRIORITY_OPTIONS,
	slippageLabel,
	SLIPPAGE_OPTIONS,
	useTradingPrefsEditor,
} from "@/lib/trading-prefs";

export function TradingForm() {
	const { prefs, update, reset } = useTradingPrefsEditor();

	function setPreset(index: number, raw: string) {
		const value = Number(raw);
		if (!Number.isFinite(value) || value <= 0) return;
		const next = [...prefs.presets];
		next[index] = Math.round(value * 100) / 100;
		update({ ...prefs, presets: next });
	}

	function removePreset(index: number) {
		if (prefs.presets.length <= 1) return;
		update({ ...prefs, presets: prefs.presets.filter((_, i) => i !== index) });
	}

	function addPreset() {
		if (prefs.presets.length >= 8) return;
		const last = prefs.presets[prefs.presets.length - 1] ?? 0;
		update({ ...prefs, presets: [...prefs.presets, Math.round((last * 2) * 100) / 100] });
	}

	return (
		<div data-testid="trading-form">
			<div className="stack-sm">
				<div className="between">
					<span className="strong">Quick-buy amounts (USD)</span>
					<span className="small muted mono">{prefs.presets.length} / 8</span>
				</div>
				<div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
					{prefs.presets.map((value, index) => (
						<div className="row" key={index} style={{ gap: 6 }}>
							<input
								className="input mono"
								style={{ width: 96 }}
								inputMode="decimal"
								aria-label={`Quick-buy amount ${index + 1}`}
								data-testid={`preset-input-${index}`}
								value={value}
								onChange={(event) => setPreset(index, event.target.value)}
							/>
							<button
								type="button"
								className="btn sm"
								aria-label={`Remove quick-buy amount ${index + 1}`}
								data-testid={`preset-remove-${index}`}
								disabled={prefs.presets.length <= 1}
								onClick={() => removePreset(index)}
							>
								×
							</button>
						</div>
					))}
					<button
						type="button"
						className="btn sm"
						data-testid="preset-add"
						disabled={prefs.presets.length >= 8}
						onClick={addPreset}
					>
						Add
					</button>
				</div>
				<p className="small muted">
					Each chip sets the ticket amount to that many USD. The defaults are{" "}
					{DEFAULT_PREFS.presets.map((p) => `$${p}`).join(", ")}.
				</p>
			</div>

			<div className="stack-sm mt-16">
				<label className="small">
					<span className="muted">Slippage</span>
					<select
						className="select"
						aria-label="Default slippage"
						data-testid="settings-slippage"
						value={prefs.slippage}
						onChange={(event) => update({ ...prefs, slippage: event.target.value })}
					>
						{SLIPPAGE_OPTIONS.map((option) => (
							<option key={option} value={option}>{slippageLabel(option)}</option>
						))}
					</select>
				</label>
				<label className="small">
					<span className="muted">Priority fee</span>
					<select
						className="select"
						aria-label="Default priority fee"
						data-testid="settings-priority"
						value={prefs.priority}
						onChange={(event) => update({ ...prefs, priority: event.target.value })}
					>
						{PRIORITY_OPTIONS.map((option) => (
							<option key={option.id} value={option.id}>{option.label}</option>
						))}
					</select>
				</label>
				<p className="small muted">
					Paper fills use the mark and ignore both values. A real venue applies them after
					the live gate.
				</p>
			</div>

			<div className="row mt-16">
				<button type="button" className="btn sm" data-testid="trading-reset" onClick={reset}>
					Restore defaults
				</button>
				<span className="small muted" data-testid="trading-saved">
					Saved in this browser
				</span>
			</div>
		</div>
	);
}
