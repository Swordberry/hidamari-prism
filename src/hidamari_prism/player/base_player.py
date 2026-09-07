import logging
import multiprocessing as mp
import sys
from abc import abstractmethod

import gi
import setproctitle

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk
from pydbus import SessionBus

from hidamari_prism.commons import DBUS_NAME_PLAYER, LOGGER_NAME, PROJECT
from hidamari_prism.utils import gnome_desktop_icon_workaround

logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{PROJECT}.player"


class DummyWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class BasePlayer(Gtk.Application):
    """
    <node>
    <interface name='io.github.swordberry.hidamari_prism.player'>
        <property name="mode" type="s" access="read"/>
        <property name="data_source" type="s" access="readwrite"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='stop_wallpaper'/>
        <method name='start_wallpaper'/>
        <method name='quit_player'/>
    </interface>
    </node>
    """

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE, **kwargs
        )
        setproctitle.setproctitle(mp.current_process().name)
        self.windows = dict()
        self._wallpaper_visible = True
        self._monitor_detect()

    def _monitor_detect(self):
        display = Gdk.Display.get_default()
        screen = display.get_default_screen()

        for i in range(display.get_n_monitors()):
            monitor = display.get_monitor(i)
            if monitor not in self.windows:
                self.windows[monitor] = None

        screen.connect("size-changed", self._on_size_changed)
        display.connect("monitor-added", self._on_monitor_added)
        display.connect("monitor-removed", self._on_monitor_removed)

    def new_window(self, gdk_monitor):
        # Override here for different window
        # NOTE: Don't forget to set the application=self, otherwise the application will quit immediately lol
        return DummyWindow(application=self)

    def _on_size_changed(self, *args):
        logger.info("[Player] size-changed")
        for monitor, window in self.windows.items():
            if window is None:
                continue
            rect = monitor.get_geometry()
            window.set_size_request(rect.width, rect.height)
            window.move(rect.x, rect.y)

    def _on_monitor_added(self, _, gdk_monitor, *args):
        logger.info("[Player] monitor-added")
        self.windows[gdk_monitor] = None
        self.do_activate()

    def _on_monitor_removed(self, _, gdk_monitor, *args):
        logger.info("[Player] monitor-removed")
        del self.windows[gdk_monitor]

    def do_startup(self):
        Gtk.Application.do_startup(self)

    def do_activate(self):
        for monitor in self.windows:
            if not self.windows[monitor]:
                window = self.new_window(monitor)
                window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
                rect = monitor.get_geometry()
                x, y, width, height = rect.x, rect.y, rect.width, rect.height
                window.set_size_request(width, height)
                window.move(x, y)
                self.windows[monitor] = window
                # Only present newly-created windows. Re-presenting an
                # already-shown full-screen desktop window forces the compositor
                # to relayer/recomposite and makes the taskbar/top bar flicker,
                # especially when monitors are added/removed on the fly.
                self.windows[monitor].present()
        # Workaround for DING extension
        gnome_desktop_icon_workaround()

    @property
    @abstractmethod
    def mode(self):
        pass

    @property
    @abstractmethod
    def data_source(self):
        pass

    @data_source.setter
    def data_source(self, data_source):
        pass

    @property
    @abstractmethod
    def volume(self):
        pass

    @volume.setter
    def volume(self, volume):
        pass

    @property
    @abstractmethod
    def is_mute(self):
        pass

    @is_mute.setter
    def is_mute(self, is_mute):
        pass

    @property
    @abstractmethod
    def is_playing(self):
        pass

    @abstractmethod
    def pause_playback(self):
        pass

    @abstractmethod
    def start_playback(self):
        pass

    def stop_wallpaper(self):
        """Hide every wallpaper window so the OS wallpaper shows through.

        The player keeps running; the active monitors stay tracked in
        ``self.windows`` so the wallpaper can be brought back instantly.
        """
        logger.info("[Player] stopping wallpaper")
        self._wallpaper_visible = False
        for window in self.windows.values():
            if window is not None:
                window.hide()

    def start_wallpaper(self):
        """Re-present every wallpaper window after ``stop_wallpaper``."""
        logger.info("[Player] starting wallpaper")
        self._wallpaper_visible = True
        for monitor, window in self.windows.items():
            if window is None:
                continue
            rect = monitor.get_geometry()
            window.set_size_request(rect.width, rect.height)
            window.move(rect.x, rect.y)
            window.present()

    def ensure_wallpaper_visible(self):
        """Re-present hidden wallpaper windows if the wallpaper was stopped.

        Called after an apply/mode change so a freshly applied wallpaper shows
        again even when it had been turned off with ``stop_wallpaper``.
        """
        if not self._wallpaper_visible:
            self.start_wallpaper()

    def quit_player(self):
        self.quit()


def main():
    bus = SessionBus()
    app = BasePlayer()
    try:
        bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
