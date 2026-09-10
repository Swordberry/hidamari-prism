/*
 * Hidamari Prism Window State
 *
 * GNOME intentionally hides window introspection from (sandboxed) apps:
 * org.gnome.Shell.Eval, Introspect.GetWindows and GetRunningApplications all
 * answer "not allowed". The only supported channel for a wallpaper app to know
 * whether a window is maximized or fullscreen is a GNOME Shell extension.
 *
 * When the user enables "Pause when maximized window" on Wayland, Hidamari
 * Prism installs and enables this extension, which mirrors the result to a
 * tiny state file inside the app's own data directory (which the sandbox can
 * read):
 *
 *   ~/.var/app/io.github.swordberry.Hidamari_Prism/window_state
 *
 *   eDP-1=m          one "connector=state" line per monitor that has a
 *   HDMI-A-1=f       maximized (m) or fullscreen (f) window on the active
 *                    workspace; monitors without a blocking window are simply
 *                    absent. The app pauses only the wallpapers on the listed
 *                    monitors, so the other screens keep animating.
 *
 * It polls once per second, which is far cheaper than the per-frame decode
 * work it lets the wallpaper skip while covered.
 *
 * Written in the GNOME Shell 45+ ESM format (works on 45 through 49) and only
 * using window APIs that are stable across those versions (no global.display,
 * which mutter 46 removed).
 */
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_DIR = GLib.get_home_dir() + '/.var/app/io.github.swordberry.Hidamari_Prism';
const STATE_PATH = APP_DIR + '/window_state';

export default class HidamariWindowStateExtension extends Extension {
    enable() {
        this._timer = null;
        this._compute();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT_IDLE, 1, () => {
            this._compute();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        try {
            GLib.unlink(STATE_PATH);
        } catch (e) { }
    }

    _connectorForMonitor(monitorIndex) {
        try {
            const mm = Meta.MonitorManager.get();
            try {
                const spec = mm.get_monitor_spec(monitorIndex);
                if (spec && spec.connector)
                    return spec.connector;
            } catch (e) { }
            try {
                const info = mm.get_monitor_infos()[monitorIndex];
                if (info && info.connector)
                    return info.connector;
            } catch (e) { }
        } catch (e) { }
        return null;
    }

    _compute() {
        const busy = new Map();

        let ws = null;
        try {
            ws = global.workspace_manager.get_workspace_by_index(
                global.workspace_manager.get_active_workspace_index());
        } catch (e) { }

        try {
            for (const actor of global.get_window_actors()) {
                const w = actor.get_meta_window();
                if (!w || w.minimized)
                    continue;
                if (this._isWallpaperWindow(w))
                    continue;
                if (!this._isOnActiveWorkspace(w, ws))
                    continue;
                let flags = '';
                try {
                    if (w.is_fullscreen())
                        flags += 'f';
                } catch (e) { }
                try {
                    if (this._isMaximized(w))
                        flags += 'm';
                } catch (e) { }
                if (!flags)
                    continue;
                try {
                    const conn = this._connectorForMonitor(w.get_monitor());
                    if (!conn)
                        continue;
                    const merged = busy.has(conn) ? busy.get(conn) + flags : flags;
                    busy.set(conn, [...new Set(merged.split(''))].join('+'));
                } catch (e) { }
            }
        } catch (e) {
            log(`[HidamariWindowState] compute: ${e}`);
        }

        this._writeState(busy);
    }

    _isWallpaperWindow(w) {
        try {
            let cls = '';
            if (typeof w.get_wm_class === 'function')
                cls = w.get_wm_class() || '';
            if (!cls && typeof w.get_wm_class_instance === 'function')
                cls = w.get_wm_class_instance() || '';
            if (cls.toLowerCase().includes('hidamari_prism'))
                return true;
        } catch (e) { }
        return false;
    }

    _writeState(busy) {
        try {
            GLib.mkdir_with_parents(APP_DIR, 0o755);
            let content = '';
            for (const [conn, flags] of busy)
                content += `${conn}=${flags}\n`;
            // GLib.file_set_contents writes atomically and needs no hand-rolled
            // stream plumbing (Gio.DataOutputStream's constructor takes a
            // param-spec first, which is easy to get wrong with replace()).
            GLib.file_set_contents(STATE_PATH, content);
        } catch (e) {
            log(`[HidamariWindowState] writeState: ${e}`);
        }
    }

    _isOnActiveWorkspace(w, ws) {
        try {
            if (w.is_on_all_workspaces())
                return true;
            return w.get_workspace() === ws;
        } catch (e) {
            return true;
        }
    }

    _isMaximized(w) {
        // GNOME 49+ exposes is_maximized(); 45-48 use the get_maximized()
        // bitmask where Meta.MaximizeFlags.BOTH === 3.
        if (typeof w.is_maximized === 'function')
            return w.is_maximized();
        try {
            return (w.get_maximized() || 0) === 3;
        } catch (e) {
            return false;
        }
    }
}
