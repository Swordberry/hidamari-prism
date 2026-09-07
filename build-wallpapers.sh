#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
APP_ID="io.github.swordberry.Hidamari_Prism"

echo "==> Setting up flatpak shared-modules submodule..."
git submodule update --init pkgs/flatpak/shared-modules 2>/dev/null || true
MANIFEST="pkgs/flatpak/io.github.swordberry.Hidamari_Prism.json"
BUILD_DIR="build-flatpak"
BUNDLE="hidamari_prism-wallpapers.flatpak"
echo "==> Building..."
flatpak-builder --force-clean --ccache --disable-rofiles-fuse "$BUILD_DIR" "$MANIFEST"
echo "==> Exporting bundle..."
flatpak build-export "repo" "$BUILD_DIR"
flatpak build-bundle "repo" "$BUNDLE" "$APP_ID" master
echo "==> Bundle: $BUNDLE"
