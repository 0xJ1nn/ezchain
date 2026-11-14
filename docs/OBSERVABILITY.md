# OBSERVABILITY — answering a money-path question

The PAPER stack answers "why was this order rejected" from three signals that share one
vocabulary: the persisted order row (`paper_orders.reason`, a code from
[REASON-CODES.md](REASON-CODES.md)), the access log line (one JSON object per request,
carrying `request_id`, `intent_id`, `order_id`, `strategy_id` and `mode`), and the
`/metrics` exposition. This page is the query set that turns those into an answer. The
metric families themselves are listed in
[RUNBOOK.md §12.6.2.1](RUNBOOK.md#12621-operational-metric-families).

**Read this first.** Every query below is a *filter*, not a verdict. An empty result means
**no matching event in that window** — not "all good". A quiet deployment, a stopped
scheduler and a healthy one look identical in an empty graph, so always look at
`ezchain_up`, `ezchain_paper_orders_total` and the time range before concluding anything.
Where a family can be *absent* (the database could not be read), the scrape omits it:
absence is unknown, and it is never zero.

## Families this page reads

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `ezchain_paper_rejects_total` | counter | `reason` | persisted refusals, one series per reason code |
| `ezchain_paper_orders_total` | counter | `status` | persisted orders by lifecycle status |
| `ezchain_venue_latency_seconds` | histogram | `venue` | wall-clock seconds of one venue call |
| `ezchain_source_freshness_seconds` | gauge | `source`, `source_class` | age of the newest persisted observation |
| `ezchain_source_freshness_budget_seconds` | gauge | `source_class` | the budget that age is compared with |
| `ezchain_breaker_state` | gauge | `provider`, `state` | `0` closed, `1` half-open, `2` open |

`ezchain_paper_rejects_total` is sampled from the order rows at scrape time
(`GROUP BY reason WHERE status = 'rejected'`), so it counts every refusal the database
holds rather than only the ones this worker happened to see.

**Cardinality.** `reason` is drawn from the reason-code taxonomy plus `OTHER` (a code
outside the taxonomy) and `UNKNOWN` (a rejection that persisted no code). A token mint, a
pair address, a wallet or an order id can never become a label: it is normalised to
`OTHER` before it reaches the registry. `venue` is the quote provider's own `kind` from
the chain registry, plus `other`.

**Present-but-empty vs absent.** When the database is readable and nothing has been
refused, the family's `# HELP`/`# TYPE` lines are there with no samples: that is a real
"no rejects". When the database cannot be read, the whole family is omitted and
`ezchain_up 0` says why. Neither case is a promise that orders worked.

## Step 1 — is anything being rejected?

```promql
# What it answers: the reject rate in the window, split by reason code.
sum by (reason) (rate(ezchain_paper_rejects_total[5m]))
```

```promql
# What it answers: the total reject rate, for the dashboard's single number.
sum(rate(ezchain_paper_rejects_total[5m]))
```

An empty result means **no rejected order in the last 5 minutes** (or the family is
absent because the database could not be read — check `ezchain_up`). It does not mean the
orders succeeded.

## Step 2 — which reason dominates?

```promql
# What it answers: the top reason code in the window, as the reason label.
topk(1, sum by (reason) (increase(ezchain_paper_rejects_total[1h])))
```

```promql
# What it answers: the top reason as a share of all refusals, so a long tail is visible.
topk(1, sum by (reason) (increase(ezchain_paper_rejects_total[1h])))
  / scalar(sum(increase(ezchain_paper_rejects_total[1h])))
```

Read the label, then look up that code in [REASON-CODES.md](REASON-CODES.md) for the
meaning, the module that raises it and whether it is retryable. `OTHER` means a stored
reason that is not in the taxonomy — inspect the row
(`SELECT reason, count(*) FROM paper_orders WHERE status = 'rejected' GROUP BY reason`)
rather than guessing from the graph.

## Step 3 — was it a timeout? read the venue latency

```promql
# What it answers: the p95 seconds per venue, for the last 15 minutes.
histogram_quantile(0.95, sum by (le, venue) (rate(ezchain_venue_latency_seconds_bucket[15m])))
```

```promql
# What it answers: the venues whose p95 sits above their own reported answer budget.
histogram_quantile(0.95, sum by (le, venue) (rate(ezchain_venue_latency_seconds_bucket[15m])))
  > 2.5
```

If `reason="QUOTE_TIMEOUT"`, `reason="NO_ROUTE"` or `reason="TIMEOUT"` appears in step 1
and a venue's p95 is close to the router's per-attempt budget, the venue — not the order —
is the cause. Cross-check with `ezchain_breaker_state{state!="closed"}`: a breaker that
opened would have refused the next call before it was made. An empty latency result means
**no venue call was recorded in the window**; the histogram only moves when a quote is
attempted.

`ezchain_venue_latency_seconds` is instrumented at the call boundary the router already
awaits (`provider.quote`), so its buckets — 50 ms to 10 s — contain the router's own 8 s
deadline and a timeout is a real bucket, not `+Inf`.

## Step 4 — was it a refusal to trust data? read the staleness

```promql
# What it answers: how far past its budget each source is, in seconds. 0 or less = inside.
ezchain_source_freshness_seconds - on(source_class)
  group_left ezchain_source_freshness_budget_seconds
```

```promql
# What it answers: which refusals are data-quality refusals, not strategy decisions.
sum by (reason) (increase(ezchain_paper_rejects_total{reason=~"QUOTE_STALE|SNAPSHOT_STALE|PROVIDER_STALE|MARK_STALE"}[1h]))
```

A `SNAPSHOT_STALE` or `QUOTE_STALE` refusal with a positive overage above answers "the mark
was too old to price this order". A `NaN` age is a clock fault, not freshness: it is
selected only with `ezchain_source_freshness_seconds != ezchain_source_freshness_seconds`
and it means the source's timestamp ran ahead of this host.

## Step 5 — tie it to one order

The graph tells you the *kind* of refusal. To name the order, use the log line, not the
metrics — the metrics deliberately carry no ids, because those are unbounded labels.

```bash
# The access line for one request id, from the api container's stdout.
docker compose -f infra/compose.yaml logs api | grep '"request_id": "<id>"'
```

The line carries `request_id`, `order_id`, `intent_id`, `strategy_id` and `mode`, so the
same order can be followed across the API, the engine and the executor. Then read the
persisted reason and its message:

```bash
curl -s 'http://localhost:8080/api/v1/paper/orders?limit=50' \
  | jq '.[] | select(.status == "rejected") | {id, reason, reasons}'
```

`reasons` holds the structured detail (the refusal's code, message and evidence); `reason`
is the code of the first gate that refused it, which is the value the `reason` label
counts.

## What an empty result means

| Query | Empty result |
|---|---|
| step 1 / 2 (rejects) | no refusal persisted in that window. Not "all orders worked" — check `ezchain_paper_orders_total{status="filled"}` |
| step 3 (venue latency) | no venue call in that window. A static paper-mark chain makes very few |
| step 4 (freshness overage) | no source series: either no observation is persisted, or the database could not be read (`ezchain_up 0`) |

An empty panel is never evidence of health on its own. Pair every read with
`ezchain_up` and with the order counters for the same window.
