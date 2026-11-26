"""The real-money contract cannot claim more than the build does.

`docs/LIVE-EXECUTION.md` is the normative document for live trading, and it described a
system that does not exist: an executor HTTP service, an encrypted vault file, a simulate
stage, live tables, a `live:` book, and a code (`RECONCILE_DRIFT`) that is in no taxonomy.
A reader could not tell a plan from a property.

Two things are pinned here, both of them failures that already happened:

1. **Gate numbers.** The document said `≥ 7 days, ≥ 20 intents, ≥ 5 fills` while the code,
   ADR-0009 and the wireframe all said 14/100/20. Two thresholds for one gate is how a
   strategy gets promoted a week early.
2. **Plan-only codes.** Anything the contract names but the taxonomy lacks must be marked
   as unbuilt in the same document. When a code lands in the taxonomy this test fails, which
   is the reminder to delete the "not built" note instead of leaving a stale one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ezchain_api.config import Settings
from ezchain_core.reason_codes import REASON_CODES

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "LIVE-EXECUTION.md"
STATES = ROOT / "design" / "wireframe" / "27-states.html"

# Codes the contract names that no taxonomy row exists for. Each must be described as
# unbuilt where the document names it; the day one is implemented this list shrinks.
PLAN_ONLY_CODES = ("RECONCILE_DRIFT",)


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_the_documented_paper_gate_defaults_are_the_configured_ones() -> None:
    """One threshold set for the gate, stated once."""
    text = _contract()
    match = re.search(
        r"≥ (?P<days>\d+) days,\s*≥ (?P<intents>\d+) intents,\s*≥ (?P<fills>\d+) fills", text
    )
    assert match is not None, "the contract must state the paper gate thresholds"
    defaults = Settings(environment="test")  # type: ignore[call-arg]
    assert int(match["days"]) == defaults.paper_gate_min_days
    assert int(match["intents"]) == defaults.paper_gate_min_intents
    assert int(match["fills"]) == defaults.paper_gate_min_fills


def test_the_wireframe_paper_gate_sample_matches_the_same_thresholds() -> None:
    """A sample panel that contradicts the gate teaches the wrong number."""
    defaults = Settings(environment="test")  # type: ignore[call-arg]
    markup = STATES.read_text(encoding="utf-8")
    # The panel is the `GATE_NOT_MET` state: elapsed days, intents and fills out of their
    # thresholds.
    panel = re.search(
        r"(?P<days>\d+)d / (?P<day_target>\d+)d</span>"
        r".*?(?P<intents>\d+) / (?P<intent_target>\d+) intents</span>"
        r".*?(?P<fills>\d+) / (?P<fill_target>\d+) fills",
        markup,
        re.S,
    )
    assert panel is not None, "the paper-gate state panel must show all three thresholds"
    assert int(panel["day_target"]) == defaults.paper_gate_min_days
    assert int(panel["intent_target"]) == defaults.paper_gate_min_intents
    assert int(panel["fill_target"]) == defaults.paper_gate_min_fills
    # The remaining-fills sentence is derived from the same pair, so it drifts with them.
    remaining = int(panel["fill_target"]) - int(panel["fills"])
    assert f"{remaining} fills" in markup


@pytest.mark.parametrize("code", PLAN_ONLY_CODES)
def test_a_code_the_taxonomy_lacks_is_marked_unbuilt(code: str) -> None:
    assert code not in REASON_CODES, (
        f"{code} now has a taxonomy row; remove it from the contract's unbuilt notes "
        "and from PLAN_ONLY_CODES rather than leaving two descriptions of it"
    )
    assert code in _contract(), f"the contract should name {code} where it is unbuilt"
    lowered = _contract().lower()
    assert "not built" in lowered, "the contract must say which of these do not exist"


def test_the_contract_has_a_what_exists_table() -> None:
    """The index is what stops a section being read as a description of running code."""
    text = _contract()
    assert "## 0. What exists in this build" in text
    section = text.split("## 0. What exists in this build", 1)[1].split("\n## ", 1)[0]
    assert "Not built" in section
    # The three claims that were most misleading are the ones worth naming explicitly.
    for element in ("executor_client", "vault.json", "RECONCILE_DRIFT"):
        assert element in section, f"the index should account for {element}"
