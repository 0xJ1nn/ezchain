# RESEARCH — strategy platform and agent surface (2026)

Date: 2026-10-23. Scope: external strategy plugins, ML packages, paper/live separation,
and agent integration for ezCHAIN. This note records the evidence behind
[ADR-0028](DECISIONS.md#adr-0028--strategy-plugins-run-in-a-supervised-worker-agents-use-mcp),
[STRATEGY-SDK.md](STRATEGY-SDK.md) and [AGENT-INTEGRATION.md](AGENT-INTEGRATION.md).
Competitor star counts are the GitHub API values on this date and are context, not a
quality claim.

## 1. What a trader actually wants

The repeated job-to-be-done across trader forums, bot documentation and the competitor
feature sets is not "more indicators". It is **a trustworthy loop from idea to evidence to
execution**:

| Job | What good looks like | Failure mode the market complains about |
|---|---|---|
| Research | Fast backtests, closed bars only, no look-ahead, declared fee/slippage/fill model | Pretty equity curves that cannot be reproduced |
| Validate | Out-of-sample/walk-forward, Monte Carlo, parameter stability, trade-count significance | Overfitting a single window; no significance test |
| Size and risk | Max order/position/drawdown, daily loss, kill switch, exposure caps, stale-data refusal | One bad signal drains the account; no hard stop |
| Execute | One pipeline for paper/testnet/live; idempotent intents; receipt-derived fills | Paper and live disagree; duplicate fills |
| Operate | Positions/PnL/ledger, alerts, exports, audit trail, restart-safe state | Screens disagree; ghost positions |
| Extend | Python SDK, indicators, ML, notebooks, plugin ecosystem, hot reload | Monolith; strategy code welded to the engine |
| Delegate | Agent-readable state and safe actions, with a human approval boundary | Agent has unrestricted keys or no observability |
| Trust | Self-hosted, keys local, explicit mode labels, reproducible results | Custody risk; hidden live trading; unverifiable claims |

ezCHAIN already has the hardest part: an append-only ledger, a paper execution path, a
risk policy, reconciliation and a paper gate. The missing layer was the **extension and
delegation surface** — not another strategy.

## 2. Competitor matrix

| Project | Stars | Strategy/plugin model | ML | Agent/MCP | Backtesting | Notable trust boundary |
|---|---:|---|---|---|---|---|
| [Freqtrade](https://github.com/freqtrade/freqtrade) | 55.8k | Python strategy classes, hyperopt, FreqAI | Adaptive ML (FreqAI) | REST/Telegram; no first-class MCP | Strong, dry-run, plotting | Exchange keys in config; dry-run first |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | 20.6k | V2 controllers, scripts, connectors | Via external models | [Condor](https://hummingbot.org/condor/) two-layer LLM reasoning + deterministic execution | Paper trading, backtesting | Exchange keys encrypted; Gateway for DEX |
| [Jesse](https://github.com/jesse-ai/jesse) | 8.7k | Python strategies, 300+ indicators | Built-in ML pipeline (scikit-learn) | **Jesse MCP** for Claude/Codex/Cursor/VS Code/Zed | Strong, Monte Carlo, significance, optimization | Self-hosted; research/live parity |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | 30.6k | Rust-native event-driven strategies | AI-training-friendly engine | Not the primary surface | Nanosecond, multi-venue, same code live | Rust core + Python control plane |
| [OctoBot](https://github.com/Drakkar-Software/OctoBot) | 6.8k | Visual + Python, grid/DCA/TradingView | OpenAI/Ollama AI connectors | No MCP; node/mobile UI | Yes | Self-custody, local node |
| [Superalgos](https://github.com/Superalgos/Superalgos) | 5.7k | Visual strategy designer, data mining | Limited | No MCP | Yes | Self-hosted multi-server |
| [Backtrader](https://github.com/mementum/backtrader) | 23.5k | Python strategy library | External | No | Library, not a platform | No execution custody |

Patterns worth copying:

1. **Python is the strategy language.** Every serious platform lets a user write Python
   and import their own packages. A closed node DSL cannot absorb ML research.
2. **One strategy class runs in research and execution.** Nautilus and Jesse make parity
   explicit; ezCHAIN's graph evaluator already does this for graphs, and the plugin
   contract must do it for code.
3. **ML is a pipeline, not a checkbox.** FreqAI trains/retrains models inside the bot;
   Jesse gathers labelled data from backtests and serves predictions inside strategies.
   The useful primitive is "bring your own model, run it in the strategy", not a bundled
   model zoo.
4. **Agents are arriving through MCP.** Jesse ships an MCP server; Hummingbot ships
   Condor; Kraken's 2025 CLI added MCP + paper trading. An agent surface is now table
   stakes, not a differentiator by itself.
5. **The differentiator is the safety boundary.** Condor's own framing is probabilistic
   reasoning separated from deterministic execution. ezCHAIN's ledger + paper gate +
   intent-only SDK is the stronger version of that split.

## 3. Agent findings

### MCP is the integration protocol

The [MCP 2025-11-25 specification](https://modelcontextprotocol.io/specification/2025-11-25)
defines JSON-RPC 2.0 over stdio/HTTP with lifecycle negotiation, tools, resources and
prompts. Both target agents support it natively:

- **Hermes Agent** ([README](https://github.com/NousResearch/hermes-agent)) connects
  external tool servers through `mcp_servers` in `~/.hermes/config.yaml`, supports local
  stdio and remote HTTP, discovers tools at startup and can filter per server.
- **OpenClaw** ([README](https://github.com/openclaw/openclaw),
  [MCP docs](https://docs.openclaw.ai/tools/mcp)) stores servers under `mcp.servers`,
  supports Streamable HTTP/SSE/stdio, applies its tool-profile and approval policy to
  every MCP tool, and has `openclaw mcp doctor --probe` for reachability.

The MCP specification itself warns that tools are **model-controlled** and recommends a
human in the loop with the ability to deny invocations. That maps directly to ezCHAIN's
rule: reads open, writes authenticated and opt-in, no live tool.

### The security research is unambiguous

- [OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10) lists token/secret
  exposure, privilege escalation via scope creep, tool poisoning, supply-chain attacks
  and insufficient logging.
- [MCP03: Tool Poisoning](https://owasp.org/www-project-mcp-top-10/2025/MCP03-2025%E2%80%93Tool-Poisoning)
  shows that descriptions, parameter schemas and response content are treated as trusted
  context by the model. A malicious server can change a tool after approval ("rug pull").
- [Microsoft's MCP control-plane guidance](https://developer.microsoft.com/blog/securing-mcp-a-control-plane-for-agent-tool-execution/)
  argues for policy, identity and audit around tool execution, not just prompt hygiene.
- The [AI-Trader benchmark](https://arxiv.org/html/2512.10971v1) finds that general
  intelligence does not automatically produce good trading: most agents had poor returns
  and weak risk management, and risk control was the differentiator.

Implications implemented here:

- the MCP server exposes **no mode argument** and no live order tool;
- writes are absent unless `EZCHAIN_MCP_ALLOW_WRITES=1`; every write still needs the API
  bearer token;
- destructive tools carry MCP annotations (`destructiveHint`) so the host can prompt;
- tool/resource text is treated as data, and the server instructions say so;
- plugin manifests declare permissions and chains; unknown permissions fail discovery;
- plugin code runs in a supervised worker with a scrubbed environment, not in the API
  process;
- agent-readable state is a single resource (`ezchain://state`) with mode, kill switch,
  risk, account, positions, strategies and plugins.

## 4. 2026 trends that matter

1. **Agentic harnesses, deterministic execution.** Condor, Jesse MCP and Kraken's CLI all
   separate "the model reasons" from "the engine executes". ezCHAIN's intent-only SDK and
   one execution path are the same architecture with stronger accounting.
2. **MCP as the universal tool bus.** Hermes, OpenClaw, Claude, Codex, Cursor, VS Code
   and Zed all speak it. Shipping one MCP server reaches all of them; a bespoke plugin
   per agent does not.
3. **ML moves into the strategy package.** FreqAI/Jesse show demand for training,
   feature engineering and inference inside the bot. The platform should not own the
   model; it should let a plugin depend on `numpy`/`scikit-learn`/`torch` and run it in
   the strategy process with the same risk clamps.
4. **Risk is the product.** The AI-Trader result, the 2022–2025 bot failures and the
   competitor docs all converge: hard limits, kill switches, paper gates and honest mode
   labels matter more than a new indicator.
5. **Local-first and self-custody.** OctoBot and ezCHAIN both emphasize self-hosting;
   agent convenience must not become key custody. The plugin worker has no key material,
   and the executor remains a separate process (ADR-0026).
6. **Reproducibility and provenance.** Signed packages, pinned manifests, immutable
   backtests and replayable state are the trust primitives. A strategy is an artifact
   with a version, a hash and a paper record, not an editable file on a server.

## 5. Gap analysis and what this PR closes

| Gap | Before | After |
|---|---|---|
| External code strategies | Docs only; five declarative kinds | `ezchain-sdk` + supervised worker + `kind=plugin` |
| Plugin catalogue | None | `GET /api/v1/plugins`, reload, manifest issues |
| Agent reads | REST only, no single state doc | `ezchain://state`, `GET /api/v1/agent/state`, MCP resources |
| Agent actions | None | MCP tools, paper-only, read-only default, bearer auth |
| Paper/live boundary | Paper executor only | Server-derived mode; live boot refused; readiness endpoint |
| ML packages | Not addressed | Plugin owns its dependencies; SDK has no ML lock-in |
| Plugin state | Not replayable | `ctx.state` persisted in the run log |
| Plugin failure containment | None | Timeout, memory cap, scrubbed env, crash quarantine |

Still open (tracked, not claimed):

- plugin backtesting harness (graph backtests remain the only harness);
- signed plugin packages and a registry;
- bwrap/seccomp/network namespace for a real sandbox;
- per-user RBAC and scoped API keys (one deployment token today);
- streaming market data to plugins (v1 is request/response);
- live executor and venue adapters (ADR-0026 is a contract, not shipped code).

## 6. Sources

Primary sources read for this note:

- MCP specification 2025-11-25: <https://modelcontextprotocol.io/specification/2025-11-25>
- Hermes Agent: <https://github.com/NousResearch/hermes-agent> and
  <https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp>
- OpenClaw: <https://github.com/openclaw/openclaw> and
  <https://docs.openclaw.ai/tools/mcp>
- Freqtrade: <https://github.com/freqtrade/freqtrade>, FreqAI:
  <https://www.freqtrade.io/en/stable/freqai/>
- Hummingbot: <https://github.com/hummingbot/hummingbot>, Condor:
  <https://hummingbot.org/condor/>
- Jesse: <https://github.com/jesse-ai/jesse>
- NautilusTrader: <https://github.com/nautechsystems/nautilus_trader> and
  <https://nautilustrader.io/docs/latest/concepts/strategies>
- OctoBot: <https://github.com/Drakkar-Software/OctoBot>
- Superalgos: <https://github.com/Superalgos/Superalgos>
- Backtrader: <https://github.com/mementum/backtrader>
- OWASP MCP Top 10: <https://owasp.org/www-project-mcp-top-10>
- OWASP MCP03 tool poisoning:
  <https://owasp.org/www-project-mcp-top-10/2025/MCP03-2025%E2%80%93Tool-Poisoning>
- Microsoft, "Securing MCP: A Control Plane for Agent Tool Execution":
  <https://developer.microsoft.com/blog/securing-mcp-a-control-plane-for-agent-tool-execution/>
- AI-Trader benchmark: <https://arxiv.org/html/2512.10971v1>

Where this note states a conclusion about ezCHAIN behavior, the code and tests in this
repository are the evidence; the competitor links are context, not an endorsement.
