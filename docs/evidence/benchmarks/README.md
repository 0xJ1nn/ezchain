# Evidence — kernel benchmark protocol and status (2026-11-23)

Scope: the per-function timing behind the performance claims in ADR-0049 and ADR-0050
(`docs/DECISIONS.md`). This document records how such a measurement must be taken and what
evidence exists today. It claims no speed figure: no kernel benchmark was executed on the
host that authored it, and no raw output is committed.

## What a recorded run must carry

A timing number is not a claim until it is reproducible. Every recorded kernel benchmark
must state, in the run artifact itself:

- **Host** — machine model, CPU, memory and operating system, captured at run time.
- **Interpreter / toolchain** — Python or Rust version and the exact build profile; for a
  PyO3 port, the ABI and the extension file that was loaded.
- **Corpus** — the recorded input set, by path and count, so the same inputs can be replayed.
- **Sample size** — iterations per case and the number of repeated runs.
- **Interleaving** — the order in which the compared implementations are called, alternating
  so that thermal drift or scheduling noise cannot be attributed to one side.
- **Raw output** — the unedited per-run measurements, committed beside the summary.

A number without all six fields is unrecorded, whatever it reported.

## What is committed today

| Path | What it is | Timing? |
|---|---|---|
| `rust/ezchain-bridge/tests/test_bridge_parity.py` | Parity harness: runs each recorded case against the Python original and the built PyO3 module and requires identical values and typed errors | No — parity only |
| `rust/ezchain-bridge/tests/cases.jsonl` | Recorded case corpus (scope normaliser and stale reason) | No — inputs only |
| `tools/stress/http.py` (`http.py:494`) | End-to-end HTTP latency in the stress harness | Yes, end-to-end only; no single function is profiled |
| `docs/evidence/p0-production/stress-all-2026-10-23.md` | Dated release-drill report with that end-to-end output | Yes, dated 2026-10-23 |

The 53 scope-normaliser cases named in ADR-0050 are the parity corpus above: parity is
committed and reproducible. The speed comparison that once accompanied them was kept in an
uncommitted note with no harness, corpus or raw output, so it is **unrecorded** and no
multiplier is repeated here.

## Unmeasured paths

ADR-0049 lists the four candidate hot paths — event decode, quote ladders, the execution
clock and the ledger hot section — as unmeasured. No run has been recorded for any of them.
The measurement that would settle each is described in that ADR; this directory is where a
dated run artifact belongs once one exists. Money arithmetic and the scope normaliser are
likewise unmeasured here: the parity harness proves correctness, not speed.

## Reproducing a baseline

Once the bridge is built, the parity harness gives the callable pair and the corpus needed
for an interleaved timing run:

```bash
# from the repository root, with the project virtualenv active
.venv/bin/python -m pytest rust/ezchain-bridge/tests/test_bridge_parity.py -v
```

That command verifies parity only. A timing run must add its own driver that interleaves the
two callables over the recorded corpus at a declared sample size and commits host,
interpreter, corpus, sample size, interleaving and raw output with the result. Until such a
driver and its output are committed, no per-kernel speed figure in this repository is a
recorded claim.
