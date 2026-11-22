# RELEASES — building, signing and verifying a release

Official releases are tagged builds from the canonical repository. Anyone can check that a
download is one of them. The private half of the signing key belongs to the maintainer and
never enters the repository; this document records the other half of the arrangement: what
is signed, where the public key goes and how a reader verifies a download.

## 1. The key

The maintainer creates one release key pair and keeps the private half outside the checkout,
in a store that is not committed and not copied into a build or a fixture. A source checkout
ships no key material. The public half is published by the maintainer at
`tools/release/official-public.pem` in the official release, and its fingerprint belongs in
the release notes so a reader can tell a substituted key apart from the published one.

Creating or rotating the key is a maintainer action. Scripts that sign hand the private key
to OpenSSL through `EZCHAIN_SIGNING_KEY`, name the artifact they are about to sign and
require an explicit confirmation. No script prints the key or its path into a log.

## 2. What gets signed

Three artifacts carry a signature file named after them with `.sig` appended:

- the release archive's checksum file, `ezchain-<tag>.tar.gz.sha256`, which covers the
  archive the installer downloads;
- the funding configuration published with the release, `config/funding.json`, so a reader
  can check donation details against the signed file instead of trusting a copied one;
- the build attestation, `release/attestation.json`, which the running application checks
  for the Settings → General provenance verdict.

The archive itself is built by `tools/install/package-release.sh --tag <tag> --sign`. The
`--sign` flag signs the checksum file; it requires `EZCHAIN_SIGNING_KEY` and never reads a
key from the checkout. Sign the funding configuration and the attestation with
`tools/release/sign.sh --yes <file>` when they are ready for the tag. Publishing the
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

The flag is true only when the attestation's signature checks against the public key and the
attestation names the canonical repository. A missing key, a missing attestation, a missing
signature, a malformed record, a changed signature, a repository mismatch and an unavailable
verifier each produce a distinct reason. Settings → General renders the verdict; it shows a
positive badge only for a verified build and states the reason in every other case.
Verification never returns key material or a signature to the browser.

## 5. Development and tests

The release tests generate a throwaway key pair in a temporary directory for each run and
exercise the scripts through the shell. No test fixture carries a private key, and no
private key is committed, printed or placed in a fixture. A test key can verify the sign and
verify round trip and its failure paths; it cannot make a build official, because the
published public half is the maintainer's to distribute.
