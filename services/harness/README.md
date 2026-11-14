# services/harness

The agent harness: the model clients the runtime loop calls, and a headless command that
runs one session from a terminal with every step printed.

## Model clients

The model clients the agent runtime calls. `ezchain-agent-runtime` owns the loop, the
budgets, the session lifecycle and the sourced answers; this service owns the two providers
behind its `ModelClient` protocol:

| Provider | Client | Credential | Wire |
|---|---|---|---|
| Anthropic | `AnthropicClient` | `ANTHROPIC_API_KEY` | Messages API `/v1/messages` |
| OpenAI-compatible | `OpenAICompatibleClient` | `OPENAI_API_KEY` | chat completions `/chat/completions` |

"OpenAI-compatible" is the format, not one vendor: the base URL is a constructor argument, so
the same client speaks to OpenAI, a gateway or a local server.

**Transport is injected.** A client is built with a `Transport` callable and calls it with an
`HttpRequest`, so the suite drives the whole mapping — message shapes, tool-call round-trips,
usage, errors — with no key and no socket. `HttpxTransport` is the only network code in the
package and is a single `httpx.request`; `httpx` is already a pinned dependency of this
repository (`requirements.txt`, `requirements-runtime.txt`).

## What is pinned

The model id is **a constructor argument**, never an environment read inside `complete()`. A
session that starts on `claude-sonnet-4-5` sends that id on every turn and records that id on
every turn, even if `EZCHAIN_AGENT_MODEL` or another model variable changes under it.
`SessionRecord` refuses a turn whose id differs from its own, and a client refuses a record
pinned to a different id, so the id that is billed and the id that is written down cannot
drift apart. `factory.model_from_env` reads `EZCHAIN_AGENT_MODEL` **once**, to choose the id
for a new session; `factory.build` then hands that value to the client.


## The headless command

The headless command: one ezCHAIN agent session from a terminal, with every step printed as
it happens.

It owns no loop. It constructs the real
[`AgentLoop`](../agent-runtime/src/ezchain_agent_runtime/loop.py) from
`services/agent-runtime`, hands it the same budgets and the same kill switch the panel
does, and reports the loop's outcome. A session that stops, fails, or waits for a person is
never reported as a success.

## Running a session

Both seams the loop takes are supplied as `module:attribute` factories, so the command needs
no provider SDK of its own:

```bash
ezchain-harness \
  --model   my_provider:build_client \
  --toolbox my_mcp:build_toolbox \
  --prompt  "Read the account and summarise paper equity."
```

| Flag | Meaning |
|---|---|
| `--prompt` | the session prompt (default: stdin) |
| `--model` | `module:attribute`, a zero-argument factory returning a `ModelClient` |
| `--toolbox` | `module:attribute`, a zero-argument factory returning an `McpToolbox` |
| `--session-turns` / `--session-tokens` / `--session-cost-usd` | the session budget |
| `--daily-turns` / `--daily-tokens` / `--daily-cost-usd` | the day's budget for the agent |

Defaults are the runtime's own: 8 turns, 10 000 tokens and $1 per session; 50 turns,
100 000 tokens and $10 per day. A factory that cannot build its client is reported as
unusable configuration, not as a failed session, because no session started.

## Exit codes

| Code | Name | Meaning |
|---|---|---|
| `0` | completed | the loop reached `done` with a sourced answer |
| `1` | failed | the session failed (`MODEL_FAILED`, `MCP_UNREACHABLE`, `UNSOURCED_NUMBER`, …) |
| `2` | usage | the command could not start: bad flag, unimportable factory, empty prompt |
| `3` | stopped | the session stopped: kill switch or a budget |
| `4` | awaiting approval | an order came back `pending_approval`; a person must decide it |

Only `0` means the session finished. A stopped or failed session never exits `0`, and a
session waiting for a person is not a success: the command does not decide, and neither
does the loop — the platform's approval queue does.

## Trace format

Every line goes to stdout, is flushed as it is written, and carries the same prefix:

```
ezchain-harness: session 0f7c…
ezchain-harness: tools 4: ezchain_get_account, ezchain_get_state, …
ezchain-harness: turn 1
ezchain-harness: call ezchain_get_account {}
ezchain-harness: result ezchain_get_account ok as_of="…", cash="1000.00", equity="1000.00"
ezchain-harness: turn 2
ezchain-harness: answer Read from the account.
ezchain-harness: number equity 1000.00 (source ezchain_get_account.equity, age 3s)
ezchain-harness: end COMPLETED state=done tools=1 turns=2 tokens=0 cost_usd=0.00
```

`session`, `tools`, `turn`, `call`, `result`, `answer`, `number` and `end` appear in that
order as the session proceeds. `end` is one line rendered from the `SessionOutcome`:
the typed `StopReason`, the state, how many tool results the session recorded, and the
turns, tokens and cost spent; a stop carries its detail. A tool result is one bounded line,
so a large payload cannot drown the trace.

The trace wraps the two seams and never inspects a result for an approval or a refusal —
those are the loop's decisions, and the `end` line reports what the loop decided.

## Tests

`tests/harness` scripts the model and the MCP server through the same factory flags. No
test opens a socket, and no test needs a provider key:

```bash
pytest tests/harness -q
```
