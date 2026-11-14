"""The review gate, decided from a recorded thread and diff — no network.

Every case feeds a saved ``gh pr view --json`` document to the checker, so the decision is
reproducible and each refusal is a test rather than a hope.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from tools.check_reviews import (
    MONEY_PATHS,
    Evidence,
    EvidenceError,
    Verdict,
    collect,
    evaluate,
    main,
    money_path,
)

MONEY = ["packages/core-py/src/ezchain/core/ledger.py"]
OTHER = ["apps/web/src/app/page.tsx"]


def review(login: str, state: str = "APPROVED", *, bot: bool = False) -> dict[str, object]:
    return {"author": {"login": login, "is_bot": bot}, "state": state}


def comment(
    login: str, body: str = "approved", assoc: str = "OWNER", *, bot: bool = False
) -> dict[str, object]:
    return {
        "author": {"login": login, "is_bot": bot},
        "authorAssociation": assoc,
        "body": body,
    }


def thread(
    reviews: Sequence[dict[str, object]] = (),
    comments: Sequence[dict[str, object]] = (),
) -> str:
    return json.dumps({"reviews": list(reviews), "comments": list(comments)})


def verdict(
    reviews: Sequence[dict[str, object]] = (),
    comments: Sequence[dict[str, object]] = (),
    paths: Sequence[str] = MONEY,
) -> Verdict:
    document = thread(reviews, comments)
    return evaluate(collect("0xJ1nn/ezchain", 1, run=lambda argv: document), paths)


@pytest.mark.parametrize("prefix", MONEY_PATHS)
def test_every_protected_prefix_is_money_path(prefix: str) -> None:
    assert money_path([f"{prefix}some/file.py"]) == (prefix,)


@pytest.mark.parametrize(
    "path",
    [
        "apps/web/src/app/page.tsx",
        "docs/WORKFLOW.md",
        "apps/api/src/ezchain/api/routes.py",
        "packages/ui/button.tsx",
        "packages/adapters",
    ],
)
def test_a_path_outside_the_prefixes_is_not_money_path(path: str) -> None:
    assert money_path([path]) == ()


def test_a_money_diff_needs_two_and_another_diff_needs_one() -> None:
    evidence = Evidence()

    assert evaluate(evidence, MONEY).required == 2
    assert evaluate(evidence, OTHER).required == 1


def test_zero_approvals_is_refused() -> None:
    result = verdict()

    assert result.approvers == ()
    assert not result.satisfied
    assert "refused — 0 of 2" in " ".join(result.report())


def test_one_approval_fails_a_money_diff_and_passes_another() -> None:
    reviews = [review("reviewer-a")]

    assert not verdict(reviews).satisfied
    assert verdict(reviews, paths=OTHER).satisfied


def test_two_distinct_approvers_satisfy_a_money_diff() -> None:
    result = verdict([review("reviewer-a"), review("reviewer-b")])

    assert result.satisfied
    assert [a.reviewer for a in result.approvers] == ["reviewer-a", "reviewer-b"]


def test_the_same_reviewer_twice_is_one_approver() -> None:
    result = verdict([review("reviewer-a"), review("reviewer-a")], [comment("reviewer-a")])

    assert [a.reviewer for a in result.approvers] == ["reviewer-a"]
    assert not result.satisfied


def test_a_maintainer_comment_counts_once_however_many_it_says_approved() -> None:
    result = verdict(comments=[comment("0xJ1nn"), comment("0xJ1nn", body="approved again")])

    assert [a.source for a in result.approvers] == ["comment"]
    assert not result.satisfied


def test_a_comment_and_a_review_by_one_login_are_still_one_approver() -> None:
    assert len(verdict([review("reviewer-a")], [comment("reviewer-a")]).approvers) == 1


def test_two_maintainer_comments_satisfy_a_money_diff() -> None:
    assert verdict(comments=[comment("reviewer-a"), comment("reviewer-b")]).satisfied


@pytest.mark.parametrize(
    "body",
    [
        "not approved yet",
        "I read the diff and it is fine",
        "requested changes: approved flows still leak",
        "**approved**",
        "looks good, approved",
    ],
)
def test_a_comment_that_does_not_begin_a_line_with_approved_does_not_count(body: str) -> None:
    assert verdict(comments=[comment("reviewer-a", body=body)]).approvers == ()


def test_an_approved_line_inside_a_longer_comment_counts() -> None:
    body = "Read it.\n\napproved\n"

    assert len(verdict(comments=[comment("reviewer-a", body=body)]).approvers) == 1


def test_a_comment_without_a_maintainer_association_does_not_count() -> None:
    assert verdict(comments=[comment("stranger", assoc="NONE")]).approvers == ()


def test_a_bot_approval_never_counts() -> None:
    result = verdict(
        [review("reviewer-a"), review("automation[bot]", bot=True)],
        [comment("codex-connector", bot=True)],
    )

    assert [a.reviewer for a in result.approvers] == ["reviewer-a"]
    assert not result.satisfied


def test_a_changes_requested_or_dismissed_review_does_not_count() -> None:
    reviews = [review("reviewer-a", "CHANGES_REQUESTED"), review("reviewer-b", "DISMISSED")]

    assert verdict(reviews).approvers == ()


def test_an_unreadable_thread_is_refused_not_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changed = tmp_path / "changed.txt"
    changed.write_text("\n".join(MONEY) + "\n", encoding="utf-8")

    def broken(argv: Sequence[str]) -> str:
        raise EvidenceError("gh pr view failed: HTTP 503")

    with pytest.raises(EvidenceError, match="503"):
        collect("0xJ1nn/ezchain", 1, run=broken)

    code = main(["1", "--diff", str(changed)], run=broken)

    assert code == 1
    assert "refused" in capsys.readouterr().err


def test_malformed_json_is_refused() -> None:
    with pytest.raises(EvidenceError, match="no usable JSON"):
        collect("0xJ1nn/ezchain", 1, run=lambda argv: "not json")


def test_a_document_without_reviews_and_comments_is_refused() -> None:
    with pytest.raises(EvidenceError, match="without reviews and comments"):
        collect("0xJ1nn/ezchain", 1, run=lambda argv: json.dumps({"author": {"login": "x"}}))


def test_the_cli_reads_the_diff_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    document = thread([review("reviewer-a"), review("reviewer-b")])
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(MONEY) + "\n"))

    code = main(["1", "--json"], run=lambda argv: document)

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["money_path"] == ["packages/core-py/"]
    assert payload["required"] == 2
    assert payload["satisfied"] is True


def test_the_cli_reads_the_diff_from_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changed = tmp_path / "changed.txt"
    changed.write_text("\n".join(OTHER) + "\n", encoding="utf-8")

    code = main(["1", "--diff", str(changed)], run=lambda argv: thread([review("reviewer-a")]))

    assert code == 0
    assert "no protected path" in capsys.readouterr().out


def test_the_cli_exits_one_when_a_money_diff_lacks_the_second_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changed = tmp_path / "changed.txt"
    changed.write_text("\n".join(MONEY) + "\n", encoding="utf-8")

    code = main(["1", "--diff", str(changed)], run=lambda argv: thread([review("reviewer-a")]))

    assert code == 1
    assert "refused — 1 of 2" in capsys.readouterr().out


def test_the_cli_refuses_an_unreadable_diff_file(tmp_path: Path) -> None:
    code = main(["1", "--diff", str(tmp_path / "absent.txt")])

    assert code == 1
