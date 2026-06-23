# Build The macOS DMG

This project is now set up to package the React frontend and FastAPI backend as an Electron desktop app.

Run these commands on macOS:

```bash
chmod +x scripts/build-mac-dmg.sh
./scripts/build-mac-dmg.sh
```

The DMG will be created in:

```text
frontend/release/
```

Notes:

- A real `.dmg` must be produced on macOS. Windows cannot run Apple's DMG packaging and signing toolchain.
- The packaged app stores scan data under the user's macOS Application Support directory.
- For wider distribution, sign and notarize the app with an Apple Developer ID certificate.
