# Linux App Picker submission — Hidamari Prism

**Site:** https://www.linuxappfinder.com / linux-app-picker listing form
**Name:** Hidamari Prism
**Developer (handle):** Swordberry
**License:** GPL-3.0-or-later
**Homepage:** https://github.com/Swordberry/hidamari-prism
**Download:** Flatpak bundle on the GitHub release page (v1.0.4) or the AUR (`hidamari-prism`)

## Short description

Animated wallpaper for Linux: play videos, live streams, webpages, or images as
your desktop background with low CPU/power usage.

## Long description

Hidamari Prism is a friendly fork of Hidamari, rewritten in Python and built
with the help of AI. Features include:

- Video, live-stream, webpage, and static-image wallpapers (static image: GNOME)
- Searchable wallpapers & playlists
- Wallpaper playlists with in-order or shuffle playback that loop cleanly
- Hardware-accelerated decoding (VA-API / VDPAU) with automatic CPU fallback
- Pause & mute when a window is maximized (X11 + GNOME Wayland)
- Per-monitor pause: only the screen behind a maximized/fullscreen window
  pauses
- Multi-monitor support with per-monitor decoders
- Per-monitor independent shuffle toggled from the settings window or systray menu
- No audio glitches on playlist shuffle when the wallpaper is muted

Compatibility: X11 is fully supported on every desktop; GNOME Wayland is fully
supported too. On other Wayland desktops (KDE Plasma, Sway, …) the wallpaper
plays in a plain window and pause/mute-when-maximized are no-ops.

Screenshots: `res/screenshot-1.png` (2236x1434).
Demo video: `res/demo.mp4`.
Icon: `res/hidamari_prism_icon.png` (512x512) — use the same icon/artwork as the
GitHub repo.