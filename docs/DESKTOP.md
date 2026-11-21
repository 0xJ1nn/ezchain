# DESKTOP — ezCHAIN as a native app

ezCHAIN ships as a native desktop app, not a browser tab and not a public web server.
The Electron shell supervises two private sidecars:

```
native window (Electron)
  ├─ Next.js server        127.0.0.1:<ephemeral>   UI
  └─ FastAPI paper API     127.0.0.1:<ephemeral>   SQLite at ~/.config/ezCHAIN/ezchain.db
```

Both sidecars bind to loopback only. The renderer never receives the API token: the Next
server injects it server-side, exactly like the web deployment. Closing the window hides
it to the tray; the API, strategy scheduler and market monitor keep running. Quit stops
them. A watchdog restarts either sidecar if it exits.

## Install (Linux)

The build produces a
[fuse-hardened](https://www.electronjs.org/docs/latest/tutorial/fuses) binary: the packaged
app refuses `ELECTRON_RUN_AS_NODE`, `NODE_OPTIONS` and `--inspect`, and loads only from its
`app.asar`. `tools/build-desktop.sh` verifies those fuses by reading the packaged binary
back, so a build that did not apply them fails instead of shipping.

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

## Build from source (Linux)

Prerequisites: Node 22+, pnpm 9, Python 3.11, PyInstaller in the project venv, and the Rust
toolchain (the build compiles the PyO3 bridge before freezing the sidecar).

```bash
pnpm install
.venv/bin/pip install pyinstaller
bash tools/build-desktop.sh            # unpacked build in apps/desktop/dist/linux-unpacked
bash tools/build-desktop.sh --package  # AppImage + .deb
bash tools/build-desktop.sh --check    # prove the frozen sidecar bundles the bridge
```

The build fails, rather than shipping, when either native prerequisite is missing: step 1
builds `ezchain_bridge` and step 5 loads it out of the frozen tree and calls
`quantize_money`, because the sidecar imports the extension dynamically and PyInstaller's
static analysis cannot see it. Step 7 reads the Electron fuse wire back. `--check` runs only
that bundle probe against an existing freeze.

Launch the unpacked build during development:

```bash
pnpm --filter @ezchain/desktop exec electron .
```

## Data, logs and recovery

| Path | Contents |
|---|---|
| `~/.config/ezCHAIN/ezchain.db` | paper ledger, orders, strategies, snapshots (SQLite, WAL) |
| `~/.config/ezCHAIN/api-token` | random 64-hex deployment token, mode 0600 |
| `~/.config/ezCHAIN/logs/desktop.log` | supervisor and both sidecars, rotated at 5 MiB |
| `~/.config/ezCHAIN/runtime/web-<version>/` | extracted Next runtime (versioned) |

Tray menu: Open, Restart services, Open logs, Open data folder, Quit. On macOS/Windows it
also offers Start at login; Linux XDG autostart is not implemented yet.
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

- No public listener; both sidecars bind `127.0.0.1` on ephemeral ports.
- The token is generated locally, stored 0600, and injected by the Next server only.
- No Node integration in the renderer; `contextIsolation` and sandbox are on when the OS
  permits Chromium's sandbox. Electron-builder's AppImage launcher may add `--no-sandbox`
  on hosts without unprivileged user namespaces.
- **One content-security policy for both surfaces.** `apps/web/lib/csp.ts` defines the
  policy; `next.config.ts` sends it as a response header on every route, and the shell
  applies the same string through `session.webRequest.onHeadersReceived` in `main.cjs`, so a
  response is covered even if the Next copy were compiled out. Scripts are `'self'` plus
  `'unsafe-inline'` (the App Router renders its bootstrap and RSC payload as inline
  scripts); production never allows `'unsafe-eval'`. `apps/desktop/test/sender-validation.mjs`
  fails if the two copies drift.
- **Privileged IPC is bound to the app's own top frame.** Every handler goes through
  `handleFromApp`, which validates `event.senderFrame` with `validateSenderFrame` — not
  `event.sender` — and refuses a null frame, a subframe, another origin, or a same-origin
  frame whose URL moved off-origin.
- **Fuses are flipped and then read back.** `apps/desktop/package.json` disables
  `RunAsNode`, `EnableNodeOptionsEnvironmentVariable` and `EnableNodeCliInspectArguments`, and
  enables `OnlyLoadAppFromAsar`; `tools/build-desktop.sh` re-reads the packaged binary's fuse
  wire and fails the build if a fuse did not apply. Consequence: the web sidecar cannot be a
  second copy of the Electron binary started with `ELECTRON_RUN_AS_NODE=1` (measured on
  Electron 45.0.0: that child exits 1 and serves nothing), so it runs as an Electron
  **utility process** (`utilityProcess.fork`), which is a real Node runtime the app parents
  and reaps. `apps/desktop/test/sidecar-wiring.mjs` fails if `main.cjs` ever spawns
  `process.execPath` again.
- External links open in the system browser; navigation outside the local origin is
  blocked. Camera, microphone, geolocation, notifications and clipboard reads are denied by
  permission handlers.
- There are no keys, signing or live execution in this build. `live_execute` is false.

## Verification

See [docs/evidence/desktop/linux-app.md](evidence/desktop/linux-app.md) for the launch,
paper flow, persistence and crash-recovery record.
