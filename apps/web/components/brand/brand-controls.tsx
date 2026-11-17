"use client";

import { useState } from "react";
import {
	Badge,
	Button,
	Chips,
	Delta,
	HoldButton,
	KillSwitch,
	ModePill,
	Panel,
	Segmented,
	Toggle,
} from "@/components/ui";

// The reference gallery renders the real primitives with fixture strings. The
// hold key confirms a local counter only; it never reaches an order path.
export function BrandControls() {
	const [segment, setSegment] = useState("one");
	const [chip, setChip] = useState("24h");
	const [confirmed, setConfirmed] = useState(0);

	return (
		<div data-testid="brand-controls">
		<Panel
			title="Brand controls"
			sub="plain fills, one border, no glow"
		>
			<div className="stack-sm">
				<div className="row" data-testid="brand-buttons">
					<Button variant="primary">Primary</Button>
					<Button>Default</Button>
					<Button variant="buy">Buy</Button>
					<Button variant="sell">Sell</Button>
					<Button variant="agent">Agent</Button>
					<Button variant="ghost">Ghost</Button>
					<Button variant="danger">Danger</Button>
					<Button disabled>Disabled</Button>
				</div>
				<div className="row" data-testid="brand-button-sizes">
					<Button size="sm">Small</Button>
					<Button size="md">Medium</Button>
					<Button size="lg">Large</Button>
					<Button size="xl">Extra large</Button>
				</div>
				<div className="row" data-testid="brand-hold">
					<HoldButton variant="buy" size="xl" onConfirm={() => setConfirmed((n) => n + 1)}>
						Hold to confirm
					</HoldButton>
					<span className="mono small" data-testid="brand-hold-count">
						Confirmed {confirmed}
					</span>
				</div>
				<div className="row" data-testid="brand-badges">
					<Badge>default</Badge>
					<Badge tone="ok">ok</Badge>
					<Badge tone="err">err</Badge>
					<Badge tone="warn">warn</Badge>
					<Badge tone="info">info</Badge>
					<span className="badge violet">violet</span>
				</div>
				<div className="row" data-testid="brand-modes">
					<ModePill mode="paper" />
					<ModePill mode="live" />
					<ModePill mode="unknown" />
					<ModePill mode="pending" />
				</div>
				<div className="row" data-testid="brand-kill">
					<KillSwitch>Off</KillSwitch>
					<KillSwitch armed>Armed</KillSwitch>
					<KillSwitch unknown>Unknown</KillSwitch>
					<KillSwitch pending>Pending</KillSwitch>
				</div>
				<div className="row" data-testid="brand-deltas">
					<Delta tone="up">+2.41%</Delta>
					<Delta tone="down">−0.84%</Delta>
					<Delta tone="flat">—</Delta>
					<Delta tone="up" pill>
						+7.36%
					</Delta>
				</div>
				<div className="row" data-testid="brand-fields">
					<div className="field">
						<input aria-label="Amount" placeholder="0.00" defaultValue="" />
						<span className="unit">USD</span>
					</div>
				</div>
				<div className="row" data-testid="brand-segmented">
					<Segmented
						options={[
							{ value: "one", label: "One" },
							{ value: "two", label: "Two" },
						]}
						value={segment}
						onChange={setSegment}
					/>
					<Segmented
						side
						options={[
							{ value: "buy", label: "Buy" },
							{ value: "sell", label: "Sell" },
						]}
						value="buy"
						onChange={() => {}}
					/>
				</div>
				<div className="row" data-testid="brand-chips">
					<Chips options={["5m", "1h", "6h", "24h"]} value={chip} onChange={setChip} />
				</div>
				<div className="row" data-testid="brand-toggles">
					<Toggle label="Off" />
					<Toggle label="On" defaultOn />
					<Toggle label="Disabled" disabled />
				</div>
				<div className="row" data-testid="brand-toasts">
					<div className="toast ok" role="status">
						<span className="ic" aria-hidden="true" />
						<div>
							<b>Result</b>
							<span>A result carries its reason.</span>
						</div>
						<span className="t">now</span>
					</div>
				</div>
			</div>
		</Panel>
		</div>
	);
}
