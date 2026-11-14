# services/harness

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

`SessionRecord` (`ezchain_harness.record`) is the persisted record: schema
`ezchain.harness.session.v1`, the provider, the pinned id, the start time and one row per turn
with `model_id`, token counts, cost, tool-call count, stop kind and latency. Its `render()` is
the JSON an operator or panel shows; `write(path, secrets=...)` is the file.

## Where the key comes from

**The environment only.** There is no key parameter on any client — a test asserts the
constructor signatures carry no name containing `key`, `token`, `secret`, `password` or
`credential`. `Credential.from_env(name)` reads the variable on every call, so a rotated key is
picked up without rebuilding the client, and the value exists only long enough to build one
request header.

A missing key raises `MissingCredential` before a request is sent. `Credential.__str__` and
`__repr__` render `[redacted]`, and every line the client logs passes through
`redact(..., secrets=(live_key,))` — an exact-string scrub first, then shape patterns — so a
provider that echoes the key inside an error body still produces a clean log line and a clean
`ProviderHTTPError`.

## What happens on a timeout or a provider error

`complete()` makes **exactly one** transport call. There is no retry, no backoff and no replay
anywhere in this package:

- `httpx.TimeoutException` → `ProviderTimeout`
- any other `httpx.HTTPError` → `ProviderTransportError`
- HTTP status ≥ 400 → `ProviderHTTPError(provider, status, detail)` with the detail redacted
- a body that does not decode → `ProviderProtocolError`

All of them are `ProviderError`, which the loop catches and turns into a failed session
(`MODEL_FAILED`) carrying the detail. That is the property that matters for money: a turn whose
tool calls already executed is never replayed, because the loop's answer to a raised error is to
stop, not to ask the model again. `tests/harness/test_providers_failure.py` runs the real client
in the real loop with a fake MCP server and asserts the place-order tool ran exactly once and the
session ended `FAILED`/`MODEL_FAILED`.

## Cost

No published price table ships here: a baked-in rate goes stale silently and a stale rate is a
wrong budget. `ModelPricing(input_per_million, output_per_million)` is a required argument, in
`Decimal`, and every answered turn is priced with it and written to the record.
