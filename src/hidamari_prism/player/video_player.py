import ctypes
import glob
import logging
import os
import pathlib
import random
import subprocess
import sys
import time
from threading import Timer, Thread

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import vlc
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import Image, ImageFilter
from pydbus import SessionBus

from hidamari_prism.commons import (
    CONFIG_DIR,
    DBUS_NAME_PLAYER,
    DBUS_NAME_SERVER,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_FADE_DURATION_SEC,
    CONFIG_KEY_FADE_INTERVAL,
    CONFIG_KEY_HARDWARE_ACCEL,
    CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK,
    HARDWARE_ACCEL_AUTO,
    HARDWARE_ACCEL_ON,
    HARDWARE_ACCEL_OFF,
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
    LOGGER_NAME,
    MODE_STREAM,
    MODE_VIDEO,
)
from hidamari_prism.menu import build_menu
from hidamari_prism.player.base_player import BasePlayer
from hidamari_prism.utils import (
    ActiveHandler,
    ConfigUtil,
    WaylandWindowHandler,
    WindowHandler,
    is_flatpak,
    is_gnome,
    is_static_image,
    is_wayland,
)
from hidamari_prism.yt_utils import get_best_audio, get_formats, get_optimal_video

logger = logging.getLogger(LOGGER_NAME)


# ---------------------------------------------------------------------------
# Hardware decode health watchdog.
#
# Some GPUs (notably AMD RDNA3 integrated + VA-API through VLC's GL interop)
# advertise a hardware decoder but corrupt reference frames at runtime,
# producing streams of "get_buffer() failed" / "no frame!" / "decode_slice"
# errors from the avcodec module. The wallpaper then freezes/stutters. VLC
# will not fall back on its own once a hardware decoder is chosen, so we watch
# those errors and, when a hardware decoder keeps failing, switch the whole
# player to software decode automatically -- preserving "power-efficient where
# it works, stable everywhere" without any user action.
#
# FFmpeg writes these errors straight to the process stderr (they show up in
# the systemd journal), which libvlc's own log callback does not see. So we
# redirect the player's stderr to a file at startup and scan it for failures.
# ---------------------------------------------------------------------------
_HW_FAILURE_MARKERS = (
    "get_buffer() failed",
    "thread_get_buffer() failed",
    "no frame",
    "decode_slice_header error",
)
_HW_FAILURE_TRIGGER = 4  # one faulty-decode episode is enough to fall back

VLC_STDERR_LOG_PATH = os.path.join(CONFIG_DIR, "vlc_stderr.log")


def hardware_accel_enabled(config):
    """Resolve the three-state hardware-decoding preference into a boolean
    "use hardware decoding at this spawn".

    * "on"  -> hardware (no watchdog fallback).
    * "off" -> software.
    * "auto"-> hardware, unless the auto-fallback already flagged the current
       GPU as unstable (set once per session by the watchdog).
    """
    mode = config.get(CONFIG_KEY_HARDWARE_ACCEL, HARDWARE_ACCEL_AUTO)
    if mode == HARDWARE_ACCEL_ON:
        return True
    if mode == HARDWARE_ACCEL_OFF:
        return False
    # "auto": try hardware unless the watchdog already fell back this session.
    return not bool(config.get(CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK, False))


def _redirect_stderr_to_file():
    """Redirect this process's stderr (fd 2) to a log file so VLC/FFmpeg's
    native decoder errors -- which libvlc's log callback never receives -- are
    observable to the watchdog. Python's own logs go through the persistent
    file handler, so nothing is lost."""
    global _STDERR_BACKUP_FD
    try:
        _STDERR_BACKUP_FD = os.dup(2)
        fd = os.open(VLC_STDERR_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.dup2(fd, 2)
        if fd > 2:
            os.close(fd)
    except OSError as e:  # noqa: BLE001
        logger.debug(f"[HwWatch] could not redirect stderr: {e}")


_STDERR_BACKUP_FD: int | None = None


class HardwareDecodeWatchdog:
    """Reads the VLC/FFmpeg stderr log for hardware-decode failures and decides
    when to fall back to software decoding."""

    def __init__(self):
        self._offset = 0
        self._count = 0

    def poll(self):
        """Read any new stderr lines and return the number of decode errors
        seen since the previous call."""
        try:
            with open(VLC_STDERR_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return 0
        if not chunk:
            return 0
        new = 0
        for line in chunk.splitlines():
            if any(marker in line for marker in _HW_FAILURE_MARKERS):
                new += 1
        self._count += new
        return new


#: Process-wide watchdog; VLCWidget feeds it, VideoPlayer evaluates it.
_hw_watchdog = HardwareDecodeWatchdog()


if is_wayland():
    if is_gnome():
        # GNOME Wayland hides window introspection from apps; a bundled GNOME
        # Shell extension mirrors the maximized/fullscreen state to a file that
        # WaylandWindowHandler watches.
        WindowHandler = WaylandWindowHandler
    else:
        # KDE/Sway/other Wayland: no supported window-state source, keep a
        # no-op so window-state toggles remain harmless there.
        class WindowHandler:  # noqa: F811
            def __init__(self, _: callable):
                pass


class Fade:
    def __init__(self):
        self.timer = None
        self.is_active = False

    def start(
        self,
        cur,
        target,
        step,
        fade_interval,
        update_callback: callable = None,
        complete_callback: callable = None,
    ):
        # Cancel any existing timer first
        self.cancel()
        self.is_active = True
        self._fade_step(cur, target, step, fade_interval, update_callback, complete_callback)

    def _fade_step(self, cur, target, step, fade_interval, update_callback, complete_callback):
        if not self.is_active:
            return

        new_cur = cur + step
        if (step < 0 and new_cur <= target) or (step > 0 and new_cur >= target):
            new_cur = target
            if update_callback:
                update_callback(int(new_cur))
            if complete_callback:
                complete_callback()
            self.is_active = False
        else:
            if update_callback:
                update_callback(int(new_cur))
            self.timer = Timer(
                fade_interval,
                self._fade_step,
                args=[new_cur, target, step, fade_interval, update_callback, complete_callback],
            )
            self.timer.daemon = True  # Make timer daemon to prevent blocking shutdown
            self.timer.start()

    def cancel(self):
        self.is_active = False
        if self.timer:
            self.timer.cancel()
            self.timer = None


class VLCWidget(Gtk.DrawingArea):
    """
    Simple VLC widget.
    Its player can be controlled through the 'player' attribute, which
    is a vlc.MediaPlayer() instance.
    """

    __gtype_name__ = "VLCWidget"

    def __init__(self, width, height):
        Gtk.DrawingArea.__init__(self)

        # Spawn a VLC instance and create a new media player to embed.
        # Some options need to be specified when instantiating VLC.
        # --no-disable-screensaver: Allow screensaver.
        # --aout=pulse: Force PulseAudio output. VLC's PipeWire audio-output
        #   plugin segfaults (pw_thread_loop_lock) when multiple instances are
        #   active, e.g. one per monitor. See the Flatpak, which uses Pulse too.
        # Decide hardware vs software once so both the decoder and the thread
        # count are consistent.
        hw_enabled = hardware_accel_enabled(ConfigUtil().load())
        vlc_options = [
            "--no-disable-screensaver",
            "--aout=pulse",
            # Suppress VLC's on-screen OSD/title overlay and per-frame logging:
            # wallpaper playback needs no OSD and disabling it avoids needless
            # compositing overhead.
            "--no-video-title-show",
            "--no-osd",
            "--no-snapshot-preview",
            # Preload a small amount of the stream to reduce decode stalls on
            # shuffle while keeping memory footprint low.
            "--network-caching=300",
        ]
        if hw_enabled:
            # Hardware decoding: constrain the decoder to one thread per stream.
            # The threaded h264 decoder can run out of reference frame buffers
            # when several monitors decode at once (get_buffer()/thread_get_buffer
            # failed), which leaves a monitor blank until the next shuffle.
            # Single-threaded decode uses far fewer buffers, and hardware decode
            # is fast enough not to need multiple threads.
            vlc_options += [
                "--avcodec-hw=any",
                "--vout=gl",
                "--avcodec-threads=1",
            ]
        else:
            # Software (CPU) decoding: use multiple threads so 1080p video --
            # especially high-frame-rate clips -- decodes fast enough to keep up
            # in real time. Single-threaded software decode drops frames on
            # 1080p60 content, which looks like the wallpaper "snapping" forward.
            vlc_options += [
                "--avcodec-hw=none",
                "--avcodec-threads=0",
            ]
        self.instance = vlc.Instance(vlc_options)
        self.player = self.instance.media_player_new()

        def handle_embed(*args):
            self.player.set_xwindow(self.get_window().get_xid())
            return True

        # Embed and set size.
        self.connect("realize", handle_embed)
        self.set_size_request(width, height)

    def cleanup(self):
        """Cleanup VLC resources to prevent memory leaks"""
        try:
            if self.player:
                self.player.stop()
                self.player.release()
                self.player = None
            if self.instance:
                self.instance.release()
                self.instance = None
        except Exception as e:
            logger.warning(f"[VLCWidget] Cleanup error: {e}")


class PlayerWindow(Gtk.ApplicationWindow):
    def __init__(self, name, width, height, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Setup a VLC widget given the provided width and height.
        self.width = width
        self.height = height
        self.name = name
        self.__vlc_widget = VLCWidget(width, height)
        self.add(self.__vlc_widget)
        self.__vlc_widget.show()

        # These are to allow us to right click. VLC can't hijack mouse input, and probably not key inputs either in
        # Case we want to add keyboard shortcuts later on.
        self.__vlc_widget.player.video_set_mouse_input(False)
        self.__vlc_widget.player.video_set_key_input(False)

        # A timer that handling fade-in/out
        self.fade = Fade()

        self.menu = None
        self.connect("button-press-event", self._on_button_press_event)

    def play(self):
        self.__vlc_widget.player.play()

    def play_fade(self, target, fade_duration_sec, fade_interval):
        self.play()
        cur = 0
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur=cur,
            target=target,
            step=step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
        )

    def is_playing(self):
        return self.__vlc_widget.player.is_playing()

    def pause(self):
        if self.is_playing():
            self.__vlc_widget.player.pause()

    def pause_fade(self, fade_duration_sec, fade_interval):
        cur = self.get_volume()
        target = 0
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur=cur,
            target=target,
            step=step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
            complete_callback=self.pause,
        )

    def volume_fade(self, target, fade_duration_sec, fade_interval):
        cur = self.get_volume()
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur=cur,
            target=target,
            step=step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
        )

    def media_new(self, *args):
        return self.__vlc_widget.instance.media_new(*args)

    def set_media(self, *args):
        self.__vlc_widget.player.set_media(*args)

    def set_volume(self, *args):
        self.__vlc_widget.player.audio_set_volume(*args)

    def get_volume(self):
        return self.__vlc_widget.player.audio_get_volume()

    def set_mute(self, is_mute):
        return self.__vlc_widget.player.audio_set_mute(is_mute)

    def get_position(self):
        return self.__vlc_widget.player.get_position()

    def set_position(self, *args):
        self.__vlc_widget.player.set_position(*args)

    def snapshot(self, *args):
        return self.__vlc_widget.player.video_take_snapshot(*args)

    def stretch_fit(self, window_width=None, window_height=None):
        """Stretch the wallpaper video to fill its monitor window completely,
        distorting the image to match the window's aspect ratio (Wallpaper
        Engine's "stretch" scale mode) rather than cropping or letterboxing.

        Uses the monitor/window dimensions so the video always fills the screen.
        """
        if window_width and window_height:
            self.width, self.height = window_width, window_height
        # Clear any crop/aspect carried over from a previously loaded video so it
        # does not cause unintended zooming after a shuffle.
        self.__vlc_widget.player.video_set_crop_geometry("")
        ratio = f"{self.width}:{self.height}"
        # Forcing the video's display aspect ratio to match the window makes VLC
        # render it stretched edge-to-edge across the whole monitor.
        self.__vlc_widget.player.video_set_aspect_ratio(ratio)
        logger.debug(f"[StretchFit] stretch to {self.width}x{self.height} ({ratio})")

    def add_audio_track(self, audio):
        self.__vlc_widget.player.add_slave(vlc.MediaSlaveType(1), audio, True)

    def _on_button_press_event(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            if not self.menu:
                self.menu = build_menu(MODE_VIDEO)
            self.menu.popup_at_pointer()
            return True
        return False

    def get_name(self):
        return self.name

    def cleanup(self):
        """Cleanup resources to prevent memory leaks"""
        self.fade.cancel()
        if self.__vlc_widget:
            self.__vlc_widget.cleanup()


class VideoPlayer(BasePlayer):
    """
    <node>
    <interface name='io.github.swordberry.hidamari_prism.player'>
        <property name="mode" type="s" access="read"/>
        <property name="data_source" type="s" access="readwrite"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <property name="is_paused_by_user" type="b" access="readwrite"/>
        <method name='reload_config'/>
        <method name='reload_shuffle'/>
        <method name='set_media_on_all'>
            <arg type='s' name='video_path' direction='in'/>
        </method>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='stop_wallpaper'/>
        <method name='start_wallpaper'/>
        <method name='quit_player'/>
    </interface>
    </node>
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Window/playback state must exist before ``reload_config()`` below:
        # re-enabling the window-state handler there (or the handler it creates
        # polling immediately) can call back into these attributes.
        self.active_handler, self.window_handler = None, None
        self.is_any_maximized, self.is_any_fullscreen = False, False
        self.is_paused_by_user = False

        # Shuffle support
        self._shuffle_history = []
        self._last_video_path = None
        self._shuffle_timer_id = None
        self._independent_seeded = False
        self._hw_check_id = None
        self._hw_fallback_done = False

        # Initialize X11 threads so VLC can use hardware decoding.
        # `libX11.so.6` fix for Fedora 33
        x11 = None
        for lib in ["libX11.so", "libX11.so.6"]:
            try:
                x11 = ctypes.cdll.LoadLibrary(lib)
            except OSError:
                pass
            if x11 is not None:
                x11.XInitThreads()
                break

        self.config = None
        self.window_handler = None
        self.reload_config()

        # Static wallpaper (currently for GNOME only)
        if is_gnome():
            self.original_wallpaper_uri = None
            self.original_wallpaper_uri_dark = None
            if is_flatpak():
                try:
                    self.original_wallpaper_uri = subprocess.check_output(
                        "flatpak-spawn --host gsettings get org.gnome.desktop.background picture-uri",
                        shell=True,
                        encoding="UTF-8",
                    )
                    self.original_wallpaper_uri_dark = subprocess.check_output(
                        "flatpak-spawn --host gsettings get org.gnome.desktop.background picture-uri-dark",
                        shell=True,
                        encoding="UTF-8",
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(f"[StaticWallpaper] {e}")
            else:
                gso = Gio.Settings.new("org.gnome.desktop.background")
                self.original_wallpaper_uri = gso.get_string("picture-uri")
                self.original_wallpaper_uri_dark = gso.get_string("picture-uri-dark")

    def new_window(self, gdk_monitor):
        rect = gdk_monitor.get_geometry()
        window = PlayerWindow(
            gdk_monitor.get_model(), rect.width, rect.height, application=self
        )
        # The wallpaper window must never steal focus from the foreground app,
        # otherwise interacting with the desktop (e.g. selecting text) is
        # disrupted whenever the wallpaper updates or shuffles.
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_can_focus(False)
        # Pass pointer/input events straight through the wallpaper window so the
        # full-screen surface can never block the compositor's auto-hiding
        # dock/taskbar from revealing on hover (seen with an external monitor at
        # boot, where a full-screen XWayland window made the dock flash and hide).
        # Gdk parses the flag after realization; this disables the right-click menu.
        def _enable_pass_through(w):
            gdk_window = w.get_window()
            if gdk_window is not None:
                gdk_window.set_pass_through(True)

        window.connect("realize", _enable_pass_through)
        return window

    def do_activate(self):
        super().do_activate()
        self.data_source = self.config[CONFIG_KEY_DATA_SOURCE]
        self._start_hw_watchdog()

    def _start_hw_watchdog(self):
        """Begin polling for hardware-decode failures while in "auto" mode and
        hardware decoding is currently active."""
        if self.config.get(CONFIG_KEY_HARDWARE_ACCEL, HARDWARE_ACCEL_AUTO) != HARDWARE_ACCEL_AUTO:
            return
        if not hardware_accel_enabled(self.config):
            return
        if self._hw_check_id is not None:
            return
        self._hw_check_id = GLib.timeout_add_seconds(2, self._check_hw_decode_health)
        logger.debug("[HwWatch] watchdog polling started")

    def _check_hw_decode_health(self):
        """Called on the GLib main loop every couple of seconds. If the VLC
        stderr log has accumulated enough strong decode failures, fall back to
        software decoding and stop watching."""
        if self._hw_fallback_done:
            self._hw_check_id = None
            return GLib.SOURCE_REMOVE

        new = _hw_watchdog.poll()
        if new >= _HW_FAILURE_TRIGGER:
            logger.warning(
                f"[HwWatch] {new} hardware-decode errors detected; "
                "falling back to software decoding"
            )
            self._hw_fallback_done = True
            self._hw_check_id = None
            self._fallback_to_software_decode()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _fallback_to_software_decode(self):
        """Mark the current GPU as unstable ("auto" mode only) and ask the
        server to re-create the player process with software decoding.

        The user's hardware-acceleration preference is untouched; a separate
        one-shot flag is set so the respawned player comes back in software
        mode. The server call is dispatched on a background thread: the
        server's respawn path makes a synchronous ``quit_player`` round-trip
        back to this process, so this must not run on (and block) the player's
        own main loop or the two processes deadlock.
        """
        try:
            self.config = ConfigUtil().load()
            self.config[CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK] = True
            ConfigUtil().save(self.config)
            logger.info("[HwWatch] set autofallback; respawning in software mode")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[HwWatch] failed to persist fallback: {e}")
            return

        def _apply():
            try:
                server = SessionBus().get(DBUS_NAME_SERVER)
                server.apply_hardware_accel()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[HwWatch] failed to respawn player in software mode: {e}")

        Thread(target=_apply, daemon=True).start()

    def _on_monitor_added(self, _, gdk_monitor, *args):
        super()._on_monitor_added(_, gdk_monitor, *args)
        # Give the newly-added display a per-monitor source (so it does not go
        # black waiting for the next shuffle) and start its wallpaper playing.
        window = self.windows.get(gdk_monitor)
        if window is not None:
            try:
                self._ensure_monitor_source(gdk_monitor)
                self._apply_source_to_window(gdk_monitor, window)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[Player] error starting media on added monitor: {e}")
        self.monitor_sync()

    def _ensure_monitor_source(self, gdk_monitor):
        """Make sure a (possibly freshly hot-plugged) monitor has its own entry
        in data_source so it does not fall back to a shared/blank source."""
        ds = self.config.get(CONFIG_KEY_DATA_SOURCE)
        if not isinstance(ds, dict):
            return
        model = gdk_monitor.get_model()
        current = ds.get(model) or ""
        if os.path.isfile(current):
            return
        # Give the new display a random wallpaper from the active playlist, or
        # fall back to the Default/primary source.
        videos = self._active_playlist_videos() or []
        picked = None
        if videos:
            picked = random.choice(videos)
            defaults = ds.get("Default") or ""
            if picked == defaults and len(videos) > 1:
                picked = random.choice([v for v in videos if v != defaults])
        else:
            picked = ds.get("Default") or ""
        ds[model] = picked
        ConfigUtil().save(self.config)

    def _on_monitor_removed(self, _, gdk_monitor, *args):
        # A display was unplugged. The compositor is simultaneously tearing down
        # the shared X/XWayland connection for that screen. Touching the removed
        # monitor's VLC surface right now (player.stop() -> Xv/X11 round trip)
        # can deadlock the whole desktop against that teardown -- so we detach
        # the window bookkeeping immediately, but DEFER the heavy cleanup a few
        # seconds until the compositor has fully settled on the remaining screens.
        logger.info("[Player] monitor-removed: detaching removed display")
        window = self.windows.pop(gdk_monitor, None)
        removed_name = gdk_monitor.get_model()

        # Drop the removed monitor's key from persisted data_source, keeping
        # Default plus the remaining monitors intact.
        try:
            ds = self.config.get(CONFIG_KEY_DATA_SOURCE)
            if isinstance(ds, dict):
                if removed_name in ds:
                    del ds[removed_name]
                    ConfigUtil().save(self.config)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Player] error cleaning up data_source: {e}")

        # Do NOT force a re-shuffle here: it re-randomizes every remaining
        # monitor and tears down/re-spins all their decoders at once -- exactly
        # when the system is most stretched by the unplug -- which can exhaust
        # memory or wedge the compositor. The per-monitor wallpapers keep
        # playing and the normal shuffle timer takes over on its own cycle.
        if window is None:
            logger.debug(f"[Player] removed monitor {removed_name}: no window to clean up")
            return

        # Immediately detach the dying window's media thread. This is a light-
        # weight stop (no VLC instance creation/release, no X surface teardown):
        # it just halts the decoder so it stops hammering memory/CPU right while
        # the compositor is removing that screen. The heavyweight release() and
        # GTK destroy() are deferred until the compositor has fully settled.
        try:
            window.set_media(window.media_new(""))  # Detach the playing media
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Player] error detaching removed monitor {removed_name}: {e}")

        def _teardown():
            logger.info(f"[Player] teardown start for removed monitor {removed_name}")

            # Re-check that this monitor is really still gone before we stop
            # anything, and that the window object hasn't already been detorn.
            try:
                display = Gdk.Display.get_default()
                still_present = any(
                    m.get_model() == removed_name for m in display.get_monitors()
                )
            except Exception:  # noqa: BLE001
                still_present = False
            if still_present:
                logger.info(
                    f"[Player] removed monitor {removed_name} re-appeared; skipping teardown"
                )
                return False

            try:
                window.cleanup()
                logger.info(f"[Player] teardown: VLC released for {removed_name}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[Player] teardown: error releasing VLC for {removed_name}: {e}")

            try:
                window.destroy()
                logger.info(f"[Player] teardown: window destroyed for {removed_name}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[Player] teardown: error destroying window for {removed_name}: {e}")

            return False

        # Run the delayed cleanup on the main loop after the compositor has
        # finished re-laying-out the surviving monitors. 6s is a comfortable
        # margin over GNOME's monitor reconfiguration.
        GLib.timeout_add_seconds(6, _teardown)
        logger.info(f"[Player] scheduled teardown for removed monitor {removed_name} in 6s")

    def _on_size_changed(self, *args):
        # A monitor's resolution changed (or one was added/removed). Resize every
        # wallpaper window to its monitor's new geometry and re-stretch the video
        # so the wallpaper keeps filling the whole screen.
        #
        # IMPORTANT: on monitor unplug, GDK can fire size-changed BEFORE
        # monitor-removed. A window whose monitor is already gone must be skipped:
        # touching its VLC surface (stretch_fit/aspect ratio) on a dead X window
        # under XWayland hangs the compositor/renderer.
        display = Gdk.Display.get_default()
        try:
            current = set(display.get_monitors())
        except Exception:  # noqa: BLE001
            current = None
        for monitor, window in self.windows.items():
            if window is None:
                continue
            if current is not None and monitor not in current:
                logger.info(
                    f"[Player] size-changed: skipping dying monitor {monitor.get_model()}"
                )
                continue
            try:
                rect = monitor.get_geometry()
                x, y, width, height = rect.x, rect.y, rect.width, rect.height
                window.set_size_request(width, height)
                window.move(x, y)
                if self.mode == MODE_VIDEO:
                    window.stretch_fit(width, height)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[Player] size-changed: error on {monitor.get_model()}: {e}")
        logger.info("[Player] size-changed: repositioned and re-stretched wallpapers")

    def _on_active_changed(self, active):
        if active:
            self.pause_playback()
        else:
            if self._should_playback_start():
                self.start_playback()
            else:
                self.pause_playback()

    def _on_window_state_changed(self, state):
        self.is_any_maximized, self.is_any_fullscreen = (
            state["is_any_maximized"],
            state["is_any_fullscreen"],
        )
        logger.info(
            f"is_any_maximized: {self.is_any_maximized}, is_any_fullscreen: {self.is_any_fullscreen}"
        )

        if self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]:
            if self._should_playback_start():
                self.start_playback()
            else:
                self.pause_playback()
        elif self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]:
            for monitor, window in self.windows.items():
                if not monitor.is_primary():
                    continue
                if self.is_any_fullscreen or self.is_any_maximized:
                    window.volume_fade(
                        target=0,
                        fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                        fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
                    )
                else:
                    window.volume_fade(
                        target=self.volume,
                        fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                        fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
                    )

    def _ensure_window_handler(self):
        """Create the window-state handler when it is needed and not running
        yet. On X11 it is the Wnck-based WindowHandler; on GNOME Wayland it is
        the bundled-shell-extension file watcher; on other Waylands it stays a
        no-op placeholder. On GNOME Wayland only create it once a wallpaper is
        actually configured to react to window state."""
        if self.window_handler:
            return
        if is_wayland() and is_gnome() and not (
            self.config.get(CONFIG_KEY_PAUSE_WHEN_MAXIMIZED, False)
            or self.config.get(CONFIG_KEY_MUTE_WHEN_MAXIMIZED, False)
        ):
            return
        self.window_handler = WindowHandler(self._on_window_state_changed)
        logger.info(f"[Player] window handler: {type(self.window_handler).__name__}")

    def _should_playback_start(self):
        if self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] and (
            self.is_any_maximized or self.is_any_fullscreen
        ):
            return False
        if self.is_paused_by_user:
            return False
        return True

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def data_source(self):
        return self.config[CONFIG_KEY_DATA_SOURCE]

    def _apply_source_to_window(self, monitor, window):
        """Set the video wallpaper for a single monitor window from the current
        data_source. Used both when (re)assigning all monitors and when a display
        is hot-plugged so its window actually starts playing instead of going
        black until the next shuffle."""
        if self.mode != MODE_VIDEO:
            return
        data_source = self.config[CONFIG_KEY_DATA_SOURCE]
        source = (
            data_source[monitor.get_model()]
            if monitor.get_model() in data_source
            and len(data_source[monitor.get_model()]) != 0
            else data_source["Default"]
        )
        logger.info(f"Setting source {source} to {monitor.get_model()}")
        media = window.media_new(source)
        """
        This loops the media itself. Using -R / --repeat and/or -L / --loop don't seem to work. However,
        based on reading, this probably only repeats 65535 times, which is still a lot of time, but might
        cause the program to stop playback if it's left on for a very long time.
        """
        media.add_option("input-repeat=65535")
        # Static images: VLC's image demuxer renders a single frame that would
        # otherwise end almost immediately. Looping keeps it up like a wallpaper.
        if is_static_image(source):
            media.add_option("image-duration=-1")
            media.add_option("no-audio")
        # Prevent awful ear-rape with multiple instances.
        if not monitor.is_primary():
            media.add_option("no-audio")
        window.set_media(media)
        window.set_position(0.0)
        # Stretch the wallpaper to fill this monitor's current size.
        rect = monitor.get_geometry()
        window.stretch_fit(rect.width, rect.height)

    @data_source.setter
    def data_source(self, data_source):
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source

        if self.mode == MODE_VIDEO:
            for monitor, window in self.windows.items():
                self._apply_source_to_window(monitor, window)

        elif self.mode == MODE_STREAM:
            source = data_source["Default"]
            formats = get_formats(source)
            max_height = (
                max(self.windows, key=lambda m: m.get_geometry().height).get_geometry().height
            )
            video_url, _video_width, _video_height = get_optimal_video(formats, max_height)
            audio_url = get_best_audio(formats)

            for monitor, window in self.windows.items():
                media = window.media_new(video_url)
                media.add_option("input-repeat=65535")
                window.set_media(media)
                if monitor.is_primary():
                    window.add_audio_track(audio_url)
                else:
                    # `get_optimal_video` now might return video with audio.
                    media.add_option("no-audio")
                window.set_position(0.0)
                # Stretch the stream to fill this monitor's current size.
                rect = monitor.get_geometry()
                window.stretch_fit(rect.width, rect.height)
        else:
            raise ValueError("Invalid mode")

        self.volume = self.config[CONFIG_KEY_VOLUME]
        self.is_mute = self.config[CONFIG_KEY_MUTE]
        self.start_playback()

        # Everything is initialized. Create handlers if haven't (singleton pattern).
        if not self.active_handler:
            self.active_handler = ActiveHandler(self._on_active_changed)
        self._ensure_window_handler()

        if self.config[CONFIG_KEY_STATIC_WALLPAPER] and self.mode == MODE_VIDEO:
            self.set_static_wallpaper()
        else:
            self.set_original_wallpaper()

        self._setup_shuffle()

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        for monitor in self.windows:
            if monitor.is_primary():
                self.windows[monitor].set_volume(volume)

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        for monitor, window in self.windows.items():
            if monitor.is_primary():
                window.set_mute(is_mute)

    @property
    def is_playing(self):
        return not self.is_paused_by_user

    def pause_playback(self):
        for _monitor, window in self.windows.items():
            window.pause_fade(
                fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
            )

    def start_playback(self):
        if self._should_playback_start():
            for _monitor, window in self.windows.items():
                window.play_fade(
                    target=self.volume,
                    fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                    fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
                )

    def set_media_on_all(self, video_path):
        """Swap the wallpaper on every live window to a single video, in place.

        Used by the server's shuffle/playlist path. Previously the server killed
        and re-spawned the whole player process on every change, which created a
        brand-new VLC instance and X/libva surface right at the moment a monitor
        could be mid hot-unplug -- the new decoder then fought the compositor's
        teardown and froze the desktop. Swapping media on the already-lit windows
        reuses the existing VLC instances (same process, same X window, same
        decode threads), so a monitor change can never coincide with a VLC init.
        """
        if not video_path or not os.path.isfile(video_path):
            logger.warning(f"[Player] set_media_on_all: invalid path: {video_path}")
            return
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_MODE] = MODE_VIDEO
        data_source = self.config.get(CONFIG_KEY_DATA_SOURCE)
        if isinstance(data_source, dict) and data_source:
            for key in list(data_source.keys()):
                data_source[key] = video_path
        else:
            data_source = video_path
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source
        ConfigUtil().save(self.config)
        logger.info(f"[Player] set_media_on_all: {video_path}")
        self.data_source = self.config[CONFIG_KEY_DATA_SOURCE]
        # An explicit apply should also bring a previously-stopped wallpaper
        # back onto the screen (windows stay hidden across media swaps).
        self.ensure_wallpaper_visible()

    def monitor_sync(self):
        primary_monitor = None
        for monitor, _window in self.windows.items():
            if monitor.is_primary:
                primary_monitor = monitor
                break
        if primary_monitor:
            for monitor, window in self.windows.items():
                if monitor == primary_monitor:
                    continue
                # `set_position()` method require the playback to be enabled before calling
                window.play()
                window.set_position(self.windows[primary_monitor].get_position())
                window.play() if self.windows[primary_monitor].is_playing() else window.pause()

    def set_static_wallpaper(self):
        # Currently for GNOME only
        if not is_gnome():
            return
        source = self.data_source["Default"]
        # For a static image there is nothing to seek -- just use the whole
        # frame. Only videos need the golden-ratio frame extraction.
        if is_static_image(source):
            ss = "00:00:00"
        else:
            # Get the duration of the video
            try:
                duration = float(
                    subprocess.check_output(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            source,
                        ],
                        shell=False,
                    )
                )
            except subprocess.CalledProcessError:
                duration = 0
            # Find the golden ratio
            ss = time.strftime("%H:%M:%S", time.gmtime(duration / 3.14))
        # Extract the frame
        static_wallpaper_path = os.path.join(
            CONFIG_DIR, f"static-{random.randint(0, 999999):06d}.png"
        )
        ret = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                ss,
                "-i",
                self.data_source["Default"],
                "-vframes",
                "1",
                static_wallpaper_path,
            ],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        if ret.returncode == 0 and os.path.isfile(static_wallpaper_path):
            blur_wallpaper = Image.open(static_wallpaper_path)
            blur_wallpaper = blur_wallpaper.filter(
                ImageFilter.GaussianBlur(self.config["static_wallpaper_blur_radius"])
            )
            blur_wallpaper.save(static_wallpaper_path)
            static_wallpaper_uri = pathlib.Path(static_wallpaper_path).resolve().as_uri()
            if is_flatpak():
                try:
                    subprocess.run(
                        [
                            "flatpak-spawn",
                            "--host",
                            "gsettings",
                            "set",
                            "org.gnome.desktop.background",
                            "picture-uri",
                            static_wallpaper_uri,
                        ],
                        shell=False,
                    )
                    subprocess.run(
                        [
                            "flatpak-spawn",
                            "--host",
                            "gsettings",
                            "set",
                            "org.gnome.desktop.background",
                            "picture-uri-dark",
                            static_wallpaper_uri,
                        ],
                        shell=False,
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(f"[StaticWallpaper] {e}")
            else:
                gso = Gio.Settings.new("org.gnome.desktop.background")
                gso.set_string("picture-uri", static_wallpaper_uri)
                gso.set_string("picture-uri-dark", static_wallpaper_uri)

    def set_original_wallpaper(self):
        # Currently for GNOME only
        if not is_gnome():
            return
        if is_flatpak():
            try:
                if self.original_wallpaper_uri is not None:
                    subprocess.run(
                        [
                            "flatpak-spawn",
                            "--host",
                            "gsettings",
                            "set",
                            "org.gnome.desktop.background",
                            "picture-uri",
                            self.original_wallpaper_uri,
                        ],
                        shell=False,
                    )
                if self.original_wallpaper_uri_dark is not None:
                    subprocess.run(
                        [
                            "flatpak-spawn",
                            "--host",
                            "gsettings",
                            "set",
                            "org.gnome.desktop.background",
                            "picture-uri-dark",
                            self.original_wallpaper_uri,
                        ],
                        shell=False,
                    )
            except subprocess.CalledProcessError as e:
                logger.error(f"[StaticWallpaper] {e}")
        else:
            gso = Gio.Settings.new("org.gnome.desktop.background")
            gso.set_string("picture-uri", self.original_wallpaper_uri)
            gso.set_string("picture-uri-dark", self.original_wallpaper_uri_dark)
        # Purge the generated static wallpaper (and leftover if any)
        for f in glob.glob(os.path.join(CONFIG_DIR, "static-*.png")):
            os.remove(f)

    def _active_playlist_videos(self):
        """Return the playable files of the currently armed playlist. This is
        used both for shuffle (random) and ordered (shuffle-off) playback, so
        it does not depend on the shuffle toggle being on."""
        shuffle_config = self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        active = shuffle_config.get(CONFIG_KEY_SHUFFLE_ACTIVE, "")
        playlists = self.config.get(CONFIG_KEY_PLAYLISTS, {}) or {}
        videos = playlists.get(active, []) if active else []
        return [v for v in videos if os.path.isfile(v)]

    @staticmethod
    def _shuffle_config_enabled(shuffle_config):
        return bool(shuffle_config.get(CONFIG_KEY_SHUFFLE_ENABLED, False))

    def _pick_ordered_video(self):
        """Advance the active playlist in order. Once a rotation is running the
        next item follows the last shown wallpaper; when starting fresh it keeps
        the currently-armed 'Default' wallpaper (or falls back to the first
        entry of the playlist)."""
        videos = self._active_playlist_videos()
        if not videos:
            return None
        if self._last_video_path:
            try:
                return videos[(videos.index(self._last_video_path) + 1) % len(videos)]
            except ValueError:
                pass
        # Fresh start: hold the configured Default/selected wallpaper.
        data_source = self.config.get(CONFIG_KEY_DATA_SOURCE)
        current = data_source.get("Default") if isinstance(data_source, dict) else None
        if current in videos:
            return current
        return videos[0]

    def _pick_shuffle_video(self):
        videos = self._active_playlist_videos()
        if not videos:
            return None
        candidates = list(set(videos))
        if len(candidates) > 1 and self._last_video_path in candidates:
            candidates.remove(self._last_video_path)
        return random.choice(candidates)

    def _shuffle_once(self):
        if self.mode != MODE_VIDEO:
            return
        shuffle_config = self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        data_source = self.config[CONFIG_KEY_DATA_SOURCE]

        if not self._shuffle_config_enabled(shuffle_config):
            # Ordered playback: advance through the armed playlist in order.
            target = self._pick_ordered_video()
            if target is None:
                return
            logger.info(f"[Playlist] ordered -> {target}")
            self._set_source_everywhere(data_source, target)
            return

        independent = shuffle_config.get(CONFIG_KEY_SHUFFLE_INDEPENDENT, False)
        if independent and isinstance(data_source, dict) and data_source:
            # Independent mode: every monitor (including the main one) gets its
            # own wallpaper and changes on each shuffle. The main/pre-primary
            # monitor starts from the wallpaper that was selected in the
            # playlists tab (the "Default" source), then shuffles like the rest.
            videos = self._active_playlist_videos()
            if not videos:
                return
            monitors = [m for m in self.windows.keys() if self.windows[m] is not None]
            if not monitors:
                return
            primary = next((m for m in monitors if m.is_primary()), monitors[0])
            # Seed the primary with the currently-selected wallpaper on the
            # first independent shuffle, then let it shuffle freely.
            primary_wallpaper = data_source.get("Default") or videos[0]
            if not os.path.isfile(primary_wallpaper):
                primary_wallpaper = videos[0]
            data_source["Default"] = primary_wallpaper

            random.shuffle(videos)
            if not self._independent_seeded:
                # First shuffle in independent mode: main monitor starts on the
                # selected wallpaper; every other monitor is random.
                data_source[primary.get_model()] = primary_wallpaper
                others = [m for m in monitors if m is not primary]
                for i, monitor in enumerate(others):
                    data_source[monitor.get_model()] = videos[i % len(videos)]
                self._independent_seeded = True
                logger.info(
                    f"[Shuffle] seeded primary {primary.get_model()} -> {primary_wallpaper}; "
                    f"others -> {[data_source[m.get_model()] for m in others]}"
                )
            else:
                # Subsequent shuffles: every monitor (including main) changes to
                # a random wallpaper.
                for i, monitor in enumerate(monitors):
                    data_source[monitor.get_model()] = videos[i % len(videos)]
                logger.info(
                    f"[Shuffle] independent tick -> "
                    + "; ".join(
                        f"{m.get_model()}={data_source[m.get_model()]}" for m in monitors
                    )
                )
            self._last_video_path = None
            # Persist the per-monitor assignment so it survives an (unexpected)
            # crash or restart instead of reverting to a single shared video.
            ConfigUtil().save(self.config)
            self.data_source = data_source
            return

        target = self._pick_shuffle_video()
        if target is None:
            return
        self._set_source_everywhere(data_source, target)

    def _set_source_everywhere(self, data_source, target):
        """Point every monitor (and Default) at a wallpaper so all displays
        actually change. Also records the last shown path so ordered playback
        can advance from it."""
        # Point every monitor (and Default) at the selected wallpaper so all
        # displays actually change.
        if isinstance(data_source, dict) and data_source:
            for key in list(data_source.keys()):
                data_source[key] = target
        else:
            self.config[CONFIG_KEY_DATA_SOURCE] = target
        self._last_video_path = target
        ConfigUtil().save(self.config)
        self.data_source = self.config[CONFIG_KEY_DATA_SOURCE]

    def _shuffle_tick(self):
        if self.mode != MODE_VIDEO:
            return True
        if not self._active_playlist_videos():
            return True
        self._shuffle_once()
        return True

    def _setup_shuffle(self):
        shuffle_config = self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        # The rotation timer runs whenever an active playlist is armed -- both
        # for shuffled and ordered (shuffle-toggle-off) playback.
        if self.mode != MODE_VIDEO or not self._active_playlist_videos():
            if self._shuffle_timer_id is not None:
                GLib.source_remove(self._shuffle_timer_id)
                self._shuffle_timer_id = None
            return
        if self._shuffle_timer_id is not None:
            return
        interval_min = max(
            shuffle_config.get(CONFIG_KEY_SHUFFLE_INTERVAL, 5) or 5,
            1,
        )
        interval_ms = interval_min * 60 * 1000
        self._shuffle_timer_id = GLib.timeout_add(interval_ms, self._shuffle_tick)

    def reload_shuffle(self):
        self.reload_config()
        if self._shuffle_timer_id is not None:
            GLib.source_remove(self._shuffle_timer_id)
            self._shuffle_timer_id = None
        self._last_video_path = None
        # Re-seed the primary from the currently-selected wallpaper whenever the
        # shuffle/independent settings are (re)applied.
        self._independent_seeded = False
        self._setup_shuffle()
        self._shuffle_once()
        # An explicit playlist apply should also bring a previously-stopped
        # wallpaper back onto the screen.
        self.ensure_wallpaper_visible()

    def reload_config(self):
        self.config = ConfigUtil().load()
        # The window-state handler may become relevant (Or the bundled GNOME
        # Shell extension may have just been enabled) after start -- e.g. the
        # user turned on "Pause when maximized window" from the menu.
        self._ensure_window_handler()

    def quit_player(self):
        self.set_original_wallpaper()

        # Cleanup handlers
        if self.active_handler:
            self.active_handler.cleanup()
            self.active_handler = None

        if self.window_handler:
            self.window_handler.cleanup()
            self.window_handler = None

        # Cleanup all windows
        for _monitor, window in self.windows.items():
            if window:
                window.cleanup()

        super().quit_player()


def main():
    _redirect_stderr_to_file()
    bus = SessionBus()
    app = VideoPlayer()
    try:
        bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
