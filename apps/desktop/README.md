# ezCHAIN desktop shell

Electron supervisor for the native app. It starts a frozen FastAPI sidecar on an embedded
SQLite database and a traced Next.js server, both on private loopback ports, then opens a
native window. Closing the window hides to the tray; a watchdog restarts either sidecar.

Build from the repository root:

```bash
bash tools/build-desktop.sh            # unpacked
bash tools/build-desktop.sh --package  # AppImage + .deb
```

User/operator documentation: [`docs/DESKTOP.md`](../../docs/DESKTOP.md).
