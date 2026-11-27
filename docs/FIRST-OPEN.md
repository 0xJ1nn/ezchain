# FIRST OPEN — ezCHAIN 0.1.0

This guide covers installing and opening the workstation on macOS, Windows and a
self-hosted Linux server. Read it before the first launch.

**Status.** Version 0.1.0 is paper-only and self-hosted. The packages and
manifests described here are configured and built from source; no release tag
has been published, and no binary has been signed, notarized or approved by an
app store. A locally packed artifact carries no npm provenance either: the
publisher adds provenance at publication time. Integrity comes from the
published SHA-256 digest and, for registry packages, npm provenance — not from
code signing. package managers carry updates; the app has no in-app updater.

**Real-money warning.** There is no live trading in this release. The app offers
`PAPER` only and starts its API with `EZCHAIN_EXECUTION_MODE=paper`. It creates
no wallet, asks for no seed phrase and holds no trading key. Do not point it at
funds and do not send money to any address it displays. Paper fills are not
executed trades. The project never hosts other people's keys, and nobody needs
access to a server that holds theirs.

## macOS

The native app is distributed unsigned. A Homebrew cask and the npm launcher
consume the same release artifacts:

```bash
# Homebrew cask from the project's tap (a tap is hosted by the maintainer)
brew tap 0xJ1nn/ezchain
brew install --cask ezchain

# Or the npm launcher, which selects a platform binary package automatically
npm install -g @ezchain/cli
ezchain
```

macOS may refuse to open an unsigned, unnotarized app. The operating system's
privacy and security settings decide whether to allow it; no notarization is
claimed here. If the download was altered, the SHA-256 check fails before the
app is opened.

The cask manifest and the npm platform packages are generated from the same
release manifest and checksum files. See
[distribution/package-managers](../distribution/package-managers/artifacts.json).
On the Apple-silicon build host the unsigned arm64 `.dmg` and `.zip` were built,
and the packaged sidecar answered its native-bridge smoke check. The graphical
window was not launched and the x64 variant was not built, so those are
**not verified**. No signature, notarization or store approval is claimed.

## Windows

winget and Scoop consume the same release artifact and SHA-256 file as the npm
Windows package. The manifests are generated, not hand-written:

```powershell
# winget, from a manifest hosted by the project
winget install --manifest 0xJ1nn.ezchain.yaml

# Scoop, from a bucket containing the generated manifest
scoop install ezchain

# Or the npm launcher
npm install -g @ezchain/cli
ezchain
```

SmartScreen may warn because the binary is unsigned. There is no publisher
identity in the binary and no Microsoft signing. The Windows package
configuration and artifact shape are checked by tests; a Windows build and
launch were not performed on the machine that prepared this guide, so they are
**not verified**.

## Linux and self-hosted server

The desktop app runs from an AppImage or a Debian package, and the server stack
runs from a release archive on a machine you own:

```bash
./ezCHAIN-0.1.0-x86_64.AppImage
# or
sudo apt install ./ezCHAIN-0.1.0-amd64.deb
```

For the server, download the release archive and its `.sha256` file, verify it
with `sha256sum -c`, then run the installer. The full trust model, firewall,
secrets, upgrades and backups are in [SELF-HOSTING](SELF-HOSTING.md). The paper
stack needs no broker account and no key material. See also
[DESKTOP](DESKTOP.md) for the shell's process model and recovery paths.

## First launch

On first launch the app opens a setup screen. Nothing is configured or started
until setup is finished. Choose:

- **Role** — use this machine as a local host, or connect read-only to a host
  you own. A remote address must be an absolute HTTPS URL and is probed before
  it is saved.
- **Mode** — `PAPER`, the only mode offered.
- **Data directory** — shown as an absolute path; the database and deployment
  token live there.
- **Deployment token** — generated for a local host, stored with mode `0600`,
  reported only by location. Its value is never shown in a window or written to
  a log.
- **Backup path** — a `backups` directory inside the data directory by default;
  a custom path must already exist and be writable.

Closing the window hides the app to the tray; the paper API, strategy scheduler
and market monitor keep running. Quit stops them. Opening the tray or menu
gives access to logs, the data folder, setup, service restart and, where the OS
supports it, start at login.

## Where logs and configuration live

The application data directory is the one home for configuration and logs. Its
default follows Electron's user-data path for the app name `ezCHAIN`:

| Platform | Application data directory |
|---|---|
| macOS | `~/Library/Application Support/ezCHAIN/` |
| Windows | `%APPDATA%\ezCHAIN\` |
| Linux | `~/.config/ezCHAIN/` |

The macOS and Windows locations follow the configured shell contract; only the
Linux path has a recorded launch on the build host.

| Path | Contents |
|---|---|
| `<data>/host.json` | mode, data directory, backup path, role and remote address (mode `0600`) |
| `<data>/logs/desktop.log` | supervisor and sidecar logs, rotated at 5 MiB |
| `<data>/ezchain.db` | paper ledger, orders and strategies (SQLite, WAL) |
| `<data>/api-token` | local deployment token, mode `0600`, local host only |
| `<data>/backups/` | consistent SQLite snapshots from Back up now |
| `<data>/runtime/web-<version>/` | extracted Next runtime |

A fresh profile shows setup again; re-running setup keeps the ledger because no
database file is deleted.

## Configured, built, launched, published

These are four different claims and this guide keeps them apart:

| Claim | What it means | State in this checkout |
|---|---|---|
| Configured | Build and package settings exist and are checked | npm packages, Homebrew/winget/Scoop manifests, Linux/macOS/Windows targets, Dockerfiles |
| Built | An artifact was produced by a command recorded in the evidence | npm tarballs from a packed-artifact smoke test, including a real macOS arm64 CLI built from the committed Cargo lock; unsigned macOS arm64 dmg and zip; the earlier Linux desktop build in [the evidence](evidence/desktop/linux-app.md) |
| Launched | A built artifact was started and answered its health checks | The packed macOS CLI answered `version`, `mode`, `help` and `gate` after a clean install; the app code opened its setup window under a development Electron with an isolated profile and a DevTools target list |
| Published | A tag, registry release, tap or package manager publication exists | None — tag, release and publication are maintainer-only and were not performed |

The read-only CLI reports the same `0.1.0` as the npm packages and the
workstation `VERSION` file; the packed-install check asserts that exact value.
The packaged macOS app could not be launched unattended on the build host:
macOS presented an interactive policy dialog before the main script loaded. Its
DevTools port opened but no renderer target appeared until that dialog is
approved by the operator. No GUI launch is claimed for the package.

No item here is claimed as signed, notarized, store-approved or published.
