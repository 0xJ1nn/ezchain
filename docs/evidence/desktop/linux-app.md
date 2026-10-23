# Evidence — native Linux desktop app (2026-10-23)

Candidate: `feat/desktop-app` (Electron 45.0.0, Next 15 standalone, PyInstaller sidecar,
SQLite). Build host: Omarchy/Hyprland, Wayland, 936 px tiled window.

## Build

```bash
pnpm install
bash tools/build-desktop.sh --package
```

Observed:

- `apps/desktop/dist/ezCHAIN-0.1.0-x86_64.AppImage` — 179 MB
- `apps/desktop/dist/ezCHAIN-0.1.0-amd64.deb` — 152 MB (built; install flow not exercised:
  no dpkg on this Arch host)
- packaged backend `resources/backend/ezchain-api/ezchain-api` — 14 MB
- packaged Next runtime tar (`web.tar.gz`) — 25 MB, extracted once per version to
  `~/.config/ezCHAIN/runtime/web-0.1.0/`

## Launch and trader flow

```bash
# FUSE 2 is not installed on this host; both paths were exercised.
APPIMAGE_EXTRACT_AND_RUN=1 ./apps/desktop/dist/ezCHAIN-0.1.0-x86_64.AppImage
./apps/desktop/dist/linux-unpacked/ezchain
```

Observed window: `ezCHAIN — trading workstation`, class `ezchain`, 936×1004
(`window bounds` and `innerWidth` both 936; DPR 1). Screenshot:
[linux-dashboard.png](linux-dashboard.png).

Through the native window (Playwright CDP against the Electron renderer):

1. dashboard rendered with live API data;
2. Markets → 50 rows;
3. opened a token, previewed `$1`, confirmed a paper order;
4. saw `PAPER FILL`; `/positions` showed the server position.

The dashboard then showed `1 open position`, equity `$10,000.20`, cash `$9,999.00`,
realized `-$0.003`, unrealized `+$0.20`, mark source `DexScreener · 3m`.

## Persistence and resilience

- Quit/relaunch (AppImage and unpacked builds) preserved the SQLite position and ledger.
- `kill -9 <api sidecar>`: the supervisor restarted the API on the same port; `/healthz` 200;
  the UI kept working.
- `window.ezchainDesktop.restartServices()` from the native renderer: stopped web then
  API, started both, reloaded the window; no duplicate sidecars, `/healthz` and
  `/desktop-health` 200.
- `kill -9 <web sidecar>`: supervisor restarted it on the same port; `/desktop-health`
  200.
- Web sidecar health is a dedicated `/desktop-health` route, not the dashboard, so a busy
  market fetch cannot make the watchdog kill a healthy server. A 50s observation window
  showed no spurious restarts.
- Window close hides to tray; the API stays alive (single-instance relaunch focuses the
  existing window).

## Gate

- Python: 1051 collected, 1048 passed, 3 skipped (desktop mode adds three tests).
- Playwright: 94 passed.
- Links, secrets, wireframe and agent-stub checks: green.
- Native launch, trader flow, persistence and API-crash recovery: observed as above.

## Not verified

- macOS and Windows builds (configured, not built here).
- Tray icon rendering on desktops without AppIndicator; the window itself is unaffected.
- Installer install/upgrade/uninstall flows for the `.deb` (no dpkg on the Arch build host).
- Live execution: not part of this build; `live_execute` is false.
