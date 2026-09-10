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

- **Static image wallpapers (GNOME)** — zero rendering overhead, just a beautiful desktop.
- **Per-monitor pause & mute** — when a window is maximized, only that screen's
  wallpaper pauses; your other monitors keep animating.
- **Searchable wallpapers & playlists** — find a wallpaper instantly; the
  playlist dropdown tracks the window width instead of overflowing.
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

Hidamari Prism is made for **Linux** and ships as a Flatpak, so it runs on any
distribution that supports Flatpak — Fedora, Ubuntu, Arch, openSUSE, and just
about anything else.

- **X11 — fully supported on every desktop** (GNOME, KDE Plasma, XFCE, Cinnamon,
  and any other window manager). Video, stream, and web-page wallpapers work
  everywhere, and pause/mute-when-maximized works on any X11 desktop through the
  window manager. Static-image wallpapers and restoring the original wallpaper
  are GNOME-only.
- **GNOME on Wayland — fully supported**, including pause/mute when maximized
  through a tiny opt-in GNOME Shell extension (installed with your confirmation).
- **KDE Plasma, Sway, and other Wayland compositors** — video, stream, and
  web-page wallpapers play in a plain window; the window-state toggles and
  static-image wallpapers are no-ops there. Run an X11 session for the full
  experience.

### Install

- Flatpak bundle: attach `hidamari_prism-playlists.flatpak` from the GitHub
  release v1.0.3.
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