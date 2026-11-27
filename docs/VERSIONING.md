# Versioning

ezCHAIN has **one product version**. It lives in the repository-root [`VERSION`](../VERSION)
file and nowhere else. Today it is `0.1.0`.

## The rule

`VERSION` is the only source of truth for the version of the product the user is running.
Every user-visible surface reads it: `GET /health` and `GET /healthz` return it, OpenAPI
carries it, and the web footer renders it from a value injected at build time. A missing,
unreadable or malformed file fails loudly at startup or at build time; there is no fallback
and no guessed version.

Both readers accept `EZCHAIN_VERSION_FILE` as an explicit path to the file. That override
points at the same file — it never introduces a second value.

Package versions are independent of the product version. A package may carry a different
number only when the package declares its own version and this document says so:

| Manifest | Version | Why |
|---|---|---|
| `packages/core-py/pyproject.toml` | `0.1.0` | Tracks the product version explicitly. |
| `packages/adapters/pyproject.toml` | `0.1.0` | Tracks the product version explicitly. |
| `packages/sdk-py/pyproject.toml` | `1.0.0` | The strategy SDK's own package version; not the product version. |
| `apps/web/package.json` | `0.0.0` | Private app placeholder; the product version comes from `VERSION`, injected at build time. |

The root `pyproject.toml` declares no product version at all: it configures tooling, not a
distributable package. Other internal manifests may pin their own versions, but only the
four above are part of the product-version agreement; changing any of them without the
others is a test failure.

## Enforcement

[`tests/api/test_version_agreement.py`](../tests/api/test_version_agreement.py) parses
`VERSION`, both health probes, the web reader and every manifest listed above. A change to
one side without the others fails the suite.
