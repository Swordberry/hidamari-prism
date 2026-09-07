import argparse
import logging
import os
import sys

from hidamari_prism import server
from hidamari_prism.commons import LOGGER_NAME, VIDEO_WALLPAPER_DIR
from hidamari_prism.utils import is_flatpak, is_gnome, is_wayland

logger = logging.getLogger(LOGGER_NAME)


# TODO: Add locale support
def main(version="devel", pkgdatadir="/usr/share/hidamari_prism", localedir="/usr/share/locale"):
    # Make sure that X11 is the backend. Revert Wayland to XWayland.
    os.environ["GDK_BACKEND"] = "x11"
    # Suppress VLC Log
    os.environ["VLC_VERBOSE"] = "-1"

    parser = argparse.ArgumentParser(description=f"Hidamari_Prism v{version}")
    parser.add_argument(
        "-p",
        "--pause",
        dest="p",
        type=int,
        default=0,
        help="Add pause before launching Hidamari_Prism. [sec]",
    )
    parser.add_argument(
        "-b", "--background", action="store_true", help="Launch only the live wallpaper."
    )
    parser.add_argument(
        "--stop-wallpaper",
        action="store_true",
        help="Turn the live wallpaper off on every monitor (from the taskbar menu).",
    )
    parser.add_argument(
        "--start-wallpaper",
        action="store_true",
        help="Bring the live wallpaper back on every monitor.",
    )
    parser.add_argument("-d", "--debug", action="store_true", help="Print debug messages.")
    parser.add_argument("-r", "--reset", action="store_true", help="Reset user configuration.")
    args = parser.parse_args()

    # Setup logger
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    # Desktop-file action entry points: talk to the already-running server and exit.
    if args.stop_wallpaper or args.start_wallpaper:
        existing_server = server.get_instance(server.DBUS_NAME_SERVER)
        if existing_server is None:
            logger.warning("[CLI] No running server; nothing to stop/start")
            return
        if args.stop_wallpaper:
            existing_server.stop_wallpaper()
        else:
            existing_server.start_wallpaper()
        return

    # Log system information
    sys_info = []
    sys_info.append("--- System information ---")
    sys_info.append(f"is_gnome = {is_gnome()}")
    sys_info.append(f"is_wayland = {is_wayland()}")
    sys_info.append(f"is_flatpak = {is_flatpak()}")
    sys_info.append("--------------------------")
    sys_info_str = "\n".join(sys_info)
    logger.info(f"Hidamari_Prism v{version}\n{sys_info_str}")
    logger.info(f"[Args] {vars(args)}")

    # Make Hidamari_Prism folder if not exist
    os.makedirs(VIDEO_WALLPAPER_DIR, exist_ok=True)

    # Clear sys.argv as it has influence to the Gtk.Application
    sys.argv = [sys.argv[0]]
    server.main(version, pkgdatadir, localedir, args)


if __name__ == "__main__":
    main()
