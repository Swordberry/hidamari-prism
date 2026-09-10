import gettext
import json
import locale
import logging
import os
import shutil
import time
from pprint import pformat

import gi

gi.require_version("Wnck", "3.0")
import pydbus
from gi.repository import Gio, GLib, Wnck

from hidamari_prism.commons import (
    AUTOSTART_DESKTOP_CONTENT,
    AUTOSTART_DESKTOP_CONTENT_FLATPAK,
    AUTOSTART_DESKTOP_PATH,
    AUTOSTART_DIR,
    CONFIG_DIR,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLISTS,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_SHUFFLE_INDEPENDENT,
    CONFIG_KEY_HARDWARE_ACCEL,
    CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK,
    HARDWARE_ACCEL_AUTO,
    HARDWARE_ACCEL_ON,
    HARDWARE_ACCEL_OFF,
    CONFIG_PATH,
    CONFIG_TEMPLATE,
    CONFIG_VERSION,
    LOGGER_NAME,
    MODE_VIDEO,
    PROJECT,
    TRANSLATION_DOMAIN,
    VIDEO_WALLPAPER_DIR,
)

logger = logging.getLogger(LOGGER_NAME)


def init_translations(localedir):
    """Bind the gettext text domain for the current process.

    The forkserver children (GUI, systray) don't inherit the launcher's gettext
    setup, so each entry point binds it itself. We bind at both the C-library
    level (for GtkBuilder/.ui strings) and the Python level (for _()); missing
    catalogs simply fall back to the original strings, so this can't crash.
    """
    try:
        locale.bindtextdomain(TRANSLATION_DOMAIN, localedir)
        locale.textdomain(TRANSLATION_DOMAIN)
    except (AttributeError, OSError) as e:
        logger.debug("[i18n] C locale bind skipped: %s", e)
    gettext.bindtextdomain(TRANSLATION_DOMAIN, localedir)
    gettext.textdomain(TRANSLATION_DOMAIN)


def is_gnome():
    """
    Check if current DE is GNOME or not.
    On Ubuntu 20.04, $XDG_CURRENT_DESKTOP = ubuntu:GNOME
    On Fedora 34, $XDG_CURRENT_DESKTOP = GNOME
    Hence we do the detection by looking for the word "gnome"
    """
    return "gnome" in str(os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()


def is_wayland():
    """
    Check if current session is Wayland or not.
    $XDG_SESSION_TYPE = x11 | wayland
    """
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def is_flatpak():
    """
    Check if Hidamari_Prism is a Flatpak
    Reference:
    https://gitlab.gnome.org/jrb/crosswords/-/blob/master/src/crosswords-init.c#L179
    """
    return os.path.isfile("/.flatpak-info")


def setup_autostart(autostart):
    if is_flatpak():
        """
        Use portal to autostart for Flatpak
        Documentation:
        https://libportal.org/method.Portal.request_background.html
        https://libportal.org/method.Portal.request_background_finish.html
        """

        gi.require_version("Xdp", "1.0")
        from gi.repository import Xdp

        xdp = Xdp.Portal.new()

        # Request Autostart
        xdp.request_background(
            None,  # parent
            "Autostart Hidamari_Prism in background",  # reason
            ["hidamari_prism", "-b"],  # commandline
            Xdp.BackgroundFlags.AUTOSTART if autostart else Xdp.BackgroundFlags.NONE,  # flags
            None,  # cancellable
            lambda portal, result, user_data: logger.debug(
                f"[Utils] autostart={autostart}, request_background sucess={portal.request_background_finish(result)}"
            ),  # callback
            None,  # user_data
        )

    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    logger.debug(f"[Utils] autostart={autostart}, path={AUTOSTART_DESKTOP_PATH}")
    if autostart:
        with open(AUTOSTART_DESKTOP_PATH, mode="w") as f:
            if is_flatpak():
                # Write files to the sandbox as well, for the following reasons:
                # (1) So that we know if autostart is enabled by looking the file in sandbox
                # (2) Acts as a fallback in case the portal doesn't work
                f.write(AUTOSTART_DESKTOP_CONTENT_FLATPAK)
            else:
                f.write(AUTOSTART_DESKTOP_CONTENT)
    else:
        if os.path.isfile(AUTOSTART_DESKTOP_PATH):
            os.remove(AUTOSTART_DESKTOP_PATH)


def get_video_paths():
    """Return the playable wallpaper files (videos *and* static images) in the
    Hidamari Prism folder. Images are included so they can be used as static
    wallpapers alongside videos."""
    file_list = []
    for filename in os.listdir(VIDEO_WALLPAPER_DIR):
        filepath = os.path.join(VIDEO_WALLPAPER_DIR, filename)
        file = Gio.file_new_for_path(filepath)
        info = file.query_info("standard::content-type", Gio.FileQueryInfoFlags.NONE, None)
        mime_type = info.get_content_type()
        if "video" in mime_type or "image" in mime_type:
            file_list.append(filepath)
    return sorted(file_list)


def is_static_image(path):
    """Return True if the given path is a static image (png/jpg/etc.) rather
    than a video. Used to switch the player between looping video and a static
    image rendered through VLC's image demuxer."""
    try:
        file = Gio.file_new_for_path(path)
        info = file.query_info(
            "standard::content-type", Gio.FileQueryInfoFlags.NONE, None
        )
        mime_type = info.get_content_type()
    except GLib.Error:
        return False
    return mime_type.startswith("image/")


"""
GNOME extension utils
"""


def gnome_extension_is_enabled(extension_name: str):
    try:
        gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
        info: dict = gnome_ext.GetExtensionInfo(extension_name)
        return info["state"] == 1  # ENABLE = 1
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Utils] gnome_extension_is_enabled failed for {extension_name}: {e}")
        return False


def gnome_extension_set_enable(extension_name: str):
    try:
        gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
        success: bool = gnome_ext.EnableExtension(extension_name)
        return success
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Utils] gnome_extension_set_enable failed for {extension_name}: {e}")
        return False


def gnome_extension_set_disable(extension_name: str):
    try:
        gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
        success: bool = gnome_ext.DisableExtension(extension_name)
        return success
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Utils] gnome_extension_set_disable failed for {extension_name}: {e}")
        return False


def gnome_extension_is_installed(extension_name: str):
    try:
        gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
        installed: dict = gnome_ext.ListExtensions()
        return extension_name in installed.keys()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Utils] gnome_extension_is_installed failed for {extension_name}: {e}")
        return False


def gnome_desktop_icon_workaround():
    """
    Workaround for GNOME desktop icon extensions not displaying the icons on top of Hidamari_Prism.
    Call this right after the wallpaper is shown.
    """
    if not is_gnome():
        return
    extension_list = [
        "ding@rastersoft.com",
        "desktopicons-neo@darkdemon",
        "gtk4-ding@smedius.gitlab.com",
        "zorin-desktop-icons@zorinos.com",
    ]
    for ext in extension_list:
        # Check if installed and enabled
        if gnome_extension_is_installed(ext) and gnome_extension_is_enabled(ext):
            # Reload the extension
            logger.info(f"[Utils] Apply workaround for {ext}")
            gnome_extension_set_disable(ext)
            gnome_extension_set_enable(ext)


"""
Handlers
"""


class ActiveHandler:
    """
    Handler for monitoring screen lock
    GNOME:
    https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/data/dbus-interfaces/org.gnome.ScreenSaver.xml
    Cinamon:
    https://github.com/linuxmint/cinnamon-screensaver/blob/master/libcscreensaver/org.cinnamon.ScreenSaver.xml
    Freedesktop:
    https://github.com/KDE/kscreenlocker/blob/master/dbus/org.freedesktop.ScreenSaver.xml
    """

    def __init__(self, on_active_changed: callable):
        self.session_bus = pydbus.SessionBus()
        self.proxies = []
        self.signal_subscriptions = []

        screensaver_list = [
            "org.gnome.ScreenSaver",
            "org.cinnamon.ScreenSaver",
            "org.freedesktop.ScreenSaver",
        ]
        for s in screensaver_list:
            try:
                proxy = self.session_bus.get(s)
                # Store proxy reference to prevent garbage collection
                self.proxies.append(proxy)
                subscription = proxy.ActiveChanged.connect(on_active_changed)
                self.signal_subscriptions.append((proxy, subscription))
            except GLib.Error:
                pass

    def cleanup(self):
        """Cleanup signal subscriptions"""
        # pydbus has no disconnect; connections drop when the proxies are GC'd
        self.signal_subscriptions.clear()
        self.proxies.clear()


class EndSessionHandler:
    """
    Handler for monitoring end session
    References:
    https://github.com/backloop/gendsession

    PrepareForShutdown() signal from logind is not handled
    https://gitlab.gnome.org/GNOME/gnome-shell/-/issues/787
    """

    def __init__(self, on_end_session: callable):
        self.on_end_session = on_end_session

        if is_gnome():
            session_bus = pydbus.SessionBus()
            proxy = session_bus.get("org.gnome.SessionManager")
            client_id = proxy.RegisterClient("", "")
            self.session_client = session_bus.get("org.gnome.SessionManager", client_id)
            self.session_client.QueryEndSession.connect(self.__query_end_session_handler_gnome)
            self.session_client.EndSession.connect(self.__end_session_handler_gnome)
        else:
            system_bus = pydbus.SystemBus()
            proxy = system_bus.get(".login1")
            proxy.PrepareForShutdown.connect(self.__end_session_handler)

    def __end_session_response_gnome(self, ok=True):
        if ok:
            self.session_client.EndSessionResponse(True, "")
        else:
            self.session_client.EndSessionResponse(False, "Not ready")

    def __query_end_session_handler_gnome(self, flags):
        # Ignore flags, always agree on the QueryEndSesion
        self.__end_session_response_gnome(True)

    def __end_session_handler_gnome(self, flags):
        logger.debug("[EndSessionHandler] called")
        self.on_end_session()
        self.__end_session_response_gnome(True)

    def __end_session_handler(self, *_):
        logger.debug("[EndSessionHandler] called")
        self.on_end_session()


WINDOW_STATE_ALL_MONITORS = "__all__"


def _best_monitor_for_rect(rects, x, y, w, h):
    """Return the monitor model whose geometry overlaps the given window rect
    the most; falls back to the monitor whose center is nearest."""
    cx, cy = x + w / 2, y + h / 2
    best_overlap = -1
    best_model_overlap = None
    best_dist = float("inf")
    best_model_dist = None
    for model, (mx, my, mw, mh) in rects.items():
        ox = max(0, min(x + w, mx + mw) - max(x, mx))
        oy = max(0, min(y + h, my + mh) - max(y, my))
        area = ox * oy
        if area > best_overlap:
            best_overlap = area
            best_model_overlap = model
        dcx, dcy = mx + mw / 2, my + mh / 2
        dist = (cx - dcx) ** 2 + (cy - dcy) ** 2
        if dist < best_dist:
            best_dist = dist
            best_model_dist = model
    if best_overlap > 0:
        return best_model_overlap
    return best_model_dist


class WindowHandler:
    """
    Handler for monitoring window events (maximized and fullscreen mode) for X11

    Reports which monitors currently have a maximized or fullscreen window, so
    only the wallpapers behind that window pause (the other monitors keep
    playing). ``get_monitor_rects`` is a callable returning
    ``{model: (x, y, w, h)}`` geometry for every monitor; it is used to
    attribute a blocking window to its monitor.
    """

    def __init__(self, on_window_state_changed: callable, get_monitor_rects=None):
        self.on_window_state_changed = on_window_state_changed
        self.get_monitor_rects = get_monitor_rects
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()

        # Store signal handler IDs for cleanup
        self.signal_handlers = []
        self.window_signal_handlers = {}

        # Connect screen signals and store handler IDs
        handler_id = self.screen.connect("window-opened", self.window_opened, None)
        self.signal_handlers.append((self.screen, handler_id))

        handler_id = self.screen.connect("window-closed", self.eval, None)
        self.signal_handlers.append((self.screen, handler_id))

        handler_id = self.screen.connect("active-workspace-changed", self.eval, None)
        self.signal_handlers.append((self.screen, handler_id))

        # Connect to existing windows
        for window in self.screen.get_windows():
            self._connect_window(window)

        self.prev_state = None
        # Initial check
        self.eval()

    def _connect_window(self, window):
        """Connect to a window and store the handler ID"""
        if window not in self.window_signal_handlers:
            handler_id = window.connect("state-changed", self.eval, None)
            self.window_signal_handlers[window] = handler_id

    def window_opened(self, screen, window, _):
        self._connect_window(window)

    def _busy_monitors(self):
        busy = set()
        rects = self.get_monitor_rects() if self.get_monitor_rects else {}
        can_attribute = bool(rects)
        for window in self.screen.get_windows():
            base_state = not Wnck.Window.is_minimized(window) and Wnck.Window.is_on_workspace(
                window, self.screen.get_active_workspace()
            )
            blocked = (
                Wnck.Window.is_maximized(window) or Wnck.Window.is_fullscreen(window)
            ) and base_state
            if not blocked:
                continue
            if can_attribute:
                try:
                    geom = window.get_geometry()
                    x, y, w, h = geom
                    model = _best_monitor_for_rect(rects, x, y, w, h)
                except Exception:  # noqa: BLE001
                    model = None
                if model:
                    busy.add(model)
                else:
                    busy.add(WINDOW_STATE_ALL_MONITORS)
            else:
                busy.add(WINDOW_STATE_ALL_MONITORS)
        return busy

    def eval(self, *args):
        cur_state = {"busy_monitors": self._busy_monitors()}
        if self.prev_state is None or self.prev_state != cur_state:
            self.prev_state = cur_state
            self.on_window_state_changed(cur_state)
            logger.debug(f"[WindowHandler] busy monitors: {sorted(cur_state['busy_monitors'])}")

    def cleanup(self):
        """Cleanup all signal handlers to prevent memory leaks"""
        # Disconnect screen signals
        for obj, handler_id in self.signal_handlers:
            try:
                obj.disconnect(handler_id)
            except Exception as e:
                logger.warning(f"[WindowHandler] Error disconnecting screen signal: {e}")
        self.signal_handlers.clear()

        # Disconnect window signals
        for window, handler_id in self.window_signal_handlers.items():
            try:
                window.disconnect(handler_id)
            except Exception as e:
                logger.warning(f"[WindowHandler] Error disconnecting window signal: {e}")
        self.window_signal_handlers.clear()


GNOME_EXTENSION_UUID = "window-state@io.github.swordberry.Hidamari_Prism"


def _app_data_dir():
    """The app's own ~/.var/app/<appid> dir, writable by the sandbox and,
    via the absolute path, by the host-side GNOME Shell extension too."""
    return os.path.join(os.path.expanduser("~"), ".var", "app", PROJECT)


def _gnome_extension_state_path():
    return os.path.join(_app_data_dir(), "window_state")


def wayland_window_state():
    """Parse the state file mirror written by the bundled GNOME Shell
    extension, or None if it is not available (extension disabled/absent).

    The file lists one monitor per line, keyed by monitor connector name (the
    same names Gdk reports via ``Monitor.get_model()``)::

        eDP-1=m
        HDMI-A-1=f

    where the value describes the blocking window on that monitor (``m``
    maximized, ``f`` fullscreen, ``m+f`` both). The app pauses only the
    wallpaper(s) on monitors that appear here, so a maximized window on one
    screen no longer freezes the wallpapers on the other screens.
    """
    try:
        with open(_gnome_extension_state_path(), encoding="utf-8") as f:
            busy = set()
            for line in f:
                if "=" not in line:
                    continue
                key, _, value = line.strip().partition("=")
                value = value.strip()
                if key in ("m", "f"):
                    continue  # legacy all-monitors format
                if value and value != "0":
                    busy.add(key)
        return {"busy_monitors": busy}
    except OSError:
        return None


def window_state_extension_ready():
    """True when the bundled GNOME Shell extension is installed and enabled."""
    if not is_gnome():
        return False
    try:
        shell = pydbus.SessionBus().get("org.gnome.Shell", "/org/gnome/Shell")
        info = shell.GetExtensionInfo(GNOME_EXTENSION_UUID)
        return str(info.get("state", -1)) == "1"  # ENABLED
    except Exception:  # noqa: BLE001
        return False


def window_state_extension_installed():
    """True when the bundled GNOME Shell extension's files already exist in
    the host extensions directory. This is the "already set up" check used to
    avoid re-showing the install prompt on every toggle."""
    if not is_gnome():
        return False
    ext_dir = os.path.join(
        os.path.expanduser("~"), ".local", "share", "gnome-shell", "extensions"
    )
    dest = os.path.join(ext_dir, GNOME_EXTENSION_UUID)
    return os.path.isfile(os.path.join(dest, "metadata.json")) and os.path.isfile(
        os.path.join(dest, "extension.js")
    )


def ensure_window_state_extension(pkgdatadir):
    """Install (once) the bundled GNOME Shell extension and ask Shell to enable
    it, so the wallpaper can detect maximized/fullscreen windows under GNOME
    Wayland. Returns (ok, message).

    GNOME Shell only discovers brand-new extension directories at session
    start (a documented limitation), so even after the files are copied and
    EnableExtension is accepted, the extension cannot run until the user
    restarts their session at least once. `ok` therefore reflects that the
    files were installed and Shell accepted the enable request; callers should
    treat a True result as "installed, working after a session restart" unless
    window_state_extension_ready() is already True."""
    if not is_gnome():
        return False, "GNOME Shell window detection is only supported on GNOME."
    if not pkgdatadir:
        return False, "Bundled GNOME Shell extension files are missing."

    src = os.path.join(pkgdatadir, "extensions", GNOME_EXTENSION_UUID)
    if not os.path.isdir(src):
        return False, "Bundled GNOME Shell extension files are missing."

    # GNOME Shell reads extensions from the HOST ~/.local/share/gnome-shell/
    # extensions. In the sandbox XDG_DATA_HOME is redirected to the app's own
    # ~/.var/app/<appid>/data, so GLib.get_user_data_dir() would point at the
    # wrong (invisible-to-Shell) location -- build the path from $HOME instead.
    extensions_dir = os.path.join(
        os.path.expanduser("~"), ".local", "share", "gnome-shell", "extensions"
    )
    dest = os.path.join(extensions_dir, GNOME_EXTENSION_UUID)
    try:
        os.makedirs(extensions_dir, exist_ok=True)
        if not os.path.isdir(dest):
            shutil.copytree(src, dest)
        else:
            # Refresh anything missing, keep any user tweaks to existing files.
            for name in os.listdir(src):
                src_file = os.path.join(src, name)
                dst_file = os.path.join(dest, name)
                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)
    except OSError as e:
        return False, f"Couldn't install the GNOME Shell extension: {e}"

    # Ask GNOME Shell to enable it. GNOME Shell cannot discover a brand-new
    # extension directory until the session is restarted; EnableExtension
    # therefore only succeeds once the directory is already known (previous
    # install). Both cases are acceptable -- the feature takes effect after the
    # user logs back in.
    try:
        shell = pydbus.SessionBus().get("org.gnome.Shell", "/org/gnome/Shell")
    except Exception as e:  # noqa: BLE001
        return False, f"Couldn't reach GNOME Shell: {e}"

    known = False
    for _ in range(10):
        try:
            if GNOME_EXTENSION_UUID in shell.ListExtensions():
                known = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)

    if known:
        for _ in range(10):
            try:
                shell.EnableExtension(GNOME_EXTENSION_UUID)
                if window_state_extension_ready():
                    return True, ""
            except Exception as e:  # noqa: BLE001
                logger.error(f"[Extension] EnableExtension failed: {e}")
            time.sleep(0.5)
        return False, (
            "The extension files were installed but GNOME Shell could not "
            "enable them. Check the GNOME Extensions settings."
        )

    # Extension is fresh: GNOME Shell will only see it after a session
    # restart. Files are in place; the activity resumes on next login.
    logger.info("[Extension] New extension installed; awaits session restart")
    return True, "RELOAD_REQUIRED"


class WaylandWindowHandler:
    """Window-state handler for GNOME Wayland.

    GNOME blocks every window-introspection D-Bus API available to (sandboxed)
    apps ("GetWindows is not allowed"), so Hidamari Prism ships a tiny GNOME
    Shell extension instead: it watches native window events and mirrors which
    monitor has a maximized/fullscreen window into a small
    file in the app's own data directory. This handler polls that file once
    per second -- a sub-millisecond stat/read -- which is far cheaper than the
    60fps of decode work it lets us avoid when the wallpaper is covered.
    """

    POLL_INTERVAL_SEC = 1

    def __init__(self, on_window_state_changed: callable, get_monitor_rects=None):
        self.on_window_state_changed = on_window_state_changed
        self._prev = None
        self._timer_id = GLib.timeout_add_seconds(self.POLL_INTERVAL_SEC, self._poll)
        self._poll()

    def _read(self):
        return wayland_window_state()

    def _poll(self):
        state = self._read()
        if state is None:
            # Extension gone (disabled/uninstalled): treat it as "nothing is
            # maximized" so the wallpaper always resumes cleanly.
            if self._prev is not None:
                self._prev = None
                self._emit({"busy_monitors": set()})
        elif state != self._prev:
            self._prev = state
            self._emit(state)
        return GLib.SOURCE_CONTINUE

    def _emit(self, state):
        try:
            self.on_window_state_changed(state)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[WaylandWindowHandler] {e}")

    def cleanup(self):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None


class ConfigUtil:
    def generate_template(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.save(CONFIG_TEMPLATE)

    @staticmethod
    def _check(config: dict):
        """Check if the config is valid"""
        is_all_keys_match = all(key in config for key in CONFIG_TEMPLATE)
        is_version_match = config.get("version") == CONFIG_VERSION
        return is_all_keys_match and is_version_match

    def _invalid(self):
        logger.debug("[Config] Invalid. A new config will be generated.")
        self.generate_template()
        return CONFIG_TEMPLATE

    def _migrateV3To4(self, config: dict):
        logger.debug("[Config] Migration from version 3 to 4.")
        curr_data_source = config["data_source"]
        config["data_source"] = CONFIG_TEMPLATE[CONFIG_KEY_DATA_SOURCE]
        config["data_source"]["Default"] = curr_data_source
        config["is_pause_when_maximized"] = config["is_detect_maximized"]
        del config["is_detect_maximized"]
        config["is_mute_when_maximized"] = CONFIG_TEMPLATE[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]
        config["version"] = 4
        # save config file
        self.save(config)

    def _migrateV4To5(self, config: dict):
        logger.debug("[Config] Migration from version 4 to 5.")
        config[CONFIG_KEY_PLAYLISTS] = CONFIG_TEMPLATE[CONFIG_KEY_PLAYLISTS]
        config[CONFIG_KEY_SHUFFLE] = CONFIG_TEMPLATE[CONFIG_KEY_SHUFFLE]
        config["version"] = 5
        # save config file
        self.save(config)

    def _migrateV5To6(self, config: dict):
        logger.debug("[Config] Migration from version 5 to 6.")
        shuffle = config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        shuffle.setdefault(CONFIG_KEY_SHUFFLE_INDEPENDENT, False)
        config[CONFIG_KEY_SHUFFLE] = shuffle
        config["version"] = 6
        # save config file
        self.save(config)

    def _migrateV6To7(self, config: dict):
        logger.debug("[Config] Migration from version 6 to 7.")
        config.setdefault(CONFIG_KEY_HARDWARE_ACCEL, True)
        config["version"] = 7
        # save config file
        self.save(config)

    def _migrateV7To8(self, config: dict):
        logger.debug("[Config] Migration from version 7 to 8.")
        # Hardware acceleration became a three-state preference (auto/on/off),
        # replacing the old boolean. A legacy bool maps to on/off; anything else
        # (or a fresh config) lands on "auto".
        prev = config.get(CONFIG_KEY_HARDWARE_ACCEL, HARDWARE_ACCEL_AUTO)
        if isinstance(prev, bool):
            # The old boolean "true" already meant "hardware decoding with the
            # auto-fallback watchdog", so it maps to the new "auto" (not "on").
            config[CONFIG_KEY_HARDWARE_ACCEL] = HARDWARE_ACCEL_AUTO if prev else HARDWARE_ACCEL_OFF
        elif prev not in (HARDWARE_ACCEL_AUTO, HARDWARE_ACCEL_ON, HARDWARE_ACCEL_OFF):
            config[CONFIG_KEY_HARDWARE_ACCEL] = HARDWARE_ACCEL_AUTO
        config.setdefault(CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK, False)
        config["version"] = 8
        # save config file
        self.save(config)

    def _migrateV8To9(self, config: dict):
        logger.debug("[Config] Migration from version 8 to 9.")
        config["version"] = 9
        # save config file
        self.save(config)

    def _checkMissingMonitors(self, old_config: dict, template: dict):
        # Extract the monitors from both configurations
        old_monitors = old_config.get("data_source", {}).keys()
        template_monitors = template.get("data_source", {}).keys()
        # Find monitors in the template that are not in the old configuration
        missing_monitors = set(template_monitors) - set(old_monitors)
        if len(missing_monitors) > 0:
            logger.warning(
                f"[Config] There are missing {len(missing_monitors)} monitors in config. Creating default one"
            )
            self._createMissingMonitors(missing_monitors, old_config)

    def _createMissingMonitors(self, keys: set, config: dict):
        # we will set to Default new monitor sources
        for key in keys:
            config["data_source"][key] = config["data_source"]["Default"]
        self.save(config)

    def _checkDefaultSource(self, config: dict):
        # Check if the 'Default' source is empty
        default_source = config["data_source"].get("Default", "")
        mode = config.get("mode")
        if mode == MODE_VIDEO and not os.path.isfile(default_source):
            logger.warning(
                "[Config] Default source is empty or not a valid file. Setting to the first on available."
            )

            # Get all values from the 'data_source' dictionary
            values = list(config["data_source"].values())
            # If there are no values in 'data_source', return early
            if not values:
                return

            # Set the 'Default' source to the first value available
            for value in values:
                if len(value) > 0 and os.path.isfile(value):
                    config["data_source"]["Default"] = value
                    self.save(config)
                    break

    def load(self):
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                json_str = f.read()
                try:
                    config = json.loads(json_str)
                    # migration to version 4 for data_source type change
                    if config.get("version") <= 3 and CONFIG_VERSION >= 4:
                        self._migrateV3To4(config)
                    # migration to version 5 for playlist & shuffle support
                    if config.get("version") <= 4 and CONFIG_VERSION >= 5:
                        self._migrateV4To5(config)
                    # migration to version 6 for independent-per-monitor shuffle
                    if config.get("version") <= 5 and CONFIG_VERSION >= 6:
                        self._migrateV5To6(config)
                    # migration to version 7 for hardware-acceleration preference
                    if config.get("version") <= 6 and CONFIG_VERSION >= 7:
                        self._migrateV6To7(config)
                    # migration to version 8 for hardware-accel auto/on/off modes
                    if config.get("version") <= 7 and CONFIG_VERSION >= 8:
                        self._migrateV7To8(config)
                    # migration to version 9
                    if config.get("version") <= 8 and CONFIG_VERSION >= 9:
                        self._migrateV8To9(config)
                    self._checkDefaultSource(config)
                    self._checkMissingMonitors(config, CONFIG_TEMPLATE)
                    if self._check(config):
                        logs = []
                        logs.append("--------- Config ---------")
                        logs.append(pformat(config, indent=3))
                        logs.append("--------------------------")
                        logs_str = "\n".join(logs)
                        logger.debug(f"[Config] Loaded {CONFIG_PATH}\n{logs_str}")
                        return config
                except json.decoder.JSONDecodeError:
                    logger.debug("[Config] JSONDecodeError")
        return self._invalid()

    def save(self, config):
        old_config = None
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                json_str = f.read()
                try:
                    old_config = json.loads(json_str)
                    if not self._check(old_config):
                        old_config = None
                except json.decoder.JSONDecodeError:
                    old_config = None
        # Skip if the config is identical
        if old_config == config:
            return
        with open(CONFIG_PATH, "w") as f:
            json_str = json.dumps(config, indent=3)
            print(json_str, file=f)
            logs = []
            logs.append("--------- Config ---------")
            logs.append(pformat(config, indent=3))
            logs.append("--------------------------")
            logs_str = "\n".join(logs)
            logger.debug(f"[Config] Saved {CONFIG_PATH}\n{logs_str}")
