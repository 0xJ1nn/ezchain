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

## Install

The Linux install is the one that has been exercised; the macOS and Windows packages build
from the same source on their own host and are **not verified** here (see Cross-platform).

### Linux

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

## Build from source

Prerequisites: Node 22+, pnpm 9, Python 3.11, PyInstaller in the project venv, and the Rust
toolchain (the build compiles the PyO3 bridge before freezing the sidecar).

```bash
pnpm install
.venv/bin/pip install pyinstaller
bash tools/build-desktop.sh            # unpacked build for this host
bash tools/build-desktop.sh --package  # installers for this host
bash tools/build-desktop.sh --check    # prove the frozen sidecar bundles the bridge
bash tools/build-desktop.sh --target win   # refuse, with the reason, on a non-Windows host
```

`--target` exists to be honest: PyInstaller freezes the API sidecar into a host-native
executable and does not cross-compile, so a Windows package must be built on Windows and a
macOS package on macOS. On a mismatched host the script exits 2 before doing any work and
prints what that host can build instead of failing halfway through.

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

Tray menu: Open, Restart services, Open logs, Open data folder, Start at login, Quit. "Start
at login" uses `setLoginItemSettings` on macOS and Windows and the XDG autostart file
(`~/.config/autostart/ezchain.desktop`) on Linux, which is the half of "keeps running" that
survives a reboot. An autostart launch passes `--hidden` and stays in the tray rather than
opening a window on a machine nobody is sitting at.
The app requests a single instance; launching it again focuses the existing window.
On a sidecar crash the supervisor restarts it on the same port; a renderer crash reloads
the window. The database is the durable state; restarting the app preserves positions.

## Background behaviour and its limit

The window closing hides the app; the sidecars keep running and the strategies keep
scheduling. That is real, and it is also the whole promise: a desktop is not a server.

- **Sleep stops everything.** When the OS suspends, no process runs. The shell listens to
  `powerMonitor` and stops probing on `suspend` (a restart attempted while asleep only grows
  the backoff), then probes both sidecars immediately on `resume`/`unlock-screen` instead of
  waiting up to 15 s for the next watchdog tick.
- **Uptime is not a feature of the desktop build.** Selling reliable background execution is
  a separate hosted offering, and it is deliberately not part of this build; the native
  install (`docs/SELF-HOSTING-NATIVE.md`) on a machine that stays awake is the dependable
  option today.

## Cross-platform

The shell and sidecar contract are platform-neutral (Electron + PyInstaller + Next
standalone). Three things differ and are handled explicitly rather than by luck:

| Difference | Handling |
|---|---|
| The frozen sidecar is `ezchain-api.exe` on Windows | `apps/desktop/platform.js` `sidecarName()` |
| `app.getPath("userData")` contains backslashes on Windows, which SQLite treats as escapes | `sqliteUrl()` rewrites to forward slashes; `apps/desktop/test/platform.mjs` pins the exact URL for all three platforms |
| Windows ships bsdtar at `%SystemRoot%\System32\tar.exe`, not on PATH | `tarBinary()` resolves it and falls back to `tar` |
| The standalone web bundle carries 27 pnpm symlinks, and Windows needs a privilege to create one | `tools/build-desktop.sh` writes `web.tar.gz` with `tar -h` (dereference) and fails if a symlink survives (25.39 MB → 25.43 MB) |
| macOS rejects `com.apple.security.inherit` on a main app | two entitlement files: `entitlements.mac.plist` for the app and `entitlements.mac.inherit.plist` for its children |
| Windows `chmod` only clears the read-only bit | the token's boundary there is the inherited `%APPDATA%` ACL, stated rather than implied |
| An AppImage runs from a temporary mount | the autostart entry records `APPIMAGE`, not the mount path that disappears |

`apps/desktop/test/platform.mjs` and `test/packaging.mjs` are the regression guards: each row
was a runtime failure or a build-time rejection that no Linux run could have shown.
`test/packaging.mjs` validates `build` against electron-builder's own `scheme.json`, which is
the only authority on which key belongs where.

**Still unproven, and stated as such:** no macOS or Windows package has been built or
launched on this machine. Each needs its own host (PyInstaller cannot cross-compile), an
Apple Developer ID plus notarisation for macOS, and a code-signing certificate for Windows,
or the OS will refuse or warn about the app. `apps/desktop/package.json` carries the `mac`
(`dmg`, `zip`, hardened runtime, entitlements at `resources/entitlements.mac.plist`) and
`win` (`nsis`, `portable`) targets so the build is a one-command step on a real runner; the
targets themselves are not evidence. Do not claim those targets until a build and launch are
recorded.

## Security

- No public listener; both sidecars bind `127.0.0.1` on ephemeral ports.
- The token is generated locally, stored `0600`, and injected by the Next server only. On
  Windows `chmod` only clears the read-only attribute; the token's protection there is the
  ACL inherited from `%APPDATA%\ezCHAIN` (current user, SYSTEM, Administrators), which is
  stated rather than implied.
- No Node integration in the renderer; `contextIsolation` and sandbox are on when the OS
  permits Chromium's sandbox. Electron-builder's AppImage launcher may add `--no-sandbox`
  on hosts without unprivileged user namespaces.
- **One content-security policy for both surfaces.** `apps/web/lib/csp.ts` defines the
  policy; `next.config.ts` sends it as a response header on every route, and the shell
  applies the same string through `session.webRequest.onHeadersReceived` in `main.cjs`, so a
  response is covered even if the Next copy were compiled out. Scripts are `'self'` plus
  `'unsafe-inline'` (the App Router renders its bootstrap and RSC payload as inline
  scripts); production never allows `'unsafe-eval'`. `apps/desktop/test/sender-validation.mjs`
  fails if the two copies drift. The shell applies the policy itself rather than relying on
  Next having answered: a response the Next server never produced still carries it.
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

`cd apps/desktop && node --test "test/*.mjs"` is the unit gate: it covers the CSP copy, the
sender-frame validation, the sidecar wiring, the fuse/runtime assumptions, the platform path
rules, the power gate and the autostart entry. It runs on any host, which is the point — it
is how the Windows- and macOS-only defects are caught without those machines.

`bash tools/build-desktop.sh --target win` (or `mac`) on a Linux host exits 2 with the reason
and the host-specific command to run instead; it does not start a build it cannot finish.

See [docs/evidence/desktop/linux-app.md](evidence/desktop/linux-app.md) for the launch,
paper flow, persistence and crash-recovery record.
