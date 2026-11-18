#!/usr/bin/env python3
"""Oracle harness for the pure ``to_dict`` spellings of money/quote records.

Generates a deterministic corpus, builds the *real* Python objects from
``packages/core-py`` (``money.CostPreview``, ``types.SourceRef`` and
``types.Quote``), calls their ``to_dict()`` methods and records the exact result
as canonical JSON bytes. ``tests/todict.rs`` asserts the Rust field maps,
serialised through ``ezchain-jsoncanon``, match every recorded byte.

Usage (from the checkout root, with the project venv active)::

    .venv/bin/python rust/ezchain-decimal/tests/todict_oracle.py            # write fixture
    .venv/bin/python rust/ezchain-decimal/tests/todict_oracle.py --check    # verify no drift

Every fixture line is tab-separated and self-describing::

    S\t<name>\t<url|N>\t<y>\t<mo>\t<d>\t<h>\t<mi>\t<s>\t<us>\t<canonical json>
    C\t<platform>\t<venue>\t<network|N>\t<impact|N>\t<slip|N>\t<worst|N>\t<fill>
        \t<name>\t<url|N>\t<y>\t<mo>\t<d>\t<h>\t<mi>\t<s>\t<us>\t<canonical json>
    Q\t<chain>\t<token_in>\t<token_out>\t<side>\t<amount_in>\t<amount_out>
        \t<price>\t<impact|N>\t<fee_usd>\t<kind>\t<route|N>
        \t<name>\t<url|N>\t<y>\t<mo>\t<d>\t<h>\t<mi>\t<s>\t<us>\t<canonical json>

``N`` is the sole null sentinel. A decimal input is its ``str(Decimal)`` form,
so Rust parses the exact same triple and re-emits its ``str`` spelling. The
datetime components are those of the UTC-normalised observation, because
``SourceRef.to_dict`` normalises to UTC before calling ``isoformat``; the
timezone arithmetic itself is not what this harness proves.

SEED / FIXTURE overrides exist so a held-out corpus with a different seed can
be generated and checked without overwriting the committed fixture.
"""

from __future__ import annotations

import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
CORE_PY_SRC = REPO_ROOT / "packages" / "core-py" / "src"
SEED = int(os.environ.get("EZ_TODICT_SEED", "20261118"))
FIXTURE = Path(
    os.environ.get("EZ_TODICT_FIXTURE", str(HERE / "todict_fixture.txt"))
)
sys.path.insert(0, str(CORE_PY_SRC))

from ezchain_core.money import CostPreview  # noqa: E402
from ezchain_core.types import Quote, SourceRef  # noqa: E402

BIG = "12345678901234567890123456789012345678"
TRICKY_DECS = [
    "0",
    "0E-8",
    "-0",
    "-0E-8",
    "1E+3",
    "0.1",
    "0.10",
    "-2.5",
    "1E-10",
    "5e-9",
    "15e-9",
    "0.000000035",
    "3.141592653589793238462643383279",
    BIG,
    BIG + ".12345678",
    "99999999999999999999.99999999",
]


def canonical(value: dict) -> str:
    """Exactly the byte string the canonical writer must produce."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    )


def rnd_dec(rng: random.Random, max_digits: int = 24) -> str:
    length = rng.randint(1, max_digits)
    coefficient = rng.choice("123456789") + "".join(
        rng.choice("0123456789") for _ in range(length - 1)
    )
    exponent = rng.randint(-12, 8)
    sign = rng.choice(["", "", "", "-"])
    return f"{sign}{coefficient}e{exponent:+d}"


def rnd_dt(rng: random.Random) -> datetime:
    """A naive, UTC or fixed-offset datetime with an optional fraction."""
    args = (
        rng.randint(1999, 2035),
        rng.randint(1, 12),
        rng.randint(1, 28),
        rng.randint(0, 23),
        rng.randint(0, 59),
        rng.randint(0, 59),
        rng.choice([0, 0, 0, 1, 10, 123456, 999999]),
    )
    kind = rng.choice(["naive", "utc", "offset"])
    if kind == "naive":
        return datetime(*args)
    if kind == "utc":
        return datetime(*args, tzinfo=UTC)
    offset = timezone(
        timedelta(
            hours=rng.choice([-9, -7, -5, 0, 2, 5, 9]),
            minutes=rng.choice([0, 30, 45]),
        )
    )
    return datetime(*args, tzinfo=offset)


def src_record(source: SourceRef) -> list[str]:
    """Encode a SourceRef's inputs as the UTC-normalised components to_dict reads."""
    observed = (
        source.observed_at.astimezone(UTC)
        if source.observed_at.tzinfo is not None
        else source.observed_at.replace(tzinfo=UTC)
    )
    return [
        source.name,
        "N" if source.url is None else source.url,
        str(observed.year),
        str(observed.month),
        str(observed.day),
        str(observed.hour),
        str(observed.minute),
        str(observed.second),
        str(observed.microsecond),
    ]


def dec_field(value: Decimal | int | None) -> str:
    if value is None:
        return "N"
    return str(value)


def source_line(source: SourceRef) -> str:
    return "\t".join(["S", *src_record(source), canonical(source.to_dict())])


def cost_line(preview: CostPreview) -> str:
    fields = [
        "C",
        str(preview.platform_fee_usd),
        str(preview.venue_fee_usd),
        dec_field(preview.network_fee_usd),
        dec_field(preview.price_impact_pct),
        dec_field(preview.slippage_bps),
        dec_field(preview.worst_case_out),
        str(preview.fill_fee_usd),
        *src_record(preview.source),
        canonical(preview.to_dict()),
    ]
    return "\t".join(fields)


def quote_line(quote: Quote) -> str:
    fields = [
        "Q",
        quote.chain,
        quote.token_in,
        quote.token_out,
        quote.side,
        str(quote.amount_in),
        str(quote.amount_out),
        str(quote.price),
        dec_field(quote.price_impact_pct),
        str(quote.fee_usd),
        quote.kind,
        "N" if quote.route is None else quote.route,
        *src_record(quote.source),
        canonical(quote.to_dict()),
    ]
    return "\t".join(fields)


def make_source(rng: random.Random, *, name: str | None = None, url: str | None = None) -> SourceRef:
    if name is None:
        name = rng.choice(
            ["jupiter", "kyberswap", "dex-analytics", "déx-\u4e2d\u6587", "source"]
        )
    if url is None:
        url = rng.choice([None, "https://venue.example/x?a=1&b=2", "/relative/path"])
    return SourceRef(name=name, url=url, observed_at=rnd_dt(rng))


def tricky_corpus() -> list[str]:
    rng = random.Random(SEED)
    lines: list[str] = []
    # SourceRef: naive/UTC/offset, with and without a fractional second, with
    # null and non-null urls, and non-ASCII names (ensure_ascii escaping).
    for observed in (
        datetime(2026, 11, 18, 10, 30, 15, 123456, tzinfo=UTC),
        datetime(2026, 11, 18, 10, 30, 15),
        datetime(2026, 1, 2, 3, 4, 5, 1, tzinfo=UTC),
        datetime(2026, 11, 18, 23, 59, 59, 999999, tzinfo=timezone(timedelta(hours=-7))),
        datetime(1999, 12, 31, 0, 0, 0, tzinfo=timezone(timedelta(hours=9, minutes=30))),
    ):
        for url in (None, "https://venue.example/x"):
            lines.append(source_line(SourceRef(name="jupiter", url=url, observed_at=observed)))
    lines.append(
        source_line(
            SourceRef(
                name="déx-\u4e2d\u6587",
                url="https://例え.example/路径",
                observed_at=datetime(2035, 6, 7, 8, 9, 10, 7, tzinfo=UTC),
            )
        )
    )
    # CostPreview: representation-sensitive money strings, echoed network fee
    # and price impact both null and non-null, and both slippage branches.
    for dec in TRICKY_DECS:
        lines.append(
            cost_line(
                CostPreview(
                    platform_fee_usd=Decimal("0"),
                    venue_fee_usd=Decimal(dec),
                    network_fee_usd=None,
                    price_impact_pct=None,
                    slippage_bps=50,
                    worst_case_out=Decimal(dec),
                    fill_fee_usd=Decimal(dec),
                    source=make_source(rng),
                )
            )
        )
    lines.append(
        cost_line(
            CostPreview(
                platform_fee_usd=Decimal("0E-8"),
                venue_fee_usd=Decimal("0.123456789"),
                network_fee_usd=Decimal("0.00000001"),
                price_impact_pct=Decimal("-0.50"),
                slippage_bps=None,
                worst_case_out=None,
                fill_fee_usd=Decimal("0.5"),
                source=make_source(rng, name="paper_mark", url=None),
            )
        )
    )
    lines.append(
        cost_line(
            CostPreview(
                platform_fee_usd=Decimal(0),
                venue_fee_usd=Decimal(BIG + ".00000001"),
                network_fee_usd=Decimal(BIG),
                price_impact_pct=Decimal("5.00000000"),
                slippage_bps=0,
                worst_case_out=Decimal("1E+3"),
                fill_fee_usd=Decimal("-0"),
                source=make_source(rng, name="déx-\u4e2d\u6587"),
            )
        )
    )
    # Quote: money spellings plus a nullable route and impact.
    for impact, route in ((None, None), (Decimal("0.125"), "jupiter/route"), (Decimal("-0"), "")):
        lines.append(
            quote_line(
                Quote(
                    chain="solana",
                    token_in="So111",
                    token_out="EPjFW",
                    side="buy",
                    amount_in=Decimal(dec),
                    amount_out=Decimal("0E-8"),
                    price=Decimal(dec),
                    price_impact_pct=impact,
                    fee_usd=Decimal("0.00000001"),
                    kind="jupiter",
                    route=route,
                    source=make_source(rng),
                )
            )
        )
    return lines


def random_corpus() -> list[str]:
    rng = random.Random(SEED + 1)
    lines: list[str] = []
    def maybe_dec() -> Decimal | None:
        return None if rng.random() < 0.35 else Decimal(rnd_dec(rng))

    for _ in range(160):
        lines.append(source_line(make_source(rng)))
    for _ in range(200):
        lines.append(
            cost_line(
                CostPreview(
                    platform_fee_usd=Decimal(rnd_dec(rng)),
                    venue_fee_usd=Decimal(rnd_dec(rng)),
                    network_fee_usd=maybe_dec(),
                    price_impact_pct=maybe_dec(),
                    slippage_bps=rng.choice([None, 0, 10, 25, 50, 100]),
                    worst_case_out=maybe_dec(),
                    fill_fee_usd=Decimal(rnd_dec(rng)),
                    source=make_source(rng),
                )
            )
        )
    for _ in range(200):
        lines.append(
            quote_line(
                Quote(
                    chain=rng.choice(["solana", "base", "arbitrum"]),
                    token_in=rnd_dec(rng),
                    token_out=rnd_dec(rng),
                    side=rng.choice(["buy", "sell"]),
                    amount_in=Decimal(rnd_dec(rng)),
                    amount_out=Decimal(rnd_dec(rng)),
                    price=Decimal(rnd_dec(rng)),
                    price_impact_pct=maybe_dec(),
                    fee_usd=Decimal(rnd_dec(rng)),
                    kind=rng.choice(["jupiter", "paper_mark", "lifi"]),
                    route=rng.choice([None, "route/one", "/x?y=1"]),
                    source=make_source(rng),
                )
            )
        )
    return lines


def render() -> str:
    lines = tricky_corpus() + random_corpus()
    return "\n".join(lines) + "\n"


def main() -> int:
    content = render()
    count = len(content.splitlines())
    if "--check" in sys.argv:
        current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
        if current != content:
            print(
                f"FAIL: fixture drift ({len(current.splitlines())} stored vs {count} generated)"
            )
            return 1
        print(f"OK: to_dict fixture matches oracle ({count} cases)")
        return 0
    FIXTURE.write_text(content, encoding="utf-8")
    by_kind: dict[str, int] = {}
    for line in content.splitlines():
        kind = line.split("\t", 1)[0]
        by_kind[kind] = by_kind.get(kind, 0) + 1
    print(f"wrote {FIXTURE} with {count} cases")
    print("per kind: " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
