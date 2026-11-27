# Distribution sources

This directory holds the sources for self-hosted, unsigned 0.1.0 distribution.
GitHub Releases is the artifact source; every channel consumes the same artifact
names and SHA-256 files.

- `npm/cli` — the `@ezchain/cli` launcher. It declares one optional binary
  package per host and has no install script. `npm/cli/platform-map.cjs` is the
  single host matrix.
- `npm/cli-<os>-<arch>` — one binary package per host. Each declares its `os`
  and `cpu` and stages the native executable from a release artifact at
  publication. No binary is committed.
- `package-managers/artifacts.json` — the one release manifest. It names every
  artifact and the release URL base; it contains no checksum.
- `package-managers/templates` — Homebrew cask, winget and Scoop templates with
  placeholders only.

`tools/release/gen-package-manifests.mjs --artifacts <dir> --out <dir>` reads
the release manifest, computes one SHA-256 sidecar per artifact and renders all
three package-manager manifests plus a machine-readable summary. A missing
artifact fails the run before anything is written.

Run the checks with:

```bash
node --test distribution/npm/test/*.test.cjs
node --test distribution/package-managers/test/*.test.cjs
node --test apps/desktop/installer/*.test.cjs
node --test tools/release/test/*.test.cjs
```

`tools/package-desktop.sh` selects a desktop package target and refuses ones
the host cannot build. `tools/release/build-release-images.sh` builds both
shipped images from a named release candidate with optional local health smoke
checks. Neither script publishes, pushes, signs or creates a tag. See
[docs/FIRST-OPEN.md](../docs/FIRST-OPEN.md) and
[docs/RELEASES.md](../docs/RELEASES.md).
