# services/agent-runtime

ezCHAIN's built-in agent runtime (ADR-0042). One model loop over the platform's MCP tools,
inside hard per-session and per-day budgets.

**Boundary.** The runtime owns the loop, the model seam, the budgets and the session
lifecycle. It owns no money capability, holds no platform credential other than the scoped
`read`/`trade` agent key, and has no shell, URL fetch, file access or subprocess tool. Its
only door to ezCHAIN is an injected MCP toolbox — one child `ezchain-mcp` over stdio. The
tool surface is built from the server's own `tools/list` response; a name the server did
not list cannot be called.

The ADR-0042 import boundary holds: this service imports no `ezchain_core`, `ezchain_api`,
`ezchain_engine` or `ezchain_executor`, not even for the refusal codes. Those codes arrive as
plain strings inside the MCP result (the wire format the platform already sends), the two the
loop acts on are the local `KILL_SWITCH_CODES` table, and a test pins that table to
`RISK_REASON_MESSAGES` from the outside.

**One loop.** `AgentLoop.run(prompt)` returns a `SessionOutcome`:

| Field | Meaning |
|---|---|
| `state` | `idle`, `running`, `awaiting_approval`, `done`, `stopped`, `failed` |
| `reason` | typed `StopReason` (`COMPLETED`, `SESSION_TURNS_EXHAUSTED`, `KILL_SWITCH_ARMED`, …) |
| `answer` | sourced numbers, each with tool, path, value and age |
| `observations` | every tool result the session recorded |
| `usage` / `daily_usage` | turns, tokens and cost spent |

The lifecycle edges live in one table, `session.TRANSITIONS`. The loop consults it through
`Session.fire`; there is no second definition.

**Budgets.** `BudgetLimits` carries six limits: turns, tokens and cost for the session and
for the day. The day's counters are process-scoped, keyed by `(agent, UTC date)` in
`PROCESS_SPEND`, so every session an agent starts on the same day spends from one budget —
a loop is built per session, and a loop-local ledger would reset the day. A runtime run as
several processes injects a persisted mapping through `DailyLedger(spend=...)` with the same
`consume`/`used` contract. `SessionBudget.guard()` is called before every model turn and
every tool call, and `SessionBudget.overtaken()` re-checks immediately after a turn is paid
for: reaching a limit exactly still lets that turn deliver its answer, crossing one stops the
session with the matching typed reason instead of returning `COMPLETED`. Neither is clamped
silently.

**Kill switch.** The runtime checks its own `KillSwitch` before each turn and tool call. A
platform refusal carrying `KILL_SWITCH_ACTIVE`/`KILL_SWITCH_ARMED` stops the session the
moment the tool returns. Neither can be armed by the agent: arming is an operator act.

**No fake data.** The model returns an `Answer` whose summary may not contain digits and
whose numbers are `NumberClaim(label, value, tool, path)`. `resolve_answer` looks the value
up in this session's successful tool result and fails the session with
`UNSOURCED_NUMBER` (missing result, unknown path) or `NUMBER_MISMATCH` (stated value differs
from the read model). The rendered answer prints the source and age of each number.

**The trace.** Every session writes one append-only trace (ADR-0042's audit trail) through
the injected `trace` sink; with none injected it lives in a `MemoryTraceLog` for the life of
the loop, and a durable run passes a `FileTraceLog(root)` that writes one JSON-lines file per
session.

| Operation | Contract |
|---|---|
| `TraceSink.append` | the interface's only member: no update, no delete, no rewrite |
| `MemoryTraceLog.append/read/sessions` | the same contract without a filesystem |
| `FileTraceLog.append/read/sessions/path_for` | one file per session, opened `"a"` to write and read back |

Each event records the session start (prompt hash, model id), every turn (tokens, cost,
latency), every tool call (name, arguments digest, result status and digest, latency, refusal
code), every budget debit (session and day), every kill-switch observation, and the terminal
outcome with its typed reason, spend and approval id. Rules that hold by construction:

- **Append only.** A store refuses an event that does not continue the chain, and a session id
  with events already on disk cannot be started a second time.
- **Tamper evidence.** An event's digest is the SHA-256 of `canonical(record)`: one JSON
  object with sorted keys over a validated JSON-native type domain. A float or a `Decimal`
  is refused rather than guessed at, so `1` and `"1"` cannot collide and no delimiter exists
  to smuggle a field boundary across (the lesson of the ledger seal review). `verify_events`
  stops at the first mutated, reordered, dropped or duplicated event and names the index and
  the reason.
- **Replay.** `replay(events)` verifies the chain and rebuilds a `SessionSummary` — state,
  reason, turns, tool calls, tool errors, tokens, cost, numbers — from the events alone.
  `summarize(outcome)` reads the same summary off the live outcome, and the tests assert the
  two are equal for a completed session, a budget stop, a kill-switch stop, an approval stop,
  a tool error and a model failure.
- **Redaction.** Credential-shaped strings are replaced in every field on write, tool
  arguments and results are stored as digests rather than text, and the prompt is stored as
  a hash. The agent key appears in neither.

**Testing seam.** `ModelClient` and `McpToolbox` are protocols. The suite supplies a
scripted model and a fake MCP server; it makes no network call and needs no provider key.
The stdio transport that spawns `ezchain-mcp` is not built yet — the toolbox is the seam
where it will land, alongside the panel and the headless command.

**Hostile input.** `tests/agent_runtime/test_prompt_injection.py` runs the real loop over the
real MCP server in front of the real API with a model scripted to obey injected
instructions, and asserts the structural controls hold anyway: the approval queue, the key's
tool surface, the budgets and the absence of key material. The matrix and the honest gaps are
[docs/PROMPT-INJECTION.md](../../docs/PROMPT-INJECTION.md).
where it will land.

**Entry points.** The panel and the headless command (`services/harness`) are two fronts on
this same loop and the same approval path; neither keeps a second copy of either. See
[`services/harness/README.md`](../harness/README.md) for the command, its trace format and
its exit codes.
