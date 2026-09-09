# Itch.io page — Hidamari Prism

**Type:** Linux desktop app / wallpaper engine
**Price:** pay what you want (suggest $0)
**Page:** https://swordberry.itch.io/

## Short blurb

A warm, living desktop: play videos, streams, animated webpages, or static
images as your Linux wallpaper — while sipping CPU and power. A friendly fork
of Hidamari, written in Python.

## Long description

Hidamari Prism turns your Linux desktop into a living, breathing place. Play
**videos**, **live streams**, **animated webpages**, or **static images** as
your wallpaper, and it does all of it while sipping CPU and power.

### Highlights

- **Static image wallpapers** — zero rendering overhead, just a beautiful desktop.
- **Wallpaper playlists** — bundle favourites or add an entire folder at once.
  With shuffle off, the playlist plays in order and loops perfectly.
- **Pause & mute when maximized** — the wallpaper pauses while you work, so it
  doesn't burn battery.
- **GNOME Wayland support** — pause-on-maximize works on Wayland via a tiny
  opt-in GNOME Shell extension.
- **Power-friendly by default** — hardware-accelerated decoding (VA-API / VDPAU)
  with a watchdog that automatically falls back to CPU if the GPU decoder
  glitches.
- **Multi-monitor ready** — per-monitor decoders and graceful hot-plug handling.
- **Clean looping** — playlists and videos loop predictably at the seam; no
  experimental smoothing stack.

### Compatibility

- X11 (any desktop) — full support, incl. pause/mute on maximize.
- GNOME Wayland — full support via the opt-in GNOME Shell extension.
- KDE Plasma / Sway / other Wayland — not supported as wallpaper (renders as a
  plain window); pause/mute-on-maximize is a no-op there.

### Install

- Flatpak bundle: attach `hidamari_prism-playlists.flatpak` from the GitHub
  release v1.0.1.
- `flatpak install --user ./hidamari_prism-playlists.flatpak && flatpak run io.github.swordberry.Hidamari_Prism`
- AUR: `paru -S hidamari-prism`

### Gallery

- **Icon / header**: `res/hidamari_prism_icon.png` (512x512)
- Video: use `res/demo.mp4` (or the original webm in `/home/swordberry/Videos/Screencasts/`)
- Screenshot: use `res/screenshot-1.png` (2236x1434)

## Tags

wallpaper, animated wallpaper, video wallpaper, livestream wallpaper, gnome,
wayland, x11, vlc, python, gtk

## Credits / license

GPL-3.0-or-later fork of [Hidamari](https://github.com/jeffshee/hidamari) by
Jeff Shee, developed with the help of AI. Icons by Freepik from Flaticon.
Support on [Ko-fi](https://ko-fi.com/swordberry).