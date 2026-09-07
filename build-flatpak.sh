#!/usr/bin/env bash
# Build Hidamari_Prism (+ playlist/shuffle changes) as a Flatpak with bundled VLC.
# Builds against the SYSTEM flatpak install (SDK + Platform pre-installed).
set -euo pipefail
cd "$(dirname "$0")"

APP_ID="io.github.swordberry.Hidamari_Prism"
MANIFEST="pkgs/flatpak/io.github.swordberry.Hidamari_Prism.json"
BUILD_DIR="build-flatpak"
BUNDLE="hidamari_prism-playlists.flatpak"

echo "==> Building (downloads & compiles VLC etc., may take a while)..."
flatpak-builder --force-clean --ccache --disable-rofiles-fuse "$BUILD_DIR" "$MANIFEST"

echo "==> Exporting bundle..."
flatpak build-export "repo" "$BUILD_DIR"
flatpak build-bundle "repo" "$BUNDLE" "$APP_ID" master
