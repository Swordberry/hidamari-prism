# Hidamari Prism v1.0.3 — the sunny spot for your Linux desktop

A warm, living desktop, now cleaner than ever. Hidamari Prism is a fork of
[Hidamari](https://github.com/jeffshee/hidamari) by Jeff Shee, written in Python
and developed with the help of AI.

Play **videos**, **streams**, **animated webpages**, or **static images** as
your wallpaper, while sipping CPU and power.

## Which Linux does it run on?

Hidamari Prism is made for **Linux**, and it ships as a Flatpak, so it runs on
any distribution that supports Flatpak — Fedora, Ubuntu, Arch, openSUSE, and
just about anything else.

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

## What's new in this release

- **Per-monitor pause & mute.** Pause-on-maximize finally works per screen:
  only the wallpaper behind a maximized or fullscreen window pauses (X11), and
  on GNOME Wayland the extension tracks the monitor **connector**, so a window
  dragged to another screen pauses that screen's wallpaper instead of the old
  one. Your other monitors keep animating while one is covered.
- **Quit button fixed.** Clicking Quit now cleanly shuts down the server, every
  wallpaper player, the system tray, and the control panel — no orphan
  processes.

## Fork features (this fork)

- **Per-monitor pause & mute when maximized** — only the covered monitor
  pauses; the rest of your desktop keeps living.
- **Static image wallpapers (GNOME)** — zero rendering overhead, just a
  beautiful desktop.
- **Wallpaper playlists** — bundle favourites, add an entire folder at once;
  with shuffle **off** the playlist plays **in order** and loops perfectly.
- **Searchable wallpapers & playlists** — find a wallpaper instantly from a
  search bar in both tabs.
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