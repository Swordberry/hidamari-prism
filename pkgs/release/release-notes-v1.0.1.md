# Hidamari Prism v1.0.1 — the sunny spot for your Linux desktop

A warm, living desktop, now cleaner than ever. Hidamari Prism is a fork of
[Hidamari](https://github.com/jeffshee/hidamari) by Jeff Shee, written in Python
and developed with the help of AI.

Play **videos**, **streams**, **animated webpages**, or **static images** as
your wallpaper, while sipping CPU and power.

## What's new in this release

- **Removed the experimental auto-loop/smooth-loop protocol.** Loop-point
  detection, its cache, and smooth-loop baking are gone along with their
  config/migration baggage. Playback now loops cleanly and predictably with
  VLC's `input-repeat`, so wallpapers never glitch at the loop seam and
  long-running playback keeps working. The config format is back to v9.
- Promotion refreshed: the README now carries a demo video and a brand-new
  screenshot.

## Fork features (this fork)

- **Static image wallpapers** — zero rendering overhead, just a beautiful desktop.
- **Wallpaper playlists** — bundle favourites, add an entire folder at once;
  with shuffle **off** the playlist plays **in order** and loops perfectly.
- **Pause & mute when maximized** — the wallpaper pauses while you work, so it
  doesn't burn battery.
- **GNOME Wayland support** — pause-on-maximize works on Wayland through a tiny
  opt-in GNOME Shell extension (installed with your confirmation).
- **Power-friendly by default** — hardware-accelerated decoding (VA-API /
  VDPAU) with a watchdog that automatically falls back to CPU decoding if the
  GPU decoder glitches.
- **Multi-monitor ready** — per-monitor decoders and graceful hot-plug handling.

## Try it

```bash
flatpak install --user hidamari_prism-playlists.flatpak
flatpak run io.github.swordberry.Hidamari_Prism
```

Or build from source (see `build-flatpak.sh` and `pkgs/flatpak`).

## Credits

GPL-3.0-or-later fork of Hidamari. Icons by Freepik from Flaticon. Support the
project on [Ko-fi](https://ko-fi.com/swordberry) and please star the repo! 🌟