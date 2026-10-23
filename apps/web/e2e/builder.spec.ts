import { expect, test, type Page } from "@playwright/test";
import { fromDocument, toDocument, type GraphDocument } from "../lib/graph-doc";
import { validateGraphDocument } from "../lib/graph-fixtures";

// The visual strategy builder (docs/STRATEGY-GRAPH.md). Fixture mode: the catalogue,
// validation, dry run and backtest are local and labelled FIXTURE; the save path always
// posts to /api/v1/strategies and is intercepted here.

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

const DOCUMENT: GraphDocument = {
	schema: "ezchain.graph/1",
	nodes: [
		{ id: "tick", type: "event.tick", params: {}, ui: { x: 0, y: 0 } },
		{ id: "c1", type: "data.candles", params: { timeframe: "5m" }, ui: { x: 0, y: 160 } },
		{ id: "fast", type: "ind.ema", params: { period: 9 }, ui: { x: 280, y: 120 } },
		{ id: "slow", type: "ind.ema", params: { period: 21 }, ui: { x: 280, y: 300 } },
		{ id: "x", type: "logic.cross_above", params: {}, ui: { x: 560, y: 200 } },
		{ id: "br", type: "flow.branch", params: {}, ui: { x: 840, y: 60 } },
		{ id: "buy", type: "act.buy", params: { amount_usd: "100" }, ui: { x: 1120, y: 40 } },
	],
	edges: [
		{ from: { node: "tick", port: "out" }, to: { node: "br", port: "in" } },
		{ from: { node: "c1", port: "close" }, to: { node: "fast", port: "series" } },
		{ from: { node: "c1", port: "close" }, to: { node: "slow", port: "series" } },
		{ from: { node: "fast", port: "series" }, to: { node: "x", port: "a" } },
		{ from: { node: "slow", port: "series" }, to: { node: "x", port: "b" } },
		{ from: { node: "x", port: "out" }, to: { node: "br", port: "cond" } },
		{ from: { node: "br", port: "true" }, to: { node: "buy", port: "in" } },
	],
	comments: [{ id: "k1", text: "Entry", ui: { x: -40, y: -80, w: 400, h: 300 } }],
};

const TOKEN = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263";

function nodeLocator(page: Page, id: string) {
	return page.locator(`[data-node-id="${id}"]`);
}

function handle(page: Page, id: string, port: string, direction: "source" | "target") {
	return page.locator(
		`.react-flow__handle.${direction}[data-nodeid="${id}"][data-handleid="${port}"]`,
	);
}

async function addNode(page: Page, type: string) {
	await page.getByTestId(`palette-${type}`).click();
	await expect(nodeLocator(page, type.split(".").pop() as string)).toBeVisible();
}

// `Home` animates the camera; handle boxes read mid-flight are stale, so settle first.
async function fitView(page: Page) {
	await page.keyboard.press("Home");
	await page.waitForTimeout(500);
}

async function connect(
	page: Page,
	from: { node: string; port: string },
	to: { node: string; port: string },
) {
	const source = handle(page, from.node, from.port, "source");
	const target = handle(page, to.node, to.port, "target");
	const sourceBox = await source.boundingBox();
	const targetBox = await target.boundingBox();
	expect(sourceBox, `source handle ${from.node}.${from.port}`).not.toBeNull();
	expect(targetBox, `target handle ${to.node}.${to.port}`).not.toBeNull();
	await page.mouse.move(sourceBox!.x + sourceBox!.width / 2, sourceBox!.y + sourceBox!.height / 2);
	await page.mouse.down();
	await page.mouse.move(targetBox!.x + targetBox!.width / 2, targetBox!.y + targetBox!.height / 2, {
		steps: 14,
	});
	await page.mouse.up();
}

async function fillHeader(page: Page) {
	await page.locator("#graph-name").fill("BONK cross");
	await page.locator("#graph-token").fill(TOKEN);
	await page.locator("#graph-symbol").fill("bonk");
	await page.locator("#graph-interval").fill("15");
	await page.locator("#graph-max").fill("250");
}

async function pasteDocument(page: Page, document: GraphDocument) {
	await page.getByTestId("paste-json").click();
	await page.getByTestId("json-text").fill(JSON.stringify(document, null, 2));
	await page.getByTestId("apply-paste").click();
	await expect(page.getByTestId("json-text")).toHaveCount(0);
}

test("the document model round-trips the §1 shape exactly", () => {
	const again = toDocument(fromDocument(DOCUMENT));
	expect(again).toEqual(DOCUMENT);
	expect(Object.keys(again)).toEqual(["schema", "nodes", "edges", "comments"]);
	expect(Object.keys(again.nodes[0])).toEqual(["id", "type", "params", "ui"]);
	expect(Object.keys(again.edges[0])).toEqual(["from", "to"]);
	expect(Object.keys(again.comments[0])).toEqual(["id", "text", "ui"]);
});

test("the local compiler reports the §6 error codes", () => {
	const broken: GraphDocument = {
		schema: "ezchain.graph/1",
		nodes: [
			{ id: "tick", type: "event.tick", params: {}, ui: { x: 0, y: 0 } },
			{ id: "br1", type: "flow.branch", params: {}, ui: { x: 0, y: 0 } },
			{ id: "br2", type: "flow.branch", params: {}, ui: { x: 0, y: 0 } },
			{ id: "buy", type: "act.buy", params: { amount_usd: "10" }, ui: { x: 0, y: 0 } },
			{ id: "tp", type: "guard.take_profit", params: { pct: 20 }, ui: { x: 0, y: 0 } },
			{ id: "ema", type: "ind.ema", params: { period: 0 }, ui: { x: 0, y: 0 } },
			{ id: "sum", type: "math.add", params: {}, ui: { x: 0, y: 0 } },
		],
		edges: [
			// exec fan-out: one exec output, two wires
			{ from: { node: "tick", port: "out" }, to: { node: "br1", port: "in" } },
			{ from: { node: "tick", port: "out" }, to: { node: "br2", port: "in" } },
			// two wires into one input
			{ from: { node: "br1", port: "true" }, to: { node: "buy", port: "in" } },
			{ from: { node: "br2", port: "true" }, to: { node: "buy", port: "in" } },
			// bool output into a series input
			{ from: { node: "tp", port: "hit" }, to: { node: "ema", port: "series" } },
			{ from: { node: "tp", port: "hit" }, to: { node: "br1", port: "cond" } },
		] as GraphDocument["edges"],
		comments: [],
	};
	const report = validateGraphDocument(broken);
	const codes = new Set(report.errors.map((issue) => issue.code));
	expect(report.ok).toBe(false);
	for (const code of [
		"EXEC_FANOUT",
		"INPUT_MULTIPLE",
		"PIN_TYPE_MISMATCH",
		"INPUT_REQUIRED",
		"PARAM_INVALID",
	]) {
		expect(codes, `expected ${code} in ${[...codes].join(", ")}`).toContain(code);
	}
	expect(report.warmup_bars["5m"]).toBeUndefined();
});

test("the strategies page links into the builder", async ({ page }) => {
	await page.goto("/strategies");
	await page.getByRole("link", { name: "New visual strategy" }).click();
	await expect(page.getByRole("heading", { name: "Visual strategy builder", level: 1 })).toBeVisible();
	await expect(page.getByTestId("graph-builder")).toBeVisible();
});

test("palette lists every catalogue category and node type", async ({ page }) => {
	await page.goto("/strategies/builder");
	await expect(page.getByRole("heading", { name: "Visual strategy builder", level: 1 })).toBeVisible();
	await expect(page.locator(".page-actions .badge", { hasText: "FIXTURE" })).toBeVisible();

	for (const category of [
		"events",
		"market",
		"indicators",
		"state",
		"math",
		"flow",
		"actions",
		"guards",
	]) {
		await expect(page.getByTestId(`palette-cat-${category}`)).toBeVisible();
	}
	await expect(page.getByTestId("palette-event.tick")).toBeVisible();
	await expect(page.getByTestId("palette-guard.trailing_stop")).toBeVisible();
	await expect(page.getByText("46 node types")).toBeVisible();

	// The canvas furniture the brief requires.
	await expect(page.locator(".react-flow__minimap")).toBeVisible();
	await expect(page.locator(".react-flow__controls")).toBeVisible();
	await expect(page.locator(".react-flow__background")).toBeVisible();
});

test("a palette node can be dragged onto the canvas", async ({ page }) => {
	await page.goto("/strategies/builder");
	const canvas = page.getByLabel("Strategy graph canvas");
	const box = await canvas.boundingBox();

	// HTML5 drag and drop: the palette sets application/ezchain-node on dragstart.
	await page.evaluate(
		([x, y]) => {
			const source = document.querySelector('[data-testid="palette-data.price"]') as HTMLElement;
			const target = document.querySelector('[aria-label="Strategy graph canvas"]') as HTMLElement;
			const transfer = new DataTransfer();
			source.dispatchEvent(
				new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: transfer }),
			);
			target.dispatchEvent(
				new DragEvent("dragover", {
					bubbles: true,
					cancelable: true,
					dataTransfer: transfer,
					clientX: x as number,
					clientY: y as number,
				}),
			);
			target.dispatchEvent(
				new DragEvent("drop", {
					bubbles: true,
					cancelable: true,
					dataTransfer: transfer,
					clientX: x as number,
					clientY: y as number,
				}),
			);
		},
		[box!.x + 220, box!.y + 220],
	);

	await expect(nodeLocator(page, "price")).toBeVisible();
	await expect(nodeLocator(page, "price")).toContainText("data.price");
});

test("search adds a node and a clicked node opens its details", async ({ page }) => {
	await page.goto("/strategies/builder");
	const search = page.getByLabel("Search the node catalogue");
	await search.fill("ema");
	await expect(page.getByTestId("palette-ind.ema")).toBeVisible();
	await expect(page.getByTestId("palette-data.price")).toHaveCount(0);
	await search.press("Enter");

	const ema = nodeLocator(page, "ema");
	await expect(ema).toBeVisible();
	await expect(ema).toContainText("EMA");

	// Details dock reflects the selection and shows the parameter with its range.
	await ema.click();
	const details = page.getByTestId("details-node");
	await expect(details).toContainText("Exponential moving average");
	await expect(details).toContainText("range 1 … 500");
	await expect(details).toContainText("Inputs");
	await expect(details).toContainText("Outputs");
});

test("a valid wire is accepted and bool → series is rejected", async ({ page }) => {
	await page.goto("/strategies/builder");
	await addNode(page, "guard.take_profit");
	await addNode(page, "logic.not");
	await addNode(page, "data.candles");
	await addNode(page, "ind.ema");
	await fitView(page);

	// bool → bool is legal.
	await connect(page, { node: "take_profit", port: "hit" }, { node: "not", port: "a" });
	await expect(page.locator(".react-flow__edge")).toHaveCount(1);

	// bool → series is not: the wire is refused, so no edge is created.
	await connect(page, { node: "take_profit", port: "hit" }, { node: "ema", port: "series" });
	await expect(page.locator(".react-flow__edge")).toHaveCount(1);

	// series → number is the one implicit cast and is accepted.
	await connect(page, { node: "candles", port: "close" }, { node: "ema", port: "series" });
	await expect(page.locator(".react-flow__edge")).toHaveCount(2);
});

test("dragging a wire to empty canvas opens a type-filtered node menu", async ({ page }) => {
	await page.goto("/strategies/builder");
	await addNode(page, "guard.take_profit");

	const source = handle(page, "take_profit", "hit", "source");
	const box = await source.boundingBox();
	await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
	await page.mouse.down();
	await page.mouse.move(box!.x + 320, box!.y + 160, { steps: 12 });
	await page.mouse.up();

	const menu = page.getByTestId("node-menu");
	await expect(menu).toBeVisible();
	await expect(page.getByTestId("node-menu-filter")).toContainText("bool");
	await expect(page.getByTestId("menu-item-logic.not")).toBeVisible();
	await expect(page.getByTestId("menu-item-ind.ema")).toHaveCount(0);

	// Picking a node creates it and connects it in one gesture.
	await page.getByTestId("menu-item-logic.not").click();
	await expect(menu).toHaveCount(0);
	await expect(nodeLocator(page, "not")).toBeVisible();
	await expect(page.locator(".react-flow__edge")).toHaveCount(1);
});

test("F7 reports NO_ACTION and clears once a buy is wired to the event", async ({ page }) => {
	await page.goto("/strategies/builder");
	await page.keyboard.press("F7");

	const problems = page.getByTestId("problems");
	await expect(problems).toContainText("NO_ACTION");
	await expect(problems).toContainText("NO_EVENT");

	await addNode(page, "event.tick");
	await page.keyboard.press("F7");
	await expect(page.getByTestId("problem-NO_EVENT")).toHaveCount(0);
	await expect(page.getByTestId("problem-NO_ACTION")).toBeVisible();

	await addNode(page, "act.buy");
	await page.locator("#buy-act\\.buy-amount_usd").fill("100");
	await fitView(page);
	await connect(page, { node: "tick", port: "out" }, { node: "buy", port: "in" });
	await page.keyboard.press("F7");

	await expect(page.getByTestId("problem-NO_ACTION")).toHaveCount(0);
	// The buy is reachable, so the only remaining note is the (non-blocking) NO_EXIT warning.
	await expect(problems).toContainText("0 error(s)");
	await expect(page.getByTestId("warning-NO_EXIT")).toBeVisible();
});

test("C wraps the selection in a labelled group box", async ({ page }) => {
	await page.goto("/strategies/builder");
	await addNode(page, "event.tick");
	await nodeLocator(page, "tick").click();
	await page.keyboard.press("c");

	await expect(page.getByTestId("comment-node")).toHaveCount(1);
	await expect(page.getByTestId("details-comment")).toBeVisible();
	await page.locator("#group-label").fill("Entry lane");
	await expect(page.getByTestId("comment-node")).toContainText("Entry lane");
});

test("save posts the exact §1 document and refuses an invalid graph", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await fillHeader(page);

	// An invalid graph never reaches the API.
	await page.locator("#graph-token").fill("");
	await page.getByTestId("save").click();
	await expect(page.getByRole("status")).toContainText("Not saved");
	await page.locator("#graph-token").fill(TOKEN);

	let posted: Record<string, unknown> | null = null;
	await page.route("**/api/v1/strategies", async (route) => {
		if (route.request().method() !== "POST") {
			await route.continue();
			return;
		}
		posted = route.request().postDataJSON() as Record<string, unknown>;
		await route.fulfill({
			status: 201,
			contentType: "application/json",
			body: JSON.stringify({
				id: "str-new-graph",
				name: "BONK cross",
				chain: "solana",
				token_address: TOKEN,
				token_symbol: "BONK",
				kind: "graph",
				params: posted.params,
				status: "active",
				last_run_at: null,
				created_at: "2026-10-22T09:41:00Z",
			}),
		});
	});

	await page.getByTestId("save").click();
	await expect(page.getByRole("status")).toContainText("Saved");
	expect(posted, "the save must POST a strategy").not.toBeNull();

	const body = posted as unknown as Record<string, unknown>;
	expect(body.kind).toBe("graph");
	expect(body.name).toBe("BONK cross");
	expect(body.chain).toBe("solana");
	expect(body.token_address).toBe(TOKEN);
	expect(body.token_symbol).toBe("BONK");

	const params = body.params as Record<string, unknown>;
	expect(params.interval_minutes).toBe(15);
	expect(params.max_position_usd).toBe("250");

	const graph = params.graph as GraphDocument;
	expect(Object.keys(graph)).toEqual(["schema", "nodes", "edges", "comments"]);
	expect(graph).toEqual(DOCUMENT);
	expect(Object.keys(graph.nodes[0])).toEqual(["id", "type", "params", "ui"]);
	expect(Object.keys(graph.edges[0])).toEqual(["from", "to"]);
});

test("dry run shows a decision, effects and last-value chips", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await page.locator("#graph-token").fill(TOKEN);

	await page.getByTestId("dry-run").click();
	const result = page.getByTestId("dry-run-result");
	await expect(result).toBeVisible();
	await expect(result).toContainText("DRY_RUN");
	await expect(result).toContainText("FIXTURE");
	await expect(result).toContainText("buy");
	await expect(result).toContainText("Exec path");

	// Trace chips land on the output pins, "—" for an unknown value.
	const chips = page.locator('[data-testid^="chip-"]');
	await expect(chips.first()).toBeVisible();
	expect(await chips.count()).toBeGreaterThan(0);
	await expect(page.getByTestId("chip-c1-open")).toBeVisible();
});

test("backtest tab renders metrics, equity SVG, trades and node hits", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await page.locator("#graph-token").fill(TOKEN);

	await page.getByTestId("backtest").click();
	await expect(page.getByTestId("backtest-result")).toBeVisible();
	await expect(page.getByTestId("backtest-result")).toContainText("BACKTEST");
	await expect(page.getByTestId("equity-chart")).toBeVisible();
	await expect(page.getByTestId("equity-chart").locator("polyline")).toHaveCount(2);
	await expect(page.getByTestId("backtest-result")).toContainText("Max drawdown");
	await expect(page.getByTestId("backtest-result")).toContainText("Profit factor");
	await expect(page.getByTestId("backtest-result").locator("table").first().locator("tbody tr")).toHaveCount(3);
});

test("copy puts the §1 document on the clipboard", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await page.getByTestId("copy-json").click();
	await expect(page.getByTestId("json-text")).toBeVisible();
	const text = await page.evaluate(() => navigator.clipboard.readText());
	expect(JSON.parse(text)).toEqual(DOCUMENT);
	await page.getByRole("button", { name: "Close", exact: true }).click();
});

test("editing a graph strategy loads params.graph, its trace and patches on save", async ({
	page,
}) => {
	await page.goto("/strategies/str-bonk-graph/builder");
	await expect(page.getByRole("heading", { name: "Edit graph strategy", level: 1 })).toBeVisible();
	await expect(nodeLocator(page, "cross")).toBeVisible();
	await expect(nodeLocator(page, "sell")).toBeVisible();
	await expect(page.getByTestId("comment-node")).toContainText("Entry: EMA(9/21) cross above");

	// The newest run's graph_trace supplies the chips.
	await expect(page.getByTestId("chip-candles-open")).toBeVisible();

	let patched: Record<string, unknown> | null = null;
	await page.route("**/api/v1/strategies/str-bonk-graph", async (route) => {
		if (route.request().method() !== "PATCH") {
			await route.continue();
			return;
		}
		patched = route.request().postDataJSON() as Record<string, unknown>;
		await route.fulfill({
			status: 200,
			contentType: "application/json",
			body: JSON.stringify({
				id: "str-bonk-graph",
				name: "BONK EMA cross",
				chain: "solana",
				token_address: TOKEN,
				token_symbol: "BONK",
				kind: "graph",
				params: (patched as Record<string, unknown>).params,
				status: "active",
				last_run_at: null,
				created_at: "2026-10-20T11:00:00Z",
			}),
		});
	});

	await page.getByTestId("save").click();
	await expect(page.getByRole("status")).toContainText("Saved");
	expect(patched, "editing must PATCH the strategy").not.toBeNull();
	const graph = (patched as unknown as Record<string, unknown>).params as Record<string, unknown>;
	expect((graph.graph as GraphDocument).schema).toBe("ezchain.graph/1");
	expect(((graph.graph as GraphDocument).nodes ?? []).length).toBe(11);
});

test("undo, redo and delete keep the canvas consistent", async ({ page }) => {
	await page.goto("/strategies/builder");
	await addNode(page, "event.tick");
	await nodeLocator(page, "tick").click();

	await page.keyboard.press("Delete");
	await expect(nodeLocator(page, "tick")).toHaveCount(0);

	await page.keyboard.press("Control+z");
	await expect(nodeLocator(page, "tick")).toBeVisible();

	await page.keyboard.press("Control+Shift+z");
	await expect(nodeLocator(page, "tick")).toHaveCount(0);
});

test("Space and right-click open the unfiltered node menu", async ({ page }) => {
	await page.goto("/strategies/builder");
	const canvas = page.getByLabel("Strategy graph canvas");
	const box = await canvas.boundingBox();

	await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
	await page.keyboard.press(" ");
	const menu = page.getByTestId("node-menu");
	await expect(menu).toBeVisible();
	await expect(page.getByTestId("node-menu-filter")).toContainText("All nodes");
	await expect(page.getByTestId("menu-item-ind.ema")).toBeVisible();

	await page.keyboard.press("Escape");
	await expect(menu).toHaveCount(0);

	await page.mouse.click(box!.x + 140, box!.y + 140, { button: "right" });
	await expect(menu).toBeVisible();
});

test("a cyclic document is an error and is never saved", async ({ page }) => {
	await page.goto("/strategies/builder");
	const cyclic: GraphDocument = {
		schema: "ezchain.graph/1",
		nodes: [
			{ id: "tick", type: "event.tick", params: {}, ui: { x: 0, y: 0 } },
			{ id: "buy", type: "act.buy", params: { amount_usd: "10" }, ui: { x: 260, y: 0 } },
			{ id: "ema1", type: "ind.ema", params: { period: 9 }, ui: { x: 0, y: 200 } },
			{ id: "ema2", type: "ind.ema", params: { period: 21 }, ui: { x: 260, y: 200 } },
		],
		edges: [
			{ from: { node: "tick", port: "out" }, to: { node: "buy", port: "in" } },
			{ from: { node: "ema1", port: "series" }, to: { node: "ema2", port: "series" } },
			{ from: { node: "ema2", port: "series" }, to: { node: "ema1", port: "series" } },
		],
		comments: [],
	};
	await pasteDocument(page, cyclic);
	await fillHeader(page);
	await page.keyboard.press("F7");

	await expect(page.getByTestId("problem-CYCLE")).toBeVisible();

	let posted = 0;
	await page.route("**/api/v1/strategies", async (route) => {
		posted += 1;
		await route.fulfill({ status: 500, body: "{}" });
	});
	await page.getByTestId("save").click();
	await expect(page.getByRole("status")).toContainText("Not saved");
	expect(posted, "an invalid graph must never reach the API").toBe(0);
});

test("a 422 STRATEGY_GRAPH_INVALID report lands in the Problems panel", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await fillHeader(page);

	await page.route("**/api/v1/strategies", async (route) => {
		await route.fulfill({
			status: 422,
			contentType: "application/json",
			body: JSON.stringify({
				detail: {
					code: "STRATEGY_GRAPH_INVALID",
					message: "the graph failed server validation",
					report: {
						ok: false,
						errors: [
							{
								code: "PORT_UNKNOWN",
								message: "ind.ema has no output \"hist_value\".",
								node: "fast",
								port: "hist_value",
							},
						],
						warnings: [],
						warmup_bars: { "5m": 30 },
					},
				},
			}),
		});
	});

	await page.getByTestId("save").click();
	await expect(page.getByRole("status")).toContainText("STRATEGY_GRAPH_INVALID");
	await expect(page.getByTestId("problem-PORT_UNKNOWN")).toBeVisible();
	await expect(page.getByTestId("save-state")).toContainText("server validation");
	await expect(nodeLocator(page, "fast")).toHaveClass(/nodeError/);
});

test("a non-graph strategy explains itself instead of opening a canvas", async ({ page }) => {
	await page.goto("/strategies/str-bonk-momentum/builder");
	await expect(page.getByTestId("builder-not-graph")).toContainText(
		"not a visual graph strategy",
	);
});

test("builder has no external requests", async ({ page }) => {
	const external: string[] = [];
	page.on("request", (request) => {
		const url = new URL(request.url());
		if (url.protocol === "http:" || url.protocol === "https:") {
			if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
				external.push(request.url());
			}
		}
	});
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await expect(nodeLocator(page, "buy")).toBeVisible();
	expect(external, `unexpected external requests: ${external.join(", ")}`).toEqual([]);
});

test("builder screenshots at 1440 and the narrow notice at 390", async ({ page }) => {
	await page.goto("/strategies/builder");
	await pasteDocument(page, DOCUMENT);
	await fitView(page);
	await page.screenshot({ path: ".shots/builder-1440.png", fullPage: false });

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(page.getByTestId("builder-narrow")).toContainText("The builder needs a wider screen");
	await expect(page.getByTestId("builder-narrow")).toContainText("7");
	const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
	expect(scrollWidth, "page must not scroll sideways at 390px").toBeLessThanOrEqual(390);
	await page.screenshot({ path: ".shots/builder-390.png", fullPage: true });
});
