# Release bundle

Run `npm run verify:release` to run the bridge, plugin, TypeScript, Hub, Android,
Swift, shell, and patch-state gates. The command also verifies that the freshly
rebuilt EHPK and companion APK contain no placeholder credentials, copies them
here, and writes a stable `manifest.json` plus `SHA256SUMS`.

The manifest is the release pin. Build directories remain ignored, while this
directory is intentionally available for an exact private-use bundle. Never
put Hermes API keys, pairing credentials, or device tokens in the bundle.
