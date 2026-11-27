# RELEASES — building, signing and verifying a release

Official releases are tagged builds from the canonical repository. Anyone can check that a
download is one of them. The private half of the signing key belongs to the maintainer and
never enters the repository; this document records the other half of the arrangement: what
is signed, where the public key goes and how a reader verifies a download.

Sections 1–5 are the signing and verification contract: the key, the signed artifacts,
the reader's verification steps, the in-app provenance verdict and the tests. Section 6 is
the version workflow — milestones, the release issue, the gate and carry-forward. Release
notes for each version live in `docs/release-notes/`.

## 1. The key

The maintainer creates one release key pair and keeps the private half outside the checkout,
in a store that is not committed and not copied into a build or a fixture. A source checkout
ships no key material. The public half is published by the maintainer at
`tools/release/official-public.pem` in the official release, and its fingerprint belongs in
the release notes so a reader can tell a substituted key apart from the published one. The
fingerprint is the SHA-256 of the DER-encoded public key; a reader can reproduce it with
`openssl pkey -pubin -in tools/release/official-public.pem -outform DER | openssl dgst -sha256`.
The app does not fetch or guess this value: the operator pins it from the release notes.

Creating or rotating the key is a maintainer action. Scripts that sign hand the private key
to OpenSSL through `EZCHAIN_SIGNING_KEY`, name the artifact they are about to sign and
require an explicit confirmation. No script prints the key or its path into a log.

## 2. What gets signed

Three artifacts carry a signature file named after them with `.sig` appended:

- the release archive's checksum file, `ezchain-<tag>.tar.gz.sha256`, which covers the
  archive the installer downloads;
- the funding configuration the maintainer produces and publishes with the tag,
  `config/funding.json`, so a reader can check donation details against the signed file
  instead of trusting a copied one. It is a release asset, not a file in a source checkout;
- the build attestation, `release/attestation.json`, produced with the tag and checked by the
  running application for the Settings → General provenance verdict.

The archive itself is built by `tools/install/package-release.sh --tag <tag> --sign`. The
`--sign` flag signs the checksum file; it requires `EZCHAIN_SIGNING_KEY` and never reads a
key from the checkout. Sign the funding configuration and the attestation with
`tools/release/sign.sh --yes <file>` once they have been produced for the tag. Publishing the
archive, its checksum, its signature, the signed funding configuration and the attestation
together is what makes a set of assets an official release.

The attestation is a small JSON record with two string fields: `repository`, the canonical
repository URL, and `tag`, the release tag. A signature over it is the only way a build can
claim to be official; the URL on its own proves nothing.

## 3. How a reader verifies

`tools/release/verify.sh` checks one or more artifacts against the published public key and
exits non-zero if any of them fails. It prints only the artifact name and the verdict:

```bash
tools/release/verify.sh --public-key tools/release/official-public.pem \
  ezchain-<tag>.tar.gz.sha256 \
  config/funding.json \
  release/attestation.json
```

For a downloaded archive, check the checksum first with `sha256sum -c` (or `shasum -a 256 -c`),
then check the checksum file's signature. A missing key, a missing signature and a changed
artifact all refuse; none of them is treated as a verified download. The installer
(`tools/install/install.sh`) checks the SHA-256 of every archive it downloads; the signature
check is the additional step described here.

## 4. The in-app provenance verdict

`GET /api/v1/system/build-source` reads a public key and an attestation from the running
installation and returns an `official` flag with a `status` and a `message` explaining the
result. The two paths come from `EZCHAIN_RELEASE_PUBLIC_KEY` and
`EZCHAIN_BUILD_ATTESTATION`; operators set absolute paths in a deployment. The defaults are
repository-relative, so a source checkout with no published key and no signed attestation
reports an unverified build.

A signature alone cannot authenticate the key it was made with: a fork can ship its own
keypair. The operator therefore pins the maintainer's published fingerprint in
`EZCHAIN_RELEASE_KEY_FINGERPRINT` at deployment time. That value is never committed, and with
no value set the endpoint fails closed. The flag is true only when the attestation's signature
checks against the public key, the installed key's fingerprint matches the pinned value and
the attestation names the canonical repository. A missing key, a missing attestation, a
missing signature, a malformed record, a changed signature, an unpinned key, a fingerprint
mismatch, a repository mismatch and an unavailable verifier each produce a distinct reason.
Settings → General renders the verdict; it shows a positive badge only for a verified build
and states the reason in every other case. Verification never returns key material, a
fingerprint or a signature to the browser.

## 5. Development and tests

The release tests generate a throwaway key pair in a temporary directory for each run and
exercise the scripts through the shell. No test fixture carries a private key or a real
release-key fingerprint, and neither is committed, printed or placed in a fixture. A test key
can verify the sign and verify round trip and its failure paths; it cannot make a build
official, because the fingerprint pinned at release time is the maintainer's to publish. A
A test that pins a deliberately invalid fingerprint proves a fork's own key is refused.

## Distribution channels for 0.1.0

The signing contract in sections 1–5 is the maintainer's release-authenticity
mechanism for the source archive, the funding configuration and the build
attestation. It is not code signing. The 0.1.0 desktop binaries are unsigned,
unnotarized and not store-approved, and no publisher identity is embedded in
them. Signing is revisited only when there is an entity to sign with.

GitHub Releases is the artifact source for every channel. One release manifest,
[`artifacts.json`](../distribution/package-managers/artifacts.json), names the
artifacts; `tools/release/gen-package-manifests.mjs` computes each SHA-256
sidecar from the artifact bytes and renders the Homebrew cask, winget and Scoop
manifests from those same digests. No channel carries a hand-maintained
checksum, and there is no second upload path.

- **npm.** `@ezchain/cli` is a launcher with five optional per-platform binary
  packages and no install script. The native binary is staged into a platform
  package from the release artifact at publication; the checkout carries no
  binary. Registry integrity is npm provenance plus the published per-platform
  SHA-256, not code signing.
- **Homebrew, winget and Scoop.** Their manifests are generated from the same
  release manifest and checksum files and point at the same release URLs.
  Publication of a tap or a package-manager repository is maintainer-only.
- **Docker.** `tools/release/build-release-images.sh` builds the API and web
  images from a named release candidate and can run local health smoke checks.
  It never pushes an image.
- **Local desktop.** `tools/package-desktop.sh` refuses a target the host cannot
  build rather than attempting a cross-build and calling it verified.

At publication the maintainer adds npm provenance and publishes the release
assets. Until then every channel is **configured**, not published: a locally
packed tarball has no provenance, and nothing here is claimed as signed,
notarized, store-approved or released. The vocabulary in
[FIRST-OPEN.md](FIRST-OPEN.md) keeps configured, built, launched and published
apart.

## 6. Version workflow

ezCHAIN follows Semantic Versioning. Version `0.1.0` is the first public release and the
only launch lane: it carries the complete product, including live trading with real money.
The version identifier stays `0.1.0`. Live execution is targeted by this release, not
claimed as working today: no build enables live mode until the readiness gate in
[LIVE-EXECUTION.md](LIVE-EXECUTION.md) is satisfied and the maintainer enables it.
Versions `0.2.0` and later are post-launch names kept as empty placeholders. The product
ships with no strategies. No dates are promised and the order of post-launch names may
change. See [CHANGELOG.md](../CHANGELOG.md) for the per-version change record and
[../ROADMAP.md](../ROADMAP.md) for the public roadmap.

### Versions

- `0.1.0` — the launch release: the complete product, including live execution behind the
  readiness gate. Release-defining gates: a clean-clone install, a real paper trade in the
  default gate, version agreement, a publication audit, the live readiness rollup, custody
  and key runbooks, and a venue rehearsal before live mode is enabled.
- `0.2.0`, `0.3.0`, `0.4.0`, `1.0.0` — post-launch version names, empty until work is added
  after the launch.

### Version source

The root `VERSION` file is the single source of the version string. The API health
endpoints and the web footer must agree with it. Agreement between the three is checked
on the release candidate and recorded in the release notes; until it is recorded,
version agreement is not a passed gate.

### One register per version

Each version has exactly one milestone titled with the version number, one label
`release/<version>`, one umbrella issue titled `Release <version>`, and one release
notes file in `docs/release-notes/`. The changelog lists every change in that version
with its issue or pull request and the evidence that it was checked on the release
build; an unfinished item moves to a post-launch version with a reason rather than being
dropped. The baseline for a version's diff is the previous release tag.

Decisions that belong to the maintainer rather than to a release are labelled
`owner-decision` and carry no version milestone.

### Branches

Day-to-day work uses short-lived feature branches based on the default branch, each
with its own review. Branches named `release/<version>` and `rc/<version>` are
reserved for the maintainer. No change is merged without a reviewed pull request.

### The gate

An integrated release candidate is prepared only after its changes are complete.
The full check suite runs once on the exact candidate commit; individual changes run
their targeted checks beforehand. The candidate gates are a clean-clone install, a real
paper trade inside the default gate, the publication audit, version agreement, a licence
scan, the live readiness rollup, custody and key runbooks, and a venue rehearsal before live
mode is enabled. Results are recorded as evidence on the release issue and in the release
notes. A check that has not run is reported as not run, never as passed.

### Carry-forward

Unfinished work is not discarded at a version boundary. It moves into a post-launch
version's record with its owner, issue and the reason it remains open, and the
release notes say what is still unverified.

### Publication

Only the maintainer creates tags and publishes releases. Distribution, legal and
payment steps are likewise maintainer-only. Live mode is not enabled by a published build
alone: it stays off until the readiness rollup in
[LIVE-EXECUTION.md](LIVE-EXECUTION.md) is recorded and the maintainer enables it.
