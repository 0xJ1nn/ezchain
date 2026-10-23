# ezCHAIN SDK

The stable, Apache-2.0 contract between ezCHAIN and an external strategy plugin.

A plugin is a normal Python package that depends on `ezchain-sdk>=1,<2`, ships an
`ezchain.plugin.json` manifest and exposes one `Strategy` subclass. The host owns market
data, risk, sizing, quoting, execution and accounting; a plugin only returns intents.

```python
from ezchain_sdk import Candle, Context, Intent, Strategy


class Momentum(Strategy):
    def on_start(self, ctx: Context) -> None:
        ctx.state.setdefault("last_side", None)

    def on_candle(self, ctx: Context, candle: Candle) -> list[Intent]:
        closes = [bar.close for bar in ctx.candles_for("1h", 21)]
        if len(closes) < 21 or closes[-21] <= 0:
            return []
        change = (closes[-1] / closes[-21] - 1) * 100
        if change >= ctx.params["entry_pct"] and ctx.state["last_side"] != "buy":
            ctx.state["last_side"] = "buy"
            return [Intent(side="buy", notional_usd="25", reason="MOMENTUM_ENTRY")]
        return []
```

Install the plugin into the same environment as the API/engine (or point
`EZCHAIN_PLUGIN_PATHS` at a directory containing the package) and reload the catalogue:

```bash
pip install ./my-strategy
curl -X POST -H "Authorization: Bearer $EZCHAIN_API_TOKEN" \
  http://127.0.0.1:8000/api/v1/plugins/reload
```

Rules that are enforced by the host, not by convention:

- no wallet, key, signing, HTTP or filesystem API is exposed by the SDK;
- a plugin returns intents, never orders;
- one order intent per tick; the paper risk path clamps or rejects the rest;
- plugin state is JSON and is persisted with the run, so a restart replays the decision;
- code runs in a supervised worker process with a timeout, memory cap and scrubbed
  environment. This is containment, not a security sandbox: install only plugins you trust.

See `docs/STRATEGY-SDK.md` in the ezCHAIN repository for the full manifest and lifecycle.
