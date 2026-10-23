"""Paper-to-live promotion gate, computed from persisted evidence.

The gate is a process gate, not a profitability promise (ADR-0009). It answers one
question: has this strategy version produced enough paper evidence, without an invariant
breach, for an owner to consider live promotion? It never promotes anything by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_MIN_DAYS = 14
DEFAULT_MIN_INTENTS = 100
DEFAULT_MIN_FILLS = 20


@dataclass(frozen=True)
class GateThresholds:
    min_days: int = DEFAULT_MIN_DAYS
    min_intents: int = DEFAULT_MIN_INTENTS
    min_fills: int = DEFAULT_MIN_FILLS
    require_backtest: bool = True
    require_reconciliation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_days": self.min_days,
            "min_intents": self.min_intents,
            "min_fills": self.min_fills,
            "require_backtest": self.require_backtest,
            "require_reconciliation": self.require_reconciliation,
        }


@dataclass(frozen=True)
class GateEvidence:
    started_at: datetime
    now: datetime
    intents: int
    fills: int
    backtests: int
    reconciliation_ok: bool
    invariant_breach: bool = False

    @property
    def days(self) -> float:
        return max(0.0, (self.now - self.started_at).total_seconds() / 86_400)


@dataclass(frozen=True)
class GateCheck:
    id: str
    label: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    checks: tuple[GateCheck, ...]
    thresholds: GateThresholds
    evidence: dict[str, Any]
    label: str = "PAPER"

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "label": self.label,
            "checks": [check.to_dict() for check in self.checks],
            "thresholds": self.thresholds.to_dict(),
            "evidence": self.evidence,
        }


def evaluate_gate(evidence: GateEvidence, thresholds: GateThresholds | None = None) -> GateResult:
    """Evaluate the gate. Every check is reported, including the ones that pass."""
    limits = thresholds or GateThresholds()
    days_ok = evidence.days >= limits.min_days
    intents_ok = evidence.intents >= limits.min_intents
    fills_ok = evidence.fills >= limits.min_fills
    backtest_ok = not limits.require_backtest or evidence.backtests > 0
    reconciliation_ok = not limits.require_reconciliation or evidence.reconciliation_ok
    invariant_ok = not evidence.invariant_breach
    checks = (
        GateCheck(
            "paper_days",
            f"Paper for at least {limits.min_days} days",
            days_ok,
            f"{evidence.days:.1f} days",
        ),
        GateCheck(
            "intents",
            f"At least {limits.min_intents} paper intents",
            intents_ok,
            f"{evidence.intents} intents",
        ),
        GateCheck(
            "fills",
            f"At least {limits.min_fills} paper fills",
            fills_ok,
            f"{evidence.fills} fills",
        ),
        GateCheck(
            "backtest",
            "A backtest exists for this strategy",
            backtest_ok,
            f"{evidence.backtests} backtests",
        ),
        GateCheck(
            "reconciliation",
            "Ledger reconciliation is clean",
            reconciliation_ok,
            "clean" if evidence.reconciliation_ok else "failed",
        ),
        GateCheck(
            "invariants",
            "No recorded invariant breach",
            invariant_ok,
            "none" if invariant_ok else "breach recorded",
        ),
    )
    return GateResult(
        eligible=all(check.passed for check in checks),
        checks=checks,
        thresholds=limits,
        evidence={
            "started_at": evidence.started_at.isoformat(),
            "now": evidence.now.isoformat(),
            "days": round(evidence.days, 2),
            "intents": evidence.intents,
            "fills": evidence.fills,
            "backtests": evidence.backtests,
            "reconciliation_ok": evidence.reconciliation_ok,
            "invariant_breach": evidence.invariant_breach,
        },
    )


__all__ = [
    "DEFAULT_MIN_DAYS",
    "DEFAULT_MIN_FILLS",
    "DEFAULT_MIN_INTENTS",
    "GateCheck",
    "GateEvidence",
    "GateResult",
    "GateThresholds",
    "evaluate_gate",
]
