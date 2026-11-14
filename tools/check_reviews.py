#!/usr/bin/env python3
"""Decide whether a pull request carries the review its diff requires.

A pull request is money-path when its diff touches a protected prefix:

    packages/core-py/   apps/api/alembic/   services/engine/
    services/executor/  packages/adapters/

A money-path diff needs two distinct approvers; any other diff needs one. An approver is
an ``APPROVED`` review, or a comment by an ``OWNER``, ``MEMBER`` or ``COLLABORATOR``
whose line begins with ``approved``. Several records from one login are one approver, and
a bot login is never one. A thread that cannot be read is a refusal, never a pass.

    git diff --name-only origin/main...HEAD | python3 tools/check_reviews.py 289
    python3 tools/check_reviews.py 289 -R 0xJ1nn/ezchain --diff changed.txt --json

Exit status: ``0`` when the evidence is present, ``1`` when it is missing or unreadable
(the merge gate refuses on any non-zero status), ``2`` on a usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

MONEY_PATHS: tuple[str, ...] = (
    "packages/core-py/",
    "apps/api/alembic/",
    "services/engine/",
    "services/executor/",
    "packages/adapters/",
)
MONEY_APPROVALS = 2
DEFAULT_APPROVALS = 1
MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
APPROVAL_LINE = re.compile(r"^\s*approved\b", re.IGNORECASE)

Runner = Callable[[Sequence[str]], str]


class EvidenceError(Exception):
    """The thread could not be read, so no review can be assumed."""


@dataclass(frozen=True)
class Approval:
    reviewer: str
    source: str


@dataclass(frozen=True)
class Evidence:
    reviews: tuple[Mapping[str, object], ...] = ()
    comments: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True)
class Verdict:
    money_path: tuple[str, ...]
    required: int
    approvers: tuple[Approval, ...]

    @property
    def satisfied(self) -> bool:
        return len(self.approvers) >= self.required

    def report(self) -> list[str]:
        where = ", ".join(self.money_path) if self.money_path else "no protected path"
        who = ", ".join(f"{a.reviewer} ({a.source})" for a in self.approvers) or "none"
        head = f"{where}; {self.required} distinct approver(s) required"
        if self.satisfied:
            return [head, f"ok — {len(self.approvers)} approver(s): {who}"]
        return [head, f"refused — {len(self.approvers)} of {self.required}: {who}"]


def money_path(paths: Iterable[str]) -> tuple[str, ...]:
    """The protected prefixes a diff touches, sorted; empty when it touches none."""
    touched: set[str] = set()
    for raw in paths:
        path = raw.strip().strip('"').removeprefix("./")
        for prefix in MONEY_PATHS:
            if path.startswith(prefix):
                touched.add(prefix)
    return tuple(sorted(touched))


def _actor(record: Mapping[str, object]) -> tuple[str, bool]:
    actor = record.get("author")
    if not isinstance(actor, dict):
        return "", False
    login = actor.get("login")
    name = login if isinstance(login, str) else ""
    return name, bool(actor.get("is_bot")) or name.endswith("[bot]")


def approvals(evidence: Evidence) -> tuple[Approval, ...]:
    """One entry per approving login, in the order the evidence appears."""
    found: dict[str, Approval] = {}
    for review in evidence.reviews:
        if str(review.get("state", "")).upper() != "APPROVED":
            continue
        login, bot = _actor(review)
        if login and not bot:
            found.setdefault(login, Approval(login, "review"))
    for comment in evidence.comments:
        login, bot = _actor(comment)
        if not login or bot:
            continue
        if str(comment.get("authorAssociation", "")).upper() not in MAINTAINER_ASSOCIATIONS:
            continue
        body = comment.get("body")
        if isinstance(body, str) and any(APPROVAL_LINE.match(line) for line in body.splitlines()):
            found.setdefault(login, Approval(login, "comment"))
    return tuple(found.values())


def evaluate(evidence: Evidence, paths: Iterable[str]) -> Verdict:
    touched = money_path(paths)
    required = MONEY_APPROVALS if touched else DEFAULT_APPROVALS
    return Verdict(money_path=touched, required=required, approvers=approvals(evidence))


def read_paths(lines: Iterable[str]) -> tuple[str, ...]:
    return tuple(line.strip() for line in lines if line.strip())


def _gh(argv: Sequence[str]) -> str:
    done = subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        detail = done.stderr.strip().splitlines()
        raise EvidenceError(f"gh pr view failed: {detail[0] if detail else done.returncode}")
    return done.stdout


def collect(repo: str, number: int, *, run: Runner | None = None) -> Evidence:
    """Read the pull request thread through ``gh``; any failure is an EvidenceError."""
    argv = ["pr", "view", str(number), "-R", repo, "--json", "reviews,comments"]
    try:
        raw = (run or _gh)(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceError(f"cannot run gh: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"gh printed no usable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("gh printed no usable document")
    reviews, comments = payload.get("reviews"), payload.get("comments")
    if not isinstance(reviews, list) or not isinstance(comments, list):
        raise EvidenceError("gh printed a thread without reviews and comments")
    return Evidence(
        reviews=tuple(r for r in reviews if isinstance(r, dict)),
        comments=tuple(c for c in comments if isinstance(c, dict)),
    )


def main(argv: Sequence[str] | None = None, *, run: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the review a pull request needs.")
    parser.add_argument("pr", type=int, help="pull request number")
    parser.add_argument("--repo", "-R", default="0xJ1nn/ezchain", help="owner/repository")
    parser.add_argument(
        "--diff",
        default="-",
        help="file of changed paths, one per line (default: stdin, as `git diff --name-only`)",
    )
    parser.add_argument("--json", action="store_true", help="print the machine-readable verdict")
    args = parser.parse_args(argv)

    if args.diff == "-":
        paths = read_paths(sys.stdin)
    else:
        try:
            paths = read_paths(Path(args.diff).read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            print(f"reviews: refused — cannot read the diff: {exc}", file=sys.stderr)
            return 1

    try:
        evidence = collect(args.repo, args.pr, run=run)
    except EvidenceError as exc:
        print(f"reviews: refused — {exc}", file=sys.stderr)
        return 1

    verdict = evaluate(evidence, paths)
    if args.json:
        print(
            json.dumps(
                {
                    "pr": args.pr,
                    "repo": args.repo,
                    "money_path": list(verdict.money_path),
                    "required": verdict.required,
                    "approvers": [
                        {"reviewer": a.reviewer, "source": a.source} for a in verdict.approvers
                    ],
                    "satisfied": verdict.satisfied,
                },
                sort_keys=True,
            )
        )
    else:
        for line in verdict.report():
            print(f"reviews: {line}")
    return 0 if verdict.satisfied else 1


if __name__ == "__main__":
    sys.exit(main())
