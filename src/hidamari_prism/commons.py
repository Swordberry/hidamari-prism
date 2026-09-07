import os
import subprocess

LOGGER_NAME = "Hidamari_Prism"

PROJECT = "io.github.swordberry.Hidamari_Prism"
DBUS_NAME_SERVER = f"{PROJECT}.server"
DBUS_NAME_PLAYER = f"{PROJECT}.player"

# gettext text domain (matches po/meson.build and the installed hidamari_prism.mo)
TRANSLATION_DOMAIN = "hidamari_prism"

HOME = os.environ.get("HOME")
try:
    xdg_video_dir = subprocess.check_output(
        "xdg-user-dir VIDEOS", shell=True, encoding="UTF-8"
    ).replace("\n", "")
    VIDEO_WALLPAPER_DIR = os.path.join(xdg_video_dir, "Hidamari_Prism")
except FileNotFoundError:
    # xdg-user-dir not found, use $HOME/Hidamari_Prism for Video directory instead
    VIDEO_WALLPAPER_DIR = os.path.join(HOME, "Hidamari_Prism")

xdg_config_home = os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
AUTOSTART_DIR = os.path.join(xdg_config_home, "autostart")
AUTOSTART_DESKTOP_PATH = os.path.join(AUTOSTART_DIR, f"{PROJECT}.desktop")
AUTOSTART_DESKTOP_CONTENT = """[Desktop Entry]
Name=Hidamari Prism
Exec=hidamari_prism -b
Icon=io.github.swordberry.Hidamari_Prism
Terminal=false
Type=Application
Categories=GTK;Utility;
StartupNotify=true
"""
AUTOSTART_DESKTOP_CONTENT_FLATPAK = """[Desktop Entry]
Name=Hidamari Prism
Exec=/usr/bin/flatpak run --command=hidamari_prism io.github.swordberry.Hidamari_Prism -b
Icon=io.github.swordberry.Hidamari_Prism
Terminal=false
Type=Application
Categories=GTK;Utility;
StartupNotify=true
X-Flatpak=io.github.swordberry.Hidamari_Prism
"""

CONFIG_DIR = os.path.join(xdg_config_home, "hidamari_prism")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# A persistent log file that survives hard reboots: used to diagnose crashes,
# hangs and monitor hot-plug issues where stdout/stderr are lost (the player and
# server run in separate forked processes that cannot be relied on to flush
# their output synchronously, and /tmp is wiped on every boot).
LOG_PATH = os.path.join(CONFIG_DIR, "hidamari_prism.log")

import logging

def setup_persistent_logging():
    """Attach a rotating debug file handler to the Hidamari_Prism logger.

    Safe to call more than once (idempotent). Each record is flushed
    immediately so a sudden kernel hang / forced power-off still leaves the
    final lines on disk.
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(processName)s: %(message)s"
            )
        )
        logger = logging.getLogger(LOGGER_NAME)
        # Avoid duplicate handlers if this runs in multiple processes / imports.
        for existing in logger.handlers:
            try:
                if isinstance(existing, logging.FileHandler) and existing.baseFilename == LOG_PATH:
                    return
            except Exception:
                pass
        logger.addHandler(handler)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(LOGGER_NAME).warning(f"[Log] persistent file logging disabled: {e}")

setup_persistent_logging()

MODE_NULL = "MODE_NULL"
MODE_VIDEO = "MODE_VIDEO"
MODE_STREAM = "MODE_STREAM"
MODE_WEBPAGE = "MODE_WEBPAGE"

CONFIG_VERSION = 7
CONFIG_KEY_VERSION = "version"
CONFIG_KEY_MODE = "mode"
CONFIG_KEY_DATA_SOURCE = "data_source"
CONFIG_KEY_MUTE = "is_mute"
CONFIG_KEY_VOLUME = "audio_volume"
CONFIG_KEY_STATIC_WALLPAPER = "is_static_wallpaper"
CONFIG_KEY_BLUR_RADIUS = "static_wallpaper_blur_radius"
CONFIG_KEY_PAUSE_WHEN_MAXIMIZED = "is_pause_when_maximized"
CONFIG_KEY_MUTE_WHEN_MAXIMIZED = "is_mute_when_maximized"
CONFIG_KEY_FADE_DURATION_SEC = "fade_duration_sec"
CONFIG_KEY_FADE_INTERVAL = "fade_interval"
CONFIG_KEY_SYSTRAY = "is_show_systray"
CONFIG_KEY_FIRST_TIME = "is_first_time"
CONFIG_KEY_LAUNCH_COUNT = "launch_count"
CONFIG_KEY_DONATE_ONCE = "donate_once"
CONFIG_KEY_HARDWARE_ACCEL = "hardware_acceleration"
CONFIG_KEY_PLAYLISTS = "playlists"
CONFIG_KEY_SHUFFLE = "shuffle"
CONFIG_KEY_SHUFFLE_ENABLED = "enabled"
CONFIG_KEY_SHUFFLE_INTERVAL = "interval_minutes"
CONFIG_KEY_SHUFFLE_ACTIVE = "active_playlist"
CONFIG_KEY_SHUFFLE_INDEPENDENT = "shuffle_independent"
SHUFFLE_INTERVAL_MIN_MIN = 1
SHUFFLE_INTERVAL_STEP_MIN = 1
SHUFFLE_INTERVAL_MAX_MIN = 60
CONFIG_TEMPLATE = {
    CONFIG_KEY_VERSION: CONFIG_VERSION,
    CONFIG_KEY_MODE: MODE_NULL,
    CONFIG_KEY_DATA_SOURCE: None,
    CONFIG_KEY_MUTE: False,
    CONFIG_KEY_VOLUME: 50,
    CONFIG_KEY_STATIC_WALLPAPER: True,
    CONFIG_KEY_BLUR_RADIUS: 5,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED: True,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED: False,
    CONFIG_KEY_FADE_DURATION_SEC: 1.5,
    CONFIG_KEY_FADE_INTERVAL: 0.1,
    CONFIG_KEY_SYSTRAY: False,
    CONFIG_KEY_FIRST_TIME: True,
    CONFIG_KEY_HARDWARE_ACCEL: True,
    CONFIG_KEY_PLAYLISTS: {},
    CONFIG_KEY_SHUFFLE: {
        CONFIG_KEY_SHUFFLE_ENABLED: False,
        CONFIG_KEY_SHUFFLE_INTERVAL: SHUFFLE_INTERVAL_STEP_MIN,
        CONFIG_KEY_SHUFFLE_ACTIVE: "",
        CONFIG_KEY_SHUFFLE_INDEPENDENT: False,
    },
}

from hidamari_prism.monitor import MonitorInfo

# initialize config according to monitors
info = MonitorInfo()
monitors = info.monitors()
data_sources = {}
for monitor in monitors:
    data_sources[monitor["name"]] = ""
data_sources["Default"] = ""

CONFIG_TEMPLATE[CONFIG_KEY_DATA_SOURCE] = data_sources
