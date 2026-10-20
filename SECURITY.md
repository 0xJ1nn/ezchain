# Security policy

## Reporting

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability
reporting (Security → Report a vulnerability) or email the maintainer listed in the
repository profile. Include: affected version/commit, impact, reproduction, and whether
funds or keys are at risk.

We aim to acknowledge within 72 hours and to ship a fix or mitigation within 30 days for
critical issues. A 90-day disclosure window applies by default; we will credit reporters
who want credit.

## In scope

- Key custody/vault, executor signing boundary, replay/double-spend.
- Authentication, session, API keys, RBAC, CSRF, SSRF, injection.
- Ledger integrity, risk bypass, mode confusion (paper vs live).
- Plugin sandbox escape, supply chain (dependency/image), secret leakage in logs.
- Chain/venue adapter issues that can cause loss (quote manipulation, route identity).

## Out of scope

- Losing money on a strategy you chose. That is a market outcome, not a vulnerability.
- A compromised host with an unlocked vault.
- Social engineering, physical access, denial of service without a code defect.
- Findings from an automated scanner without a demonstrated impact.

## Safe harbor

Good-faith research on your own instance is welcome. Do not test against other users'
deployments, do not exfiltrate data, and stop as soon as you confirm a vulnerability.
