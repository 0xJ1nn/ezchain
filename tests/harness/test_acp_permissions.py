"""Criterion (b): permission requests become approvals, and the agent gets no local tools.

An ACP agent can ask its client for permission and for file, shell and terminal access. The
harness is that client. These tests pin the two rules that keep those requests from becoming
a privilege path: a permission is granted only when the injected approval authority returns
a one-shot choice, and every local capability request is refused.
"""

from __future__ import annotations

import pytest

from ezchain_harness.acp import AcpClient, PermissionKind

from .acp_helpers import (
    FakeAgent,
    RecordingBroker,
    RefusingBroker,
    assert_secret_absent,
    capability_request,
    option,
    permission_request,
    start_and_prompt,
)

ONESHOT = option("allow-once", PermissionKind.ALLOW_ONCE.value, "Allow once")
STANDING = option("allow-always", PermissionKind.ALLOW_ALWAYS.value, "Always allow")
REJECT = option("reject-once", PermissionKind.REJECT_ONCE.value, "Reject")


def _answer_for(agent: FakeAgent, req_id: int) -> dict:
    return next(m for m in agent.answers() if m.get("id") == req_id)


def test_a_permission_request_is_delegated_to_the_approval_authority() -> None:
    broker = RecordingBroker([ONESHOT["optionId"]])
    attack = permission_request(900, [ONESHOT, REJECT])
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=broker)

    start_and_prompt(client, agent)

    assert len(broker.requests) == 1
    request = broker.requests[0]
    assert request.tool_name == "run_command"
    assert _answer_for(agent, 900)["result"] == {"optionId": ONESHOT["optionId"]}


def test_no_authority_decision_means_the_permission_is_denied() -> None:
    broker = RecordingBroker([])
    attack = permission_request(901, [ONESHOT, REJECT])
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=broker)

    start_and_prompt(client, agent)

    assert len(broker.requests) == 1
    answer = _answer_for(agent, 901)
    assert "optionId" not in answer.get("result", {})


@pytest.mark.parametrize(
    "attack",
    [
        capability_request(910, "fs/read_text_file", {"path": "/etc/passwd"}),
        capability_request(911, "fs/write_text_file", {"path": "/tmp/x"}),
        capability_request(912, "terminal/create", {"command": "id"}),
        capability_request(913, "terminal/wait_for_exit", {"terminalId": "1"}),
        capability_request(914, "fetch", {"url": "https://example.test"}),
    ],
)
def test_file_shell_and_network_capabilities_are_refused(attack) -> None:
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=RecordingBroker([ONESHOT["optionId"]]))

    start_and_prompt(client, agent)

    answer = _answer_for(agent, attack["id"])
    assert "error" in answer, "a local capability must never be granted"


def test_a_standing_grant_is_never_accepted_even_from_the_authority() -> None:
    broker = RecordingBroker([STANDING["optionId"]])
    attack = permission_request(920, [ONESHOT, STANDING, REJECT])
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=broker)

    start_and_prompt(client, agent)

    answer = _answer_for(agent, 920)
    assert "optionId" not in answer.get("result", {})


def test_the_authority_only_sees_one_shot_choices() -> None:
    broker = RecordingBroker([ONESHOT["optionId"]])
    attack = permission_request(921, [ONESHOT, STANDING, REJECT])
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=broker)

    start_and_prompt(client, agent)

    kinds = {opt.kind for opt in broker.requests[0].options}
    assert PermissionKind.ALLOW_ALWAYS not in kinds
    assert PermissionKind.REJECT_ALWAYS not in kinds


def test_an_unreachable_authority_fails_closed() -> None:
    broker = RefusingBroker()
    attack = permission_request(922, [ONESHOT, REJECT])
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=broker)

    start_and_prompt(client, agent)

    answer = _answer_for(agent, 922)
    assert "optionId" not in answer.get("result", {})


def test_no_credential_leaks_while_a_permission_is_resolved(monkeypatch) -> None:
    secret = "sk-agent-do-not-use-abcdef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    broker = RecordingBroker([ONESHOT["optionId"]])
    attack = permission_request(930, [ONESHOT, REJECT])
    agent = FakeAgent(attacks=[attack])
    client = AcpClient(agent, broker=broker)

    start_and_prompt(client, agent)

    assert_secret_absent(agent, secret)
