"""Pure trust-boundary kernels: intent digest, decode-and-bind, preflight.

Design note. These modules implement the *checks*, not the signer. They establish what
was approved (:class:`Intent` and its domain-separated digest), prove that the bytes an
adapter produced are exactly that approval (:func:`decode_and_bind`), and run the 16
ordered pre-broadcast checks (:func:`run_preflight`). They deliberately do not verify a
signature — that is the signer's proof, not the approval's identity; do not judge a
venue — a venue supplies bytes, never authority; and do no money arithmetic — decimals
are parsed and compared exactly, never computed. There are no keys, no network and no
database imports in this package.

The signer boundary (not wired in this change) will: digest the approval, pass the
approval and nothing else to the adapter, decode and bind the returned bytes, run
preflight with caller-read facts, and only on a clear path call the signer with those
bound bytes. Any mismatch returns a typed reason code from
:mod:`ezchain_core.reason_codes`, and every failure path stops before ``sign``.
"""

from __future__ import annotations

from .attestation import (
    TRANSACTION_SCHEMA,
    BindingRefused,
    DecodedFields,
    TransactionDecodeError,
    decode_and_bind,
    decode_transaction,
    encode_transaction,
    payload_digest,
    verify_binding,
)
from .intent import INTENT_DOMAIN, INTENT_SALT, Intent
from .preflight import CHECKS, PreflightCheck, PreflightFacts, PreflightInput, run_preflight
from .types import BINDING_FIELDS, Refusal, canonical_decimal, parse_exact_decimal

__all__ = [
    "BINDING_FIELDS",
    "CHECKS",
    "INTENT_DOMAIN",
    "INTENT_SALT",
    "TRANSACTION_SCHEMA",
    "BindingRefused",
    "DecodedFields",
    "Intent",
    "PreflightCheck",
    "PreflightFacts",
    "PreflightInput",
    "Refusal",
    "TransactionDecodeError",
    "canonical_decimal",
    "decode_and_bind",
    "decode_transaction",
    "encode_transaction",
    "parse_exact_decimal",
    "payload_digest",
    "run_preflight",
    "verify_binding",
]
