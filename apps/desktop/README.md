# ezCHAIN desktop shell

Electron supervisor for the native app. It starts a frozen FastAPI sidecar on an embedded
SQLite database and a traced Next.js server, both on private loopback ports, then opens a
native window. Closing the window hides to the tray; a watchdog restarts either sidecar.

Build from the repository root:

```bash
bash tools/build-desktop.sh            # unpacked, for this host
bash tools/build-desktop.sh --package  # installers for this host
(cd apps/desktop && node --test "test/*.mjs")  # shell unit tests, any host
```

The sidecar is a native PyInstaller freeze, so a target is built on its own platform;
`--target win|mac` refuses elsewhere and says so.

User/operator documentation: [`docs/DESKTOP.md`](../../docs/DESKTOP.md).
