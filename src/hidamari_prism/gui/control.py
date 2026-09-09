import logging
import multiprocessing as mp
import os
import subprocess
import sys
import threading
from gettext import gettext as _

# TODO: Port to Gtk4/adwaita someday...
import gi
import requests
import setproctitle

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import yt_dlp
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from pydbus import SessionBus

from hidamari_prism.commons import (
    AUTOSTART_DESKTOP_PATH,
    CONFIG_KEY_BLUR_RADIUS,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_DONATE_ONCE,
    CONFIG_KEY_FIRST_TIME,
    CONFIG_KEY_HARDWARE_ACCEL,
    CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK,
    HARDWARE_ACCEL_AUTO,
    HARDWARE_ACCEL_ON,
    HARDWARE_ACCEL_OFF,
    HARDWARE_ACCEL_OPTIONS,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLISTS,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_SHUFFLE_ACTIVE,
    CONFIG_KEY_SHUFFLE_ENABLED,
    CONFIG_KEY_SHUFFLE_INDEPENDENT,
    CONFIG_KEY_SHUFFLE_INTERVAL,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_VOLUME,
    CONFIG_PATH,
    DBUS_NAME_SERVER,
    LOGGER_NAME,
    MODE_STREAM,
    MODE_VIDEO,
    MODE_WEBPAGE,
    PROJECT,
    SHUFFLE_INTERVAL_MIN_MIN,
    SHUFFLE_INTERVAL_STEP_MIN,
    SHUFFLE_INTERVAL_MAX_MIN,
    TRANSLATION_DOMAIN,
    VIDEO_WALLPAPER_DIR,
)
from hidamari_prism.gui.gui_utils import debounce, get_thumbnail
from hidamari_prism.monitor import Monitors
from hidamari_prism.utils import (
    ConfigUtil,
    ensure_window_state_extension,
    get_video_paths,
    init_translations,
    is_gnome,
    is_static_image,
    is_wayland,
    setup_autostart,
    window_state_extension_installed,
    window_state_extension_ready,
)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{PROJECT}.gui"
APP_TITLE = "Hidamari Prism"
APP_UI_RESOURCE_PATH = "/io/github/swordberry/Hidamari_Prism/control.ui"


class ControlPanel(Gtk.Application):
    def __init__(self, version, pkgdatadir=None, *args, **kwargs):
        super().__init__(
            *args,
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
            **kwargs,
        )
        setproctitle.setproctitle(mp.current_process().name)
        # Builder init
        self.builder = Gtk.Builder()
        self.builder.set_application(self)
        self.builder.set_translation_domain(TRANSLATION_DOMAIN)
        self.builder.add_from_resource(APP_UI_RESOURCE_PATH)
        # Handlers declared in `control.ui``
        signals = {
            "on_volume_changed": self.on_volume_changed,
            "on_streaming_activate": self.on_streaming_activate,
            "on_web_page_activate": self.on_web_page_activate,
            "on_blur_radius_changed": self.on_blur_radius_changed,
            "on_playlist_combo_changed": self.on_playlist_combo_changed,
            "on_wallpaper_search_changed": self.on_wallpaper_search_changed,
            "on_playlist_search_changed": self.on_playlist_search_changed,
            "on_playlist_new": self.on_playlist_new,
            "on_playlist_rename": self.on_playlist_rename,
            "on_playlist_delete": self.on_playlist_delete,
            "on_playlist_add_videos": self.on_playlist_add_videos,
            "on_playlist_remove_selected": self.on_playlist_remove_selected,
            "on_playlist_icon_view_button_press": self.on_playlist_icon_view_button_press,
            "on_playlist_shuffle_toggled": self.on_playlist_shuffle_toggled,
            "on_playlist_interval_changed": self.on_playlist_interval_changed,
            "on_hardware_accel_combo_changed": self.on_hardware_accel_combo_changed,
            "on_donate_clicked": self.on_donate_clicked,
        }
        self.builder.connect_signals(signals)

        # Variables init
        self.version = version
        self.pkgdatadir = pkgdatadir
        self.window = None
        self.server = None
        self.icon_view = None
        self.video_paths = None
        self.all_key = "all"
        self._playlist_refreshing = False
        self._all_video_paths = []
        self._last_wallpaper_query = None

        self.is_autostart = os.path.isfile(AUTOSTART_DESKTOP_PATH)

        self._connect_server()
        self._load_config()

        # initialize monitors
        self.monitors = Monitors()
        # get video paths
        video_paths = self.config[CONFIG_KEY_DATA_SOURCE]
        for monitor in self.monitors.get_monitors():
            # check if monitor exists in paths
            if monitor in video_paths:
                self.monitors.get_monitor(monitor).set_wallpaper(video_paths[monitor])
            else:
                self.monitors.get_monitor(monitor).set_wallpaper(video_paths["Default"])

        self._setup_context_menu()  # setup context menu for selecting monitors

    def _connect_server(self):
        try:
            self.server = SessionBus().get(DBUS_NAME_SERVER)
        except GLib.Error:
            logger.error("[GUI] Couldn't connect to server")

    def _setup_context_menu(self):
        self.contextMenu_monitors = Gtk.Menu()
        self.contextMenu_monitors.show_all()

        for monitor_name, monitor in self.monitors.get_monitors().items():
            item = Gtk.MenuItem(label=_("Set For {monitor}").format(monitor=monitor_name))
            item.connect("activate", self.on_set_as, monitor)
            self.contextMenu_monitors.append(item)

        # add all option
        item = Gtk.MenuItem(label=_("Set For All"))
        item.connect("activate", self.on_set_as, self.all_key)
        self.contextMenu_monitors.append(item)

    def _load_config(self):
        self.config = ConfigUtil().load()

    def _save_config(self):
        ConfigUtil().save(self.config)

    @debounce(1)
    def _save_config_delay(self):
        self._save_config()

    def do_startup(self):
        Gtk.Application.do_startup(self)

        css = b"""
#donate-button {
    background-color: #ffffff;
    color: #000000;
    border-radius: 6px;
    font-weight: bold;
    padding: 4px 16px;
}
"""
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        actions = [
            (
                "local_video_dir",
                lambda *_: subprocess.run(["xdg-open", os.path.realpath(VIDEO_WALLPAPER_DIR)]),
            ),
            ("local_video_refresh", self._reload_icon_view),
            ("local_video_apply", self.on_local_video_apply),
            ("local_web_page_apply", self.on_local_web_page_apply),
            ("play_pause", self.on_play_pause),
            ("feeling_lucky", self.on_feeling_lucky),
            (
                "config",
                lambda *_: subprocess.run(["xdg-open", os.path.realpath(CONFIG_PATH)]),
            ),
            ("about", self.on_about),
            ("donate", self.on_donate),
            ("playlists", self.on_playlists),
            ("playlist_apply", self.on_playlist_apply),
            ("stop_wallpaper", self.on_stop_wallpaper),
            ("quit", self.on_quit),
        ]

        for action_name, handler in actions:
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", handler)
            self.add_action(action)

        statefuls = [
            ("mute", self.config[CONFIG_KEY_MUTE], self.on_mute),
            ("autostart", self.is_autostart, self.on_autostart),
            (
                "static_wallpaper",
                self.config[CONFIG_KEY_STATIC_WALLPAPER],
                self.on_static_wallpaper,
            ),
            (
                "pause_when_maximized",
                self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED],
                self.on_pause_when_maximized,
            ),
            (
                "mute_when_maximized",
                self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED],
                self.on_mute_when_maximized,
            ),
            (
                "shuffle_independent",
                (self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}).get(
                    CONFIG_KEY_SHUFFLE_INDEPENDENT, False
                ),
                self.on_shuffle_independent,
            ),
        ]

        for action_name, state, handler in statefuls:
            action = Gio.SimpleAction.new_stateful(
                action_name, None, GLib.Variant.new_boolean(state)
            )
            action.connect("change-state", handler)
            self.add_action(action)

        if is_wayland() and not is_gnome():
            # On non-GNOME Wayland (KDE, Sway, ...) there is no supported
            # window-state source, so hide the window-state toggles. On GNOME
            # Wayland they work via the bundled GNOME Shell extension.
            self.builder.get_object("TogglePauseWhenMaximized").set_visible(False)
            self.builder.get_object("ToggleMuteWhenMaximized").set_visible(False)

        if not is_gnome():
            # Disable static wallpaper functionality for non-GNOME DE
            self.builder.get_object("ToggleStaticWallpaper").set_visible(False)
            self.builder.get_object("LabelBlurRadius").set_visible(False)
            self.builder.get_object("SpinBlurRadius").set_visible(False)

        self.icon_view = self.builder.get_object("IconView")
        self.icon_view.connect("button-press-event", self.on_icon_view_button_press)
        self._reload_all_widgets()
        self._reload_playlists()

    def do_activate(self):
        if self.window is None:
            self.window: Gtk.ApplicationWindow = self.builder.get_object("ApplicationWindow")
            self.window.set_title("Hidamari Prism")
            self.window.set_application(self)
            self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.present()

        if self.server is None:
            self._show_error(_("Couldn't connect to server"))

        if self.config[CONFIG_KEY_FIRST_TIME]:
            self._show_welcome()
            self.config[CONFIG_KEY_FIRST_TIME] = False
            self._save_config()

        if self.config.get(CONFIG_KEY_DONATE_ONCE, False):
            self.config.pop(CONFIG_KEY_DONATE_ONCE, None)
            self._save_config()
            GLib.timeout_add(500, self.on_donate)

    def _show_welcome(self):
        # Welcome dialog
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            destroy_with_parent=True,
            text=_("Welcome to Hidamari Prism 🤗"),
            message_type=Gtk.MessageType.INFO,
            #    secondary_text="You can bring up the Menu by <b>Right click</b> on the desktop",
            secondary_text=_(
                "Quickstart for adding wallpapers:\n"
                " ・Click the folder icon to open the Hidamari Prism folder\n"
                " ・Put your videos or images there\n"
                " ・Click the refresh button"
            ),
            secondary_use_markup=True,
            buttons=Gtk.ButtonsType.OK,
        )
        dialog.run()
        dialog.destroy()

    def _show_error(self, error):
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            destroy_with_parent=True,
            text=_("Oops!"),
            message_type=Gtk.MessageType.ERROR,
            secondary_text=error,
            buttons=Gtk.ButtonsType.OK,
        )
        dialog.run()
        dialog.destroy()

    def on_local_video_apply(self, *_args):
        selected = self.icon_view.get_selected_items()
        if len(selected) != 0:
            # show menu
            self.contextMenu_monitors.show_all()
            self.contextMenu_monitors.popup(None, None, None, None, 0, Gtk.get_current_event_time())
        else:
            dialog = Gtk.MessageDialog(
                parent=self.window,
                modal=True,
                destroy_with_parent=True,
                text=_("No Wallpaper Selected"),
                message_type=Gtk.MessageType.INFO,
                secondary_text=_("There are no wallpapers selected.\nPlease choose one first."),
                secondary_use_markup=True,
                buttons=Gtk.ButtonsType.OK,
            )
            dialog.run()
            dialog.destroy()

    def on_set_as(self, widget, monitor):
        index = self.icon_view.get_selected_items()[0].get_indices()[0]
        video_path = self.video_paths[index]
        logger.info(f"[GUI] Wallpaper Set To {video_path} For Monitor {monitor}")
        self.config[CONFIG_KEY_MODE] = MODE_VIDEO
        paths = self.config[CONFIG_KEY_DATA_SOURCE] if not None else []
        # all option
        if monitor == self.all_key:
            for name, monitor in self.monitors.get_monitors().items():
                paths[name] = video_path
                monitor.set_wallpaper(video_path)
        else:
            paths[monitor.name] = video_path
            self.monitors.get_monitor(monitor.name).set_wallpaper(video_path)

        # also update the Default video
        paths["Default"] = video_path
        self.config[CONFIG_KEY_DATA_SOURCE] = paths
        self._save_config()
        if self.server is not None:
            self.server.video(video_path, monitor.name)

    def on_local_web_page_apply(self, *_args):
        file_chooser: Gtk.FileChooserButton = self.builder.get_object("FileChooser")
        choose: Gio.File = file_chooser.get_file()
        if choose is None:
            self._show_error(_("Please choose a HTML file"))
            return
        file_path = choose.get_path()
        logger.info(f"[GUI] Local Webpage: {file_path}")
        self.config[CONFIG_KEY_MODE] = MODE_WEBPAGE
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = (
            file_path  #! we dont want to break the config, webpage and stream modes will kept in Default source
        )
        self._save_config()
        if self.server is not None:
            self.server.webpage(choose.get_path())

    def on_play_pause(self, *_):
        if self.server is None:
            return
        prev_state = self.server.is_paused_by_user
        self.server.is_paused_by_user = not prev_state
        if not prev_state:
            self.server.pause_playback()
        else:
            self.server.start_playback()

    def on_feeling_lucky(self, *_):
        if self.server is not None:
            self.server.feeling_lucky()

    def set_mute_toggle_icon(self):
        toggle_icon: Gtk.Image = self.builder.get_object("ToggleMuteIcon")
        volume, is_mute = self.config[CONFIG_KEY_VOLUME], self.config[CONFIG_KEY_MUTE]
        if volume == 0 or is_mute:
            icon_name = "audio-volume-muted-symbolic"
        elif volume < 30:
            icon_name = "audio-volume-low-symbolic"
        elif volume < 60:
            icon_name = "audio-volume-medium-symbolic"
        else:
            icon_name = "audio-volume-high-symbolic"
        toggle_icon.set_from_icon_name(icon_name=icon_name, size=0)

    def set_scale_volume_sensitive(self):
        scale = self.builder.get_object("ScaleVolume")
        if self.config[CONFIG_KEY_MUTE]:
            scale.set_sensitive(False)
        else:
            scale.set_sensitive(True)

    def set_spin_blur_radius_sensitive(self):
        spin = self.builder.get_object("SpinBlurRadius")
        if self.config[CONFIG_KEY_STATIC_WALLPAPER]:
            spin.set_sensitive(True)
        else:
            spin.set_sensitive(False)

    def on_volume_changed(self, adjustment):
        self.config[CONFIG_KEY_VOLUME] = int(adjustment.get_value())
        logger.info(f"[GUI] Volume: {self.config[CONFIG_KEY_VOLUME]}")
        self._save_config_delay()
        if self.server is not None:
            self.server.volume = self.config[CONFIG_KEY_VOLUME]
        self.set_mute_toggle_icon()

    def on_blur_radius_changed(self, adjustment):
        self.config[CONFIG_KEY_BLUR_RADIUS] = int(adjustment.get_value())
        logger.info(f"[GUI] Blur radius: {self.config[CONFIG_KEY_BLUR_RADIUS]}")
        self._save_config_delay()
        if self.server is not None:
            self.server.blur_radius = self.config[CONFIG_KEY_BLUR_RADIUS]

    def on_mute(self, action, state):
        action.set_state(state)
        self.config[CONFIG_KEY_MUTE] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_mute = self.config[CONFIG_KEY_MUTE]
        self.set_mute_toggle_icon()
        self.set_scale_volume_sensitive()

    def on_autostart(self, action, state):
        action.set_state(state)
        self.is_autostart = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        setup_autostart(state)

    def on_static_wallpaper(self, action, state):
        action.set_state(state)
        self.config[CONFIG_KEY_STATIC_WALLPAPER] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_static_wallpaper = self.config[CONFIG_KEY_STATIC_WALLPAPER]
        self.set_spin_blur_radius_sensitive()

    def on_pause_when_maximized(self, action, state):
        action.set_state(state)
        state = bool(state)
        if state and not self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]:
            if not self._ensure_wayland_window_detection():
                # User denied (or GNOME refused the extension): keep it off.
                action.set_state(GLib.Variant.new_boolean(False))
                self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] = False
                self._save_config()
                return
        self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] = state
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_pause_when_maximized = self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]

    def on_mute_when_maximized(self, action, state):
        action.set_state(state)
        state = bool(state)
        if state and not self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]:
            if not self._ensure_wayland_window_detection():
                # User denied (or GNOME refused the extension): keep it off.
                action.set_state(GLib.Variant.new_boolean(False))
                self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED] = False
                self._save_config()
                return
        self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED] = state
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_mute_when_maximized = self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]

    def _ensure_wayland_window_detection(self):
        """Under GNOME Wayland, apps cannot see other windows, so the bundled
        GNOME Shell extension must be enabled for the window-state toggles to
        do anything. Ask the user for permission first, then install + enable
        it. Returns True when window detection is ready."""
        if not (is_wayland() and is_gnome()):
            return True
        if window_state_extension_ready():
            return True
        if window_state_extension_installed():
            # Already set up (e.g. the extension was installed on a previous
            # toggle but hasn't been picked up by GNOME Shell yet, or it was
            # enabled in GNOME Extensions settings). Don't re-install or show
            # the confirmation popup again -- just proceed.
            logger.info("[GUI] Window-detection extension already installed; skipping prompt")
            return True

        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Use a GNOME Shell extension for window detection?"),
        )
        dialog.format_secondary_text(
            _(
                "On Wayland, GNOME does not let apps see other windows. "
                "Hidamari Prism bundles a small extension for detecting "
                "maximized and fullscreen windows. Enabling it installs it "
                "into your GNOME Shell and turns it on; you can disable or "
                "remove it anytime in the GNOME Extensions settings."
            )
        )
        dialog.add_button(_("Deny"), Gtk.ResponseType.REJECT)
        dialog.add_button(_("Confirm"), Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        try:
            response = dialog.run()
        finally:
            dialog.destroy()

        if response != Gtk.ResponseType.ACCEPT:
            logger.info("[GUI] User denied installing the GNOME Shell extension")
            return False

        ok, message = ensure_window_state_extension(self.pkgdatadir)
        if ok and message == "RELOAD_REQUIRED":
            # Extension files are installed & enabled, but GNOME Shell only
            # discovers brand-new extension folders on session start -- so the
            # feature takes effect after the user logs back in.
            restart_dialog = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=_("Window detection installed"),
            )
            restart_dialog.format_secondary_text(
                _(
                    "GNOME Shell only notices a newly installed extension "
                    "after you restart your session. Restart (or log out and "
                    "back in) once and “Pause when maximized window” / "
                    "“Pause when fullscreen” will start working on Wayland. "
                    "You can also manage or remove the extension anytime in "
                    "the GNOME Extensions settings."
                )
            )
            restart_dialog.run()
            restart_dialog.destroy()
            return True
        if not ok:
            logger.error(f"[GUI] {message}")
            error_dialog = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=_("Couldn't enable GNOME window detection"),
            )
            error_dialog.format_secondary_text(message)
            error_dialog.run()
            error_dialog.destroy()
            return False

        logger.info("[GUI] GNOME Shell window-detection extension installed+enabled")
        return True

    def on_shuffle_independent(self, action, state):
        action.set_state(state)
        _playlists, shuffle = self._playlists_state()
        shuffle[CONFIG_KEY_SHUFFLE_INDEPENDENT] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.reload_shuffle_settings()

    def on_hardware_accel_combo_changed(self, widget):
        combo: Gtk.ComboBoxText = self.builder.get_object("HardwareAccelCombo")
        mode = combo.get_active_id()
        if mode is None or mode not in HARDWARE_ACCEL_OPTIONS:
            return
        if mode == self.config.get(CONFIG_KEY_HARDWARE_ACCEL, HARDWARE_ACCEL_AUTO):
            return
        self.config[CONFIG_KEY_HARDWARE_ACCEL] = mode
        # Changing the preference clears a previous auto-fallback so the new
        # choice takes effect immediately.
        self.config[CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK] = False
        logger.info(f"[GUI] hardware_accel: {mode}")
        self._save_config()
        if self.server is not None:
            self.server.apply_hardware_accel()

    def on_about(self, *_):
        about_dialog: Gtk.AboutDialog = self.builder.get_object("AboutDialog")
        about_dialog.set_transient_for(self.window)
        about_dialog.set_version(self.version)
        about_dialog.set_modal(True)
        about_dialog.present()

    def on_donate(self, *_):
        donate_popover: Gtk.Popover = self.builder.get_object("PopoverDonate")
        donate_popover.set_relative_to(self.builder.get_object("menubutton1"))
        donate_popover.popup()

    @staticmethod
    def on_donate_clicked(*_):
        Gio.AppInfo.launch_default_for_uri("https://ko-fi.com/swordberry", None)

    def on_playlists(self, *_args):
        # Switch to the integrated Playlists tab rather than a floating window.
        stack: Gtk.Stack = self.builder.get_object("stack1")
        if stack is not None:
            self._reload_playlists()
            stack.set_visible_child_name("playlists")

    def _playlists_state(self):
        """Return (playlists_dict, shuffle_dict), ensuring both exist in config."""
        if CONFIG_KEY_PLAYLISTS not in self.config or not isinstance(
            self.config[CONFIG_KEY_PLAYLISTS], dict
        ):
            self.config[CONFIG_KEY_PLAYLISTS] = {}
        if CONFIG_KEY_SHUFFLE not in self.config or not isinstance(
            self.config.get(CONFIG_KEY_SHUFFLE), dict
        ):
            self.config[CONFIG_KEY_SHUFFLE] = {}
        return self.config[CONFIG_KEY_PLAYLISTS], self.config[CONFIG_KEY_SHUFFLE]

    @staticmethod
    def _signal_block(widget, handler):
        try:
            return widget.handler_block_by_func(handler)
        except TypeError:
            return 0

    @staticmethod
    def _signal_unblock(widget, handler):
        try:
            return widget.handler_unblock_by_func(handler)
        except TypeError:
            return 0

    def _reload_playlists(self, *_):
        playlists, shuffle = self._playlists_state()
        combo: Gtk.ComboBox = self.builder.get_object("PlaylistCombo")
        self._signal_block(combo, self.on_playlist_combo_changed)
        # Model columns: 0=playlist name (id), 1=display text (ellipsized).
        store = Gtk.ListStore(str, str)
        for name in playlists.keys():
            store.append([name, name])
        combo.set_model(store)
        combo.set_id_column(0)
        active = shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE, "")
        if active in playlists:
            combo.set_active_id(active)
        else:
            combo.set_active(0 if playlists else -1)
        self._signal_unblock(combo, self.on_playlist_combo_changed)
        self._refresh_playlist_tab()

    def _current_playlist(self):
        combo: Gtk.ComboBox = self.builder.get_object("PlaylistCombo")
        name = combo.get_active_id()
        if not name:
            return None, None
        playlists, _shuffle = self._playlists_state()
        return name, playlists.get(name, [])

    def _refresh_playlist_tab(self):
        if self._playlist_refreshing:
            return
        self._playlist_refreshing = True
        try:
            self._refresh_playlist_tab_impl()
        finally:
            self._playlist_refreshing = False

    def _refresh_playlist_tab_impl(self):
        playlists, shuffle = self._playlists_state()
        name, videos = self._current_playlist()

        # Populate the thumbnail grid. Columns: 0=pixbuf, 1=basename(text), 2=full path.
        query = self._search_query("PlaylistSearch")
        if query:
            videos = [v for v in (videos or []) if query in os.path.basename(v).lower()]
        store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str)
        icon_view: Gtk.IconView = self.builder.get_object("PlaylistIconView")
        icon_view.set_model(store)
        icon_view.set_pixbuf_column(0)
        icon_view.set_text_column(1)
        for idx, video_path in enumerate(videos or []):
            if os.path.isfile(video_path):
                pixbuf = Gtk.IconTheme().get_default().load_icon("video-x-generic", 160, 0)
                store.append([pixbuf, os.path.basename(video_path), video_path])
                thread = threading.Thread(target=get_thumbnail, args=(video_path, store, idx))
                thread.daemon = True
                thread.start()

        # Shuffle switch state.
        switch: Gtk.Switch = self.builder.get_object("PlaylistShuffleSwitch")
        shuffle_active = shuffle.get(CONFIG_KEY_SHUFFLE_ENABLED, False) and name == shuffle.get(
            CONFIG_KEY_SHUFFLE_ACTIVE, ""
        )
        self._signal_block(switch, self.on_playlist_shuffle_toggled)
        switch.set_active(bool(shuffle_active))
        self._signal_unblock(switch, self.on_playlist_shuffle_toggled)

        # Interval spin value.
        interval = shuffle.get(CONFIG_KEY_SHUFFLE_INTERVAL, SHUFFLE_INTERVAL_STEP_MIN) or SHUFFLE_INTERVAL_STEP_MIN
        spin: Gtk.SpinButton = self.builder.get_object("PlaylistIntervalSpin")
        self._signal_block(spin, self.on_playlist_interval_changed)
        spin.set_value(min(max(int(interval), SHUFFLE_INTERVAL_MIN_MIN), SHUFFLE_INTERVAL_MAX_MIN))
        self._signal_unblock(spin, self.on_playlist_interval_changed)

        # Enable/disable widgets based on whether a playlist is selected.
        sensitive = bool(name)
        for obj in ("PlaylistAddBtn", "PlaylistRemoveBtn", "PlaylistShuffleSwitch", "PlaylistIntervalSpin", "PlaylistApplyBtn"):
            w = self.builder.get_object(obj)
            if w is not None:
                w.set_sensitive(sensitive)

    def on_playlist_combo_changed(self, *_args):
        self._refresh_playlist_tab()

    def on_playlist_new(self, *_args):
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_type=Gtk.MessageType.QUESTION,
            text=_("New playlist name"),
        )
        entry = Gtk.Entry()
        entry.set_activates_default(True)
        dialog.get_content_area().pack_end(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not name:
            return
        playlists, _shuffle = self._playlists_state()
        if name not in playlists:
            playlists[name] = []
        self._reload_playlists()
        combo: Gtk.ComboBox = self.builder.get_object("PlaylistCombo")
        combo.set_active_id(name)

    def on_playlist_rename(self, *_args):
        name, _videos = self._current_playlist()
        if not name:
            return
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            message_type=Gtk.MessageType.QUESTION,
            text=_("Rename playlist"),
        )
        entry = Gtk.Entry()
        entry.set_text(name)
        entry.set_activates_default(True)
        dialog.get_content_area().pack_end(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        new_name = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not new_name or new_name == name:
            return
        playlists, shuffle = self._playlists_state()
        playlists[new_name] = playlists.pop(name)
        if shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE) == name:
            shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = new_name
        self._save_config_delay()
        self._reload_playlists()
        combo: Gtk.ComboBox = self.builder.get_object("PlaylistCombo")
        combo.set_active_id(new_name)

    def on_playlist_delete(self, *_args):
        name, _videos = self._current_playlist()
        if not name:
            return
        playlists, shuffle = self._playlists_state()
        playlists.pop(name, None)
        if shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE) == name:
            shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = ""
        self._save_config_delay()
        self._reload_playlists()

    def _pick_wallpaper_files(self):
        """Open the media file chooser; return the selected paths ([] if cancelled)."""
        dialog = Gtk.FileChooserDialog(
            title=_("Add Wallpapers"),
            transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.set_select_multiple(True)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        # Accept videos and common image formats.
        media_filter = Gtk.FileFilter()
        media_filter.set_name(_("Wallpapers (Videos and Images)"))
        for pattern in (
            "*.mp4", "*.mkv", "*.webm", "*.ogv", "*.avi", "*.mov", "*.m4v", "*.mpg", "*.mpeg",
            "*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp", "*.webp", "*.svg",
        ):
            media_filter.add_pattern(pattern)
        dialog.add_filter(media_filter)
        all_filter = Gtk.FileFilter()
        all_filter.set_name(_("All Files"))
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)
        response = dialog.run()
        paths = dialog.get_filenames() if response == Gtk.ResponseType.OK else []
        dialog.destroy()
        return paths

    def on_playlist_add_videos(self, *_args):
        name, videos = self._current_playlist()
        if not name:
            return
        paths = self._pick_wallpaper_files()
        playlists, _shuffle = self._playlists_state()
        existing = set(playlists.get(name, []))
        for path in paths:
            if path not in existing:
                playlists.setdefault(name, []).append(path)
                existing.add(path)
        self._save_config_delay()
        self._refresh_playlist_tab()

    def on_playlist_remove_selected(self, *_args):
        name, _videos = self._current_playlist()
        if not name:
            return
        icon_view: Gtk.IconView = self.builder.get_object("PlaylistIconView")
        model = icon_view.get_model()
        if model is None:
            return
        paths = set()
        for tp in icon_view.get_selected_items():
            it = model.get_iter(tp)
            if it:
                paths.add(model.get_value(it, 2))
        if not paths:
            return
        playlists, _shuffle = self._playlists_state()
        playlists[name] = [v for v in playlists.get(name, []) if v not in paths]
        self._save_config_delay()
        self._refresh_playlist_tab()

    def _playlist_selected_paths(self):
        """Return the list of selected video paths in the playlist grid."""
        icon_view: Gtk.IconView = self.builder.get_object("PlaylistIconView")
        model = icon_view.get_model()
        if model is None:
            return []
        result = []
        for tp in icon_view.get_selected_items():
            it = model.get_iter(tp)
            if it:
                result.append(model.get_value(it, 2))
        return result

    def on_playlist_apply(self, *_args):
        playlists, shuffle = self._playlists_state()
        self.config[CONFIG_KEY_PLAYLISTS] = playlists
        # Arming the currently-selected playlist means it plays in order when
        # shuffle is off, or shuffles when it is on.
        name, _videos = self._current_playlist()
        if name and name in playlists:
            shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = name
        self.config[CONFIG_KEY_SHUFFLE] = shuffle
        self._save_config()
        selected = self._playlist_selected_paths()
        if self.server is None:
            return
        independent = shuffle.get(CONFIG_KEY_SHUFFLE_INDEPENDENT, False)
        if shuffle.get(CONFIG_KEY_SHUFFLE_ENABLED):
            if independent:
                # Independent mode: one selected wallpaper becomes the
                # main/"Default" monitor's seed, while every other monitor
                # shuffles freely. Just leave shuffle to pick the rest.
                if len(selected) == 1:
                    self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = selected[0]
                    self._save_config()
                self.server.apply_playlist_shuffle()
            elif len(selected) == 1:
                # Non-independent shuffle: apply the single selected wallpaper to
                # every display (shuffle will take over again on the next tick).
                self.server.play_playlist_video(selected[0])
            else:
                self.server.apply_playlist_shuffle()
        elif len(selected) == 1:
            # Ordered playback (shuffle off): seed the rotation with the
            # selected wallpaper, then continue through the playlist in order.
            if isinstance(self.config[CONFIG_KEY_DATA_SOURCE], dict):
                self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = selected[0]
                self._save_config()
            self.server.apply_playlist_shuffle()
        else:
            self.server.apply_playlist_shuffle()

    def on_playlist_icon_view_button_press(self, widget, event):
        if event.button == Gdk.BUTTON_SECONDARY:  # Right click -> play that video
            path_info = widget.get_path_at_pos(event.x, event.y)
            if path_info is not None:
                tree_path = Gtk.TreePath(path_info[0])
                widget.grab_focus()
                widget.select_path(tree_path)
                model = widget.get_model()
                it = model.get_iter(tree_path)
                video_path = model.get_value(it, 2) if it else None
                if video_path and self.server is not None:
                    self.server.play_playlist_video(video_path)
                return True
        return False

    def on_playlist_shuffle_toggled(self, widget, state, *_args):
        name, _videos = self._current_playlist()
        if not name:
            return False
        playlists, shuffle = self._playlists_state()
        shuffle[CONFIG_KEY_SHUFFLE_ENABLED] = bool(state)
        if state:
            shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = name
        # Turning shuffle off deliberately keeps the playlist armed (active), so
        # it keeps playing -- but in order instead of randomly.
        self._save_config_delay()
        return False

    def on_playlist_interval_changed(self, *_args):
        spin: Gtk.SpinButton = self.builder.get_object("PlaylistIntervalSpin")
        _playlists, shuffle = self._playlists_state()
        shuffle[CONFIG_KEY_SHUFFLE_INTERVAL] = int(
            min(max(spin.get_value(), SHUFFLE_INTERVAL_MIN_MIN), SHUFFLE_INTERVAL_MAX_MIN)
        )
        self._save_config_delay()

    def _check_url(self, url):
        # Check if the url is valid
        try:
            response = requests.get(url)
        except requests.exceptions.RequestException as e:
            logger.error(f"[GUI] Failed to access {url}. Error:\n{e}")
            self._show_error(_("Failed to access {url}. Error:\n{error}").format(url=url, error=e))
            return False
        if response.status_code >= 400:
            logger.error(f"[GUI] Failed to access {url}. Error code: {response.status_code}")
            self._show_error(
                _("Failed to access {url}. Error code: {code}").format(
                    url=url, code=response.status_code
                )
            )
            return False
        return True

    def _check_yt_dlp(self, raw_url):
        # Check if the url is valid (yt_dlp)
        try:
            with yt_dlp.YoutubeDL({"noplaylist": True}) as ydl:
                ydl.extract_info(raw_url, download=False)
        except yt_dlp.utils.DownloadError as e:
            s = " ".join(str(e).split(" ")[1:])
            logger.error(f"[GUI] Failed to stream {raw_url}. Error:\n{s}")
            self._show_error(
                _("Failed to stream {url}. Error:\n{error}").format(url=raw_url, error=s)
            )
            return False
        return True

    def on_streaming_activate(self, entry: Gtk.Entry, *_):
        url = entry.get_text()
        if not self._check_yt_dlp(url):
            return
        logger.info(f"[GUI] Streaming: {url}")
        self.config[CONFIG_KEY_MODE] = MODE_STREAM
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = (
            url  #! we dont want to break the config, webpage and stream modes will kept in Default source
        )
        self._save_config()
        if self.server is not None:
            self.server.stream(url)

    def on_web_page_activate(self, entry: Gtk.Entry, *_):
        url = entry.get_text()
        if not self._check_url(url):
            return
        logger.info(f"[GUI] Webpage: {url}")
        self.config[CONFIG_KEY_MODE] = MODE_WEBPAGE
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = (
            url  #! we dont want to break the config, webpage and stream modes will kept in Default source
        )
        self._save_config()
        if self.server is not None:
            self.server.webpage(url)

    def on_icon_view_button_press(self, widget, event):
        if event.button == Gdk.BUTTON_SECONDARY:  # Right click
            path_info = widget.get_path_at_pos(event.x, event.y)
            if path_info is not None:
                tree_path = Gtk.TreePath(path_info[0])
                self.icon_view.grab_focus()
                widget.select_path(tree_path)
                self.contextMenu_monitors.show_all()
                self.contextMenu_monitors.popup(
                    None, None, None, None, 0, Gtk.get_current_event_time()
                )
                return True
        return False

    def on_stop_wallpaper(self, *_):
        if self.server is not None:
            try:
                self.server.stop_wallpaper()
            except GLib.Error:
                pass

    def on_quit(self, *_):
        if self.server is not None:
            try:
                self.server.quit()
            except GLib.Error:
                # Ignore NoReply error
                pass
        self.quit()

    def _reload_all_widgets(self):
        self._reload_icon_view()
        self.set_mute_toggle_icon()
        self.set_scale_volume_sensitive()
        self.set_spin_blur_radius_sensitive()
        toggle_mute: Gtk.ToggleButton = self.builder.get_object("ToggleMute")
        toggle_mute.set_state = self.config[CONFIG_KEY_MUTE]

        scale_volume: Gtk.Scale = self.builder.get_object("ScaleVolume")
        adjustment_volume: Gtk.Adjustment = self.builder.get_object("AdjustmentVolume")
        # Temporary block signal
        adjustment_volume.handler_block_by_func(self.on_volume_changed)
        scale_volume.set_value(self.config[CONFIG_KEY_VOLUME])
        adjustment_volume.handler_unblock_by_func(self.on_volume_changed)

        spin_blur_radius: Gtk.Scale = self.builder.get_object("SpinBlurRadius")
        adjustment_blur: Gtk.Adjustment = self.builder.get_object("AdjustmentBlur")
        # Temporary block signal
        adjustment_blur.handler_block_by_func(self.on_blur_radius_changed)
        spin_blur_radius.set_value(self.config[CONFIG_KEY_BLUR_RADIUS])
        adjustment_blur.handler_unblock_by_func(self.on_blur_radius_changed)

        toggle_mute: Gtk.ToggleButton = self.builder.get_object("ToggleAutostart")
        toggle_mute.set_state = self.is_autostart

        self._reload_hardware_accel_combo()

    def _reload_hardware_accel_combo(self):
        combo: Gtk.ComboBoxText = self.builder.get_object("HardwareAccelCombo")
        if combo is None:
            return
        self._signal_block(combo, self.on_hardware_accel_combo_changed)
        combo.remove_all()
        combo.append(HARDWARE_ACCEL_AUTO, _("Auto (recommended)"))
        combo.append(HARDWARE_ACCEL_ON, _("On"))
        combo.append(HARDWARE_ACCEL_OFF, _("Off"))
        mode = self.config.get(CONFIG_KEY_HARDWARE_ACCEL, HARDWARE_ACCEL_AUTO)
        if mode not in HARDWARE_ACCEL_OPTIONS:
            mode = HARDWARE_ACCEL_AUTO
        combo.set_active_id(mode)
        self._signal_unblock(combo, self.on_hardware_accel_combo_changed)

    def _reload_icon_view(self, *_):
        self._all_video_paths = get_video_paths()
        self._build_icon_view()

    def _build_icon_view(self):
        query = self._search_query("WallpaperSearch")
        if query == self._last_wallpaper_query and self.icon_view is not None:
            return
        self._last_wallpaper_query = query
        # Keep the filtered list parallel to the visible thumbnail rows, since
        # `on_set_as` maps selected row indices straight onto this list.
        self.video_paths = [
            path for path in self._all_video_paths if query in os.path.basename(path).lower()
        ]
        list_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str)
        self.icon_view: Gtk.IconView = self.builder.get_object("IconView")
        self.icon_view.set_pixbuf_column(0)
        self.icon_view.set_text_column(1)
        self.icon_view.set_model(list_store)
        for idx, video_path in enumerate(self.video_paths):
            # Show a preview thumbnail for videos; static images get their own
            # thumbnail and fall back to a generic image icon.
            icon_name = "image-x-generic" if is_static_image(video_path) else "video-x-generic"
            pixbuf = Gtk.IconTheme().get_default().load_icon(icon_name, 96, 0)
            list_store.append([pixbuf, os.path.basename(video_path)])
            thread = threading.Thread(target=get_thumbnail, args=(video_path, list_store, idx))
            thread.daemon = True
            thread.start()

    def on_wallpaper_search_changed(self, *_):
        self._build_icon_view()

    def on_playlist_search_changed(self, *_):
        self._refresh_playlist_tab()

    def _search_query(self, widget_id):
        """Return lower-cased, trimmed text of a search entry (or "")."""
        widget = self.builder.get_object(widget_id)
        if widget is None:
            return ""
        return widget.get_text().strip().lower()


def _find_gresource(pkgdatadir):
    """Locate hidamari_prism.gresource: the launcher-provided prefix first, then the
    standard XDG data dirs (so `python -m hidamari_prism` works for any install)."""
    candidates = [os.path.join(pkgdatadir, "hidamari_prism.gresource")]
    candidates += [
        os.path.join(d, "hidamari_prism", "hidamari_prism.gresource")
        for d in (GLib.get_user_data_dir(), *GLib.get_system_data_dirs())
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def main(version="devel", pkgdatadir="/usr/share/hidamari_prism", localedir="/usr/share/locale"):
    init_translations(localedir)
    gresource = _find_gresource(pkgdatadir)
    if gresource is None:
        logger.error("[GUI] Couldn't find hidamari_prism.gresource. Is Hidamari_Prism installed?")
        return
    Gio.Resource.load(gresource)._register()
    Gtk.IconTheme.get_default().add_resource_path("/io/github/swordberry/Hidamari_Prism/icons")

    app = ControlPanel(version, pkgdatadir)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
