import logging
import multiprocessing as mp
import os
import random
import signal
import time
from multiprocessing import Process

import setproctitle
from gi.repository import GLib
from pydbus import SessionBus

from hidamari_prism.commons import (
    CONFIG_KEY_BLUR_RADIUS,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_DONATE_ONCE,
    CONFIG_KEY_LAUNCH_COUNT,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLISTS,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_SHUFFLE_ACTIVE,
    CONFIG_KEY_SHUFFLE_ENABLED,
    CONFIG_KEY_SHUFFLE_INDEPENDENT,
    CONFIG_KEY_HARDWARE_ACCEL,
    CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK,
    HARDWARE_ACCEL_AUTO,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_SYSTRAY,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_PLAYER,
    DBUS_NAME_SERVER,
    LOGGER_NAME,
    MODE_NULL,
    MODE_STREAM,
    MODE_VIDEO,
    MODE_WEBPAGE,
)
from hidamari_prism.gui.control import main as gui_main
from hidamari_prism.menu import show_systray_icon
from hidamari_prism.monitor import Monitors
from hidamari_prism.player.video_player import main as video_player_main
from hidamari_prism.player.web_player import main as web_player_main
from hidamari_prism.utils import ConfigUtil, EndSessionHandler, get_video_paths

loop = GLib.MainLoop()
logger = logging.getLogger(LOGGER_NAME)


class Hidamari_PrismServer:
    """
    <node>
    <interface name='io.github.swordberry.hidamari_prism.server'>
        <method name='null'/>
        <method name='video'>
            <arg type='s' name='video_path' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='stream'>
            <arg type='s' name='stream_url' direction='in'/>
        </method>
        <method name='webpage'>
            <arg type='s' name='webpage_url' direction='in'/>
        </method>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='apply_playlist_shuffle'/>
        <method name='play_playlist_video'>
            <arg type='s' name='video_path' direction='in'/>
        </method>
        <method name='reload_shuffle_settings'/>
        <method name="reload"/>
        <method name="feeling_lucky"/>
        <method name='apply_hardware_accel'/>
        <method name='stop_wallpaper'/>
        <method name='start_wallpaper'/>
        <method name='show_gui'/>
        <method name='quit'/>
        <property name="mode" type="s" access="read"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="blur_radius" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <property name="is_paused_by_user" type="b" access="readwrite"/>
        <property name="is_static_wallpaper" type="b" access="readwrite"/>
        <property name="is_pause_when_maximized" type="b" access="readwrite"/>
        <property name="is_mute_when_maximized" type="b" access="readwrite"/>
        <property name="shuffle_independent" type="b" access="readwrite"/>
    </interface>
    </node>
    """

    def __init__(self, version, pkgdatadir, localedir, args):
        setproctitle.setproctitle("hidamari_prism-server")

        self.version = version
        self.pkgdatadir = pkgdatadir
        self.localedir = localedir
        self.args = args
        self._prev_mode = None
        self._player_count = 0

        # Processes
        # Switch to `forkserver` since v3.2 for performance. BTW `fork` didn't work (it crashes).
        # Ref: https://bnikolic.co.uk/blog/python/parallelism/2019/11/13/python-forkserver-preload.html
        mp.set_start_method("forkserver")
        self.gui_process = None
        self.sys_icon_process = None
        self.player_process = None

        signal.signal(signal.SIGINT, lambda *_: self.quit())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        # SIGSEGV as a fail-safe
        signal.signal(signal.SIGSEGV, lambda *_: self.quit())
        # Monitoring EndSession (OS reboot, shutdown, etc.)
        EndSessionHandler(self.quit)

        # Configuration
        if args.reset:
            ConfigUtil().generate_template()
        self._load_config()

        # A fresh launch re-probes hardware decoding: clear the auto-fallback
        # flag so "auto" mode starts on hardware again this session (the
        # watchdog may set it later if this GPU's decoder misbehaves).
        if self.config.get(CONFIG_KEY_HARDWARE_ACCEL, HARDWARE_ACCEL_AUTO) == HARDWARE_ACCEL_AUTO:
            self.config[CONFIG_KEY_HARDWARE_ACCEL_AUTOFALLBACK] = False
            self._save_config()

        # Show the donation popup on the 1st launch of the program, once.
        launch_count = int(self.config.get(CONFIG_KEY_LAUNCH_COUNT, 0)) + 1
        self.config[CONFIG_KEY_LAUNCH_COUNT] = launch_count
        if launch_count == 1:
            self.config[CONFIG_KEY_DONATE_ONCE] = True
        self._save_config()

        # Player process
        self.reload()

        # Show main GUI
        if not args.background:
            self.show_gui()

        logger.info("[Server] Started")

    def _load_config(self):
        self.config = ConfigUtil().load()

    def _save_config(self):
        ConfigUtil().save(self.config)

    def _setup_player(self, mode, data_source=None, monitor=None):
        """Setup and run player"""
        logger.info(f"[Mode] {mode}")
        self.config[CONFIG_KEY_MODE] = mode

        # Set data source if specified
        if data_source and monitor:
            self.config[CONFIG_KEY_DATA_SOURCE][monitor] = data_source
        if data_source is not None:
            self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = data_source

        # Tear down the old player by terminating/killing its process below.
        # We deliberately do NOT do a synchronous DBus ``quit_player()`` here:
        # that call only returns after the player's window.cleanup() finishes,
        # which can block forever on a wedged VLC decoder (e.g. a hardware
        # decode failure while "auto" mode falls back). The process signal
        # handling below terminates the same process regardless, so the
        # round-trip is redundant AND a deadlock vector.

        # Terminate old player process and wait for it to finish
        if self.player_process:
            self.player_process.terminate()
            self.player_process.join(timeout=5)  # Wait up to 5 seconds
            if self.player_process.is_alive():
                logger.warning("[Server] Player process didn't terminate, killing it")
                self.player_process.kill()
                self.player_process.join(timeout=2)
            self.player_process = None

        if mode in [MODE_VIDEO, MODE_STREAM]:
            self.player_process = Process(
                name=f"hidamari_prism-player-{self._player_count}", target=video_player_main
            )
        elif mode == MODE_WEBPAGE:
            self.player_process = Process(
                name=f"hidamari_prism-player-{self._player_count}", target=web_player_main
            )
        elif mode == MODE_NULL:
            pass
        else:
            raise ValueError("[Server] Unknown mode")
        if self.player_process is not None:
            self.player_process.start()
            self._player_count += 1

        # Refresh systray icon if the mode changed
        if self.config[CONFIG_KEY_SYSTRAY]:
            if self._prev_mode != self.mode:
                if self.sys_icon_process:
                    self.sys_icon_process.terminate()
                    self.sys_icon_process.join(timeout=3)
                    if self.sys_icon_process.is_alive():
                        self.sys_icon_process.kill()
                        self.sys_icon_process.join(timeout=1)
                self.sys_icon_process = Process(
                    name="hidamari_prism-systray",
                    target=show_systray_icon,
                    args=(mode, self.localedir),
                )
                self.sys_icon_process.start()
            self._prev_mode = self.mode

    def video(self, video_path=None, monitor=None):
        # Prefer swapping media in place on the already-running player process
        # rather than killing and re-spawning it. A fresh VLC instance initializes
        # its X/libva surface; if that lands while a monitor is being hot-unplugged,
        # the new decoder fights the compositor's teardown and can hard-freeze the
        # desktop (observed: get_buffer() failed / mmco thrash + GNOME wedge).
        # Reusing the live windows keeps the same VLC instances and X surfaces.
        if video_path and os.path.isfile(video_path):
            player = get_instance(DBUS_NAME_PLAYER)
            if player is not None and self.player_process and self.player_process.is_alive():
                try:
                    player.set_media_on_all(video_path)
                    return
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[Server] in-place media swap failed, will respawn: {e}")
        self._setup_player(MODE_VIDEO, video_path, monitor)

    def stream(self, stream_url=None):
        self._setup_player(MODE_STREAM, stream_url)

    def webpage(self, webpage_url=None):
        self._setup_player(MODE_WEBPAGE, webpage_url)

    @staticmethod
    def pause_playback():
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.pause_playback()

    @staticmethod
    def start_playback():
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.start_playback()

    def apply_playlist_shuffle(self):
        """Apply the saved playlist/shuffle settings: if shuffle is enabled with
        a valid active playlist, start playing a video from that playlist (even
        if nothing is currently playing), then let the player shuffle through it."""
        logger.info("[Server] apply_playlist_shuffle called")
        self.config = ConfigUtil().load()
        shuffle_config = self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        if self.config[CONFIG_KEY_MODE] == MODE_NULL:
            # Nothing is playing: start a wallpaper from the active playlist
            # (random when shuffled, first-in-order otherwise).
            self._autostart_shuffle_video()
        # Always let the player re-arm: sets up the rotation timer (ordered or
        # shuffled) and applies the next wallpaper from the active playlist.
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.reload_shuffle()

    def reload_shuffle_settings(self):
        """Tell the player to reload shuffle config (e.g. after the
        per-monitor independent flag changed) and apply it immediately."""
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.reload_shuffle()

    def play_playlist_video(self, video_path=None):
        """Play a specific video wallpaper immediately on every display."""
        logger.info(f"[Server] play_playlist_video called with: {video_path}")
        self.config = ConfigUtil().load()
        if not video_path or not os.path.isfile(video_path):
            logger.warning(f"[Server] play_playlist_video: invalid path: {video_path}")
            return
        ds = self.config[CONFIG_KEY_DATA_SOURCE]
        if isinstance(ds, dict) and ds:
            for key in list(ds.keys()):
                ds[key] = video_path
        else:
            self.config[CONFIG_KEY_DATA_SOURCE] = video_path
        self.config[CONFIG_KEY_MODE] = MODE_VIDEO
        self._save_config()
        logger.info(f"[Server] Starting playlist video: {video_path}")
        self.video(video_path)

    def reload(self):
        if self.config[CONFIG_KEY_MODE] == MODE_VIDEO:
            self.video()
        elif self.config[CONFIG_KEY_MODE] == MODE_STREAM:
            self.stream()
        elif self.config[CONFIG_KEY_MODE] == MODE_WEBPAGE:
            self.webpage()
        elif self.config[CONFIG_KEY_MODE] == MODE_NULL:
            if not self._autostart_shuffle_video():
                pass
        else:
            raise ValueError("[Server] Unknown mode")

    def apply_hardware_accel(self):
        """Re-create the player so the hardware-acceleration preference takes
        effect. VLC instance options (--avcodec-hw) are fixed when the player
        process starts, so toggling it needs a fresh player -- same mode and
        source, new decoder stack. Web mode is untouched (it doesn't decode
        video with VLC)."""
        logger.info("[Server] apply_hardware_accel called")
        self.config = ConfigUtil().load()
        mode = self.config.get(CONFIG_KEY_MODE)
        if mode not in (MODE_VIDEO, MODE_STREAM):
            logger.info("[Server] apply_hardware_accel: no video player to recreate")
            return
        self._setup_player(mode)

    def _autostart_shuffle_video(self):
        """If an active playlist is armed with valid files and nothing is
        currently playing, start a wallpaper from that playlist (random if
        shuffle is on, otherwise the first entry in order). Returns True if a
        wallpaper was started."""
        shutdown_config = self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        active = shutdown_config.get(CONFIG_KEY_SHUFFLE_ACTIVE, "")
        playlists = self.config.get(CONFIG_KEY_PLAYLISTS, {}) or {}
        videos = [v for v in playlists.get(active, []) if os.path.isfile(v)]
        if not videos:
            return False
        shuffle_enabled = bool(shutdown_config.get(CONFIG_KEY_SHUFFLE_ENABLED, False))

        self.config[CONFIG_KEY_MODE] = MODE_VIDEO
        ds = self.config[CONFIG_KEY_DATA_SOURCE]

        if (shuffle_enabled
                and shutdown_config.get(CONFIG_KEY_SHUFFLE_INDEPENDENT, False)
                and isinstance(ds, dict) and ds):
            # Independent mode on autostart: keep the selected wallpaper on
            # "Default" (the primary/main monitor) and give every other monitor
            # a random one. The player re-balances this per real monitors.
            primary_wallpaper = ds.get("Default") or videos[0]
            if not os.path.isfile(primary_wallpaper):
                primary_wallpaper = videos[0]
            ds["Default"] = primary_wallpaper
            random.shuffle(videos)
            others = [k for k in ds.keys() if k != "Default"]
            for i, key in enumerate(others):
                ds[key] = videos[i % len(videos)]
        else:
            # Shuffle off: start the ordered rotation from the first entry.
            video_path = random.choice(videos) if shuffle_enabled else videos[0]
            if isinstance(ds, dict):
                for key in list(ds.keys()):
                    ds[key] = video_path
            else:
                self.config[CONFIG_KEY_DATA_SOURCE] = video_path

        self._save_config()
        self.video()
        return True

    def feeling_lucky(self):
        """Random play a video from the directory"""
        monitors = Monitors().get_monitors()
        for monitor in monitors:
            file_list = get_video_paths()
            # Remove current data source from the random selection
            if self.config[CONFIG_KEY_DATA_SOURCE][monitor] in file_list:
                file_list.remove(self.config[CONFIG_KEY_DATA_SOURCE][monitor])
            if file_list:
                video_path = random.choice(file_list)
                self.config[CONFIG_KEY_MODE] = MODE_VIDEO
                self.config[CONFIG_KEY_DATA_SOURCE][monitor] = video_path
                self._save_config()
            self.video(video_path)

    @staticmethod
    def stop_wallpaper():
        """Turn the animated wallpaper off on every monitor.

        The player windows are hidden (unmapped) so the underlying OS wallpaper
        shows through again, but the player process keeps running so the
        wallpaper can be brought back instantly with ``start_wallpaper``.
        """
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.stop_wallpaper()

    @staticmethod
    def start_wallpaper():
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.start_wallpaper()

    def show_gui(self):
        """Show main GUI"""
        self.gui_process = Process(
            name="hidamari_prism-gui",
            target=gui_main,
            args=(
                self.version,
                self.pkgdatadir,
                self.localedir,
            ),
        )
        self.gui_process.start()

    def quit(self):
        # Do NOT do a synchronous DBus ``quit_player()`` here (see the note in
        # ``_setup_player``): that call only returns after the player's
        # window.cleanup() finishes, which can block forever on a wedged VLC
        # decoder. If this method stalls, the GUI blocked waiting for its reply
        # never closes and the whole app hangs. Terminating the process signal
        # stops its video just as well and always returns.
        for process in [self.player_process, self.gui_process, self.sys_icon_process]:
            if process and process.is_alive():
                process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    logger.warning(f"[Server] Process {process.name} didn't terminate, killing it")
                    process.kill()
                    process.join(timeout=1)

        loop.quit()
        logger.info("[Server] Stopped")

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.volume = volume

    @property
    def blur_radius(self):
        return self.config[CONFIG_KEY_BLUR_RADIUS]

    @blur_radius.setter
    def blur_radius(self, blur_radius):
        self.config[CONFIG_KEY_BLUR_RADIUS] = blur_radius
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.is_mute = is_mute

    @property
    def is_playing(self):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            return player.is_playing
        return False

    @property
    def is_paused_by_user(self):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None and player.mode in [MODE_VIDEO, MODE_STREAM]:
            return player.is_paused_by_user
        return None

    @is_paused_by_user.setter
    def is_paused_by_user(self, is_paused_by_user):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None and player.mode in [MODE_VIDEO, MODE_STREAM]:
            player.is_paused_by_user = is_paused_by_user

    @property
    def is_static_wallpaper(self):
        return self.config[CONFIG_KEY_STATIC_WALLPAPER]

    @is_static_wallpaper.setter
    def is_static_wallpaper(self, is_static_wallpaper):
        self.config[CONFIG_KEY_STATIC_WALLPAPER] = is_static_wallpaper
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def is_pause_when_maximized(self):
        return self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]

    @is_pause_when_maximized.setter
    def is_pause_when_maximized(self, is_pause_when_maximized):
        self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] = is_pause_when_maximized
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def is_mute_when_maximized(self):
        return self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]

    @is_mute_when_maximized.setter
    def is_mute_when_maximized(self, is_mute_when_maximized):
        self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED] = is_mute_when_maximized
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def shuffle_independent(self):
        """Read the per-monitor independent-shuffle toggle from the config."""
        shuffle_config = self.config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        return bool(shuffle_config.get(CONFIG_KEY_SHUFFLE_INDEPENDENT, False))

    @shuffle_independent.setter
    def shuffle_independent(self, enabled):
        logger.info(f"[Server] shuffle_independent -> {bool(enabled)}")
        self.config[CONFIG_KEY_SHUFFLE][CONFIG_KEY_SHUFFLE_INDEPENDENT] = bool(enabled)
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_shuffle()


def get_instance(dbus_name):
    bus = SessionBus()
    try:
        instance = bus.get(dbus_name)
    except GLib.Error:
        return None
    return instance


def main(version, pkgdatadir, localedir, args):
    server = get_instance(DBUS_NAME_SERVER)
    if server is not None:
        server.show_gui()
    else:
        # Pause before launching
        time.sleep(args.p)
        bus = SessionBus()
        server = Hidamari_PrismServer(version, pkgdatadir, localedir, args)
        try:
            bus.publish(DBUS_NAME_SERVER, server)
            loop.run()
        except RuntimeError as e:
            raise Exception("Failed to create server") from e
