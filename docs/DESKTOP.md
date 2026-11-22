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

On first launch the app asks which role it should play. **Use this machine** keeps the
behaviour above and needs no configuration. **Connect to a remote host** asks for the host's
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

The strategy engine runs on the host. Closing the client window or putting the laptop to
sleep does not stop it: the shell starts no sidecar and sends no shutdown when it leaves.
When the connection returns, the view reloads the host's read models and shows their source
and age; a host that cannot be reached shows a retry state instead of stale numbers.

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

| Path | Contents |
|---|---|
| `~/.config/ezCHAIN/ezchain.db` | paper ledger, orders, strategies, snapshots (SQLite, WAL) |
| `~/.config/ezCHAIN/api-token` | random 64-hex deployment token, mode 0600, local host only |
| `~/.config/ezCHAIN/host.json` | chosen role and remote HTTPS address, mode 0600 |
| `~/.config/ezCHAIN/logs/desktop.log` | supervisor and both sidecars, rotated at 5 MiB |
| `~/.config/ezCHAIN/runtime/web-<version>/` | extracted Next runtime (versioned) |

Tray menu: Open, Restart services (Reconnect to host when attached remotely), Open logs,
Open data folder, Quit. On macOS/Windows it also offers Start at login; Linux XDG autostart
is not implemented yet.
The app requests a single instance; launching it again focuses the existing window.
On a sidecar crash the supervisor restarts it on the same port; a renderer crash reloads
the window. The database is the durable state; restarting the app preserves positions.

## Cross-platform

The shell and sidecar contract are platform-neutral (Electron + PyInstaller + Next
standalone), but this branch builds Linux only. macOS and Windows are **not built or
verified** on this machine; each needs its own PyInstaller backend binary and an Electron
build/notarization setup on that host. Do not claim those targets until a build and launch
are recorded.

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
- External links open in the system browser; navigation outside the local origin is
  blocked. A remote view cannot navigate to the host's web app.
- There are no keys, signing or live execution in this build. `live_execute` is false.

## Verification

See [docs/evidence/desktop/linux-app.md](evidence/desktop/linux-app.md) for the launch,
paper flow, persistence and crash-recovery record.
