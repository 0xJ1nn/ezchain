# DESKTOP — ezCHAIN as a native app

ezCHAIN ships as a native desktop app, not a browser tab and not a public web server.
There is one product and two roles, chosen on first launch. A **host** runs the paper API,
the strategy engine and the ledger. A **shell** is the window: it either starts a local
host or attaches read-only to one the operator owns.

As a host, the Electron shell supervises two private sidecars:

```
native window (Electron)
  ├─ Next.js server        127.0.0.1:<ephemeral>   UI
  └─ FastAPI paper API     127.0.0.1:<ephemeral>   SQLite at ~/.config/ezCHAIN/ezchain.db
```

Both sidecars bind to loopback only. The renderer never receives the API token: the Next
server injects it server-side, exactly like the web deployment. Closing the window hides
it to the tray; the API, strategy scheduler and market monitor keep running. Quit stops
them. A watchdog restarts either sidecar if it exits. A shell that attaches remotely runs
neither sidecar, holds neither token nor key, and owns no process that a disconnect could
stop.

## First-run setup

On first launch the app opens a setup screen; nothing is configured or started until the
operator finishes it. The screen decides and then displays:

- **Mode** — `PAPER`. This release offers no live mode and creates no key material. The API
  is started with `EZCHAIN_EXECUTION_MODE=paper` from the same setup, so the mode shown on
  screen and the mode the API enforces cannot disagree.
- **Data directory** — the platform's application data directory, `~/.config/ezCHAIN` on
  Linux, by default. The resolved absolute path is shown, not implied. The database and the
  deployment token live here.
- **Deployment token** — generated at setup for a local host, written with mode `0600`, and
  reported only as `stored at <path>`. Its value is never rendered in a window, never
  written to a log line and never stored in the repository; the screen says so rather than
  promising a reveal that never happens.
- **Backup path** — a `backups` directory inside the data directory by default. A custom
  path must already exist and be writable; a bad one is refused at setup with the reason.
  **Back up now** takes a consistent SQLite snapshot with `VACUUM INTO`, checks its integrity
  before reporting the file and its size, and restores by replacing `ezchain.db`.
- **Connection** — this machine (default) or a host URL. A remote URL is probed over HTTPS
  before it is saved.

Every value has one home: `host.json` in the application data directory is what the screen
writes and what the supervisor reads on every start. The database URL, backup directory and
execution mode handed to the API are derived from that same file, so the paths on screen are
the paths in use. Setup can be reopened from the tray or the application menu; re-running
it keeps the ledger, because no database file is deleted or recreated. Re-applying a local
setup first stops the sidecars and watchdog it already started, so a second host is never
left running beside the first.

## Install (Linux)

```bash
# AppImage — no install (requires FUSE 2)
chmod +x ezCHAIN-0.1.0-x86_64.AppImage
./ezCHAIN-0.1.0-x86_64.AppImage

# If FUSE is unavailable (common on Arch/Omarchy):
APPIMAGE_EXTRACT_AND_RUN=1 ./ezCHAIN-0.1.0-x86_64.AppImage
# or run the unpacked build directly:
apps/desktop/dist/linux-unpacked/ezchain

# Debian/Ubuntu (package built; install flow not exercised on the Arch build host)
sudo apt install ./ezCHAIN-0.1.0-amd64.deb
ezchain
```

The app opens a native window (the desktop environment may tile it), shows the dashboard,
and uses real market data in `PAPER` mode. It does not require Docker, Postgres or a separately started API.

## Connect to a remote host

On the first-run screen the app asks which role it should play. **Use this machine** keeps
the behaviour above and needs no configuration. **Connect to a host** asks for the host's
HTTPS address. The address is probed before it is saved: the app makes a read-only request
to the host's health endpoint over TLS, and only a successful response stores the setting.
If the host is unreachable the chooser stays open with the reason and a retry button; it
never opens an empty window. The saved role and address live in the application data
directory, outside the repository. A fresh profile shows the chooser again.

A host built by the native installer serves the same workstation behind TLS. The remote
window is not that host's web app. It is a local read-only view whose only data channel is
a request bridge with three hard limits: it sends `GET` or `HEAD` only, it accepts only an
allowlist of read paths (health, agent state and capabilities, paper account, positions,
orders, fills, ledger and alerts, and kill-switch state), and it drops every credential
header before a request leaves the process. The remote shell generates no deployment token,
reads no local ledger and holds no venue key. There is no order form in the view, so an
order can only be created on the host, by the operator, through the host's own approval
path. These limits live in the desktop code and are pinned by unit tests; they are not a
promise in prose.

TLS is required because a read-only view fetched over plain HTTP could be rewritten in
transit. Host URLs must be absolute `https://` addresses with no embedded username or
password; self-signed certificates are not a shortcut the app bypasses. The trust model is
the one in [REMOTE SURFACES](REMOTE-SURFACES.md): one origin you own is one trusted
workspace, and a remote client is a view, not a signer. The host keeps the deployment
credential server-side and decides whether reads need scoped authentication. Granting read
access to that host is granting access to the same paper account, so keep the origin
private and do not share the address as if it were a login.

The strategy engine runs on the host. The client starts no process there and sends no
shutdown when it closes or when the laptop sleeps, so it has no mechanism to stop a host
strategy; this is a design property, not a measured result on this machine. When the
connection returns, the view reloads the host's read models; a host that cannot be reached
shows a retry state instead of stale numbers.

## Build from source (Linux)

Prerequisites: Node 22+, pnpm 9, Python 3.11, PyInstaller in the project venv.

```bash
pnpm install
.venv/bin/pip install pyinstaller
bash tools/build-desktop.sh            # unpacked build in apps/desktop/dist/linux-unpacked
bash tools/build-desktop.sh --package  # AppImage + .deb
```

Launch the unpacked build during development:

```bash
pnpm --filter @ezchain/desktop exec electron .
```

## Data, logs and recovery

Paths below are the defaults. Setup can move the database, token and backups; `host.json`
and the logs stay in the application data directory so a fresh profile always knows where
to find its configuration.

| Path | Contents |
|---|---|
| `<data>/ezchain.db` | paper ledger, orders, strategies (SQLite, WAL) |
| `<data>/api-token` | random 64-hex deployment token, mode 0600, local host only |
| `<data>/backups/ezchain-<timestamp>.db` | consistent SQLite snapshots from Back up now |
| `<data>/runtime/web-<version>/` | extracted Next runtime (versioned) |
| `~/.config/ezCHAIN/host.json` | mode, data directory, backup path, role and remote address, mode 0600 |
| `~/.config/ezCHAIN/logs/desktop.log` | supervisor and both sidecars, rotated at 5 MiB |

Tray menu: Open, Restart services (Reconnect to host when attached remotely), Open logs,
Open data folder, Setup, Quit. On macOS/Windows it also offers Start at login; Linux XDG
autostart is not implemented yet.
The app requests a single instance; launching it again focuses the existing window.
On a sidecar crash the supervisor restarts it on the same port; a renderer crash reloads
the window. The database is the durable state; restarting the app preserves positions.

## Cross-platform

The shell and sidecar contract are platform-neutral (Electron + PyInstaller + Next
standalone). Linux, macOS and Windows package targets are **configured** in
`apps/desktop/package.json`; the macOS and Windows configurations are unsigned by
design and produce a `.dmg`/`.zip` and a `.exe` installer respectively. The
frozen sidecar's name follows the host executable convention, so the supervisor
resolves `ezchain-api.exe` on Windows without a special case at the call site.

`tools/package-desktop.sh --target <linux|mac|win|all>` refuses any target that
cannot be built on the current host before it starts work, and records that
target as not built and not verified. macOS and Windows were **not built or
launched** on the build host for this branch; each needs its own PyInstaller
backend binary and an Electron build on that host. No signing, notarization or
store approval is configured or claimed. See
[docs/FIRST-OPEN.md](FIRST-OPEN.md) for install paths, the paper-only warning and
the data/log locations, and [docs/RELEASES.md](RELEASES.md) for the status
vocabulary.

## Security

- No public listener; as a host, both sidecars bind `127.0.0.1` on ephemeral ports.
- The token is generated locally only for a local host, stored 0600, and injected by the
  Next server only. A remote shell never generates, stores or transmits it.
- A remote shell sends only `GET`/`HEAD` to an allowlist of read paths, drops credential
  headers, and renders a local page with no order form. Approvals and execution stay on
  the host.
- No Node integration in the renderer; `contextIsolation` and sandbox are on when the OS
  permits Chromium's sandbox. Electron-builder's AppImage launcher may add `--no-sandbox`
  on hosts without unprivileged user namespaces.
- Only the setup window receives setup controls. The dashboard and remote views get no
  `finishSetup` or `backupNow` call, and the supervisor accepts a setup change only from
  the setup window's own renderer, so a script on the loaded page cannot stop the host,
  move its data or re-point the view.
- External links open in the system browser; navigation outside the local origin is
  blocked. A remote view cannot navigate to the host's web app.
- There are no keys, signing or live execution in this build. The setup screen offers
  `PAPER` only and the API is started with `EZCHAIN_EXECUTION_MODE=paper`; `live_execute` is
  false.
- The deployment token value is never logged. The screen reports where it is stored, and
  the summary it renders contains no secret.

## Verification

The setup decisions are pinned by the desktop unit tests: they assert that the displayed
paths are the paths handed to the API, that a backup is a consistent copy which restores,
that a bad backup path is refused at setup with its reason, and that no secret reaches a
summary, a page or a log line. They also cover the local to local re-run teardown, the
setup-window-only bridge and the integrity check on the written snapshot.

See [docs/evidence/desktop/linux-app.md](evidence/desktop/linux-app.md) for the launch,
paper flow, persistence and crash-recovery record.
