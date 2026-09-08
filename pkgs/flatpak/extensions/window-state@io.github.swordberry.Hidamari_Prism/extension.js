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
 *   m=0|1   any (non-wallpaper) window on the active workspace is maximized
 *   f=0|1   any (non-wallpaper) window on the active workspace is fullscreen
 *
 * It polls once per second, which is far cheaper than the per-frame decode
 * work it lets the wallpaper skip while covered.
 *
 * Written in the GNOME Shell 45+ ESM format (works on 45 through 49) and only
 * using window APIs that are stable across those versions (no global.display,
 * which mutter 46 removed).
 */
import GLib from 'gi://GLib';

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

    _compute() {
        let anyMax = false;
        let anyFs = false;

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
                try {
                    if (w.is_fullscreen())
                        anyFs = true;
                } catch (e) { }
                try {
                    if (this._isMaximized(w))
                        anyMax = true;
                } catch (e) { }
            }
        } catch (e) {
            log(`[HidamariWindowState] compute: ${e}`);
        }

        this._writeState(anyMax, anyFs);
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

    _writeState(max, fs) {
        try {
            GLib.mkdir_with_parents(APP_DIR, 0o755);
            const content = `m=${max ? 1 : 0}\nf=${fs ? 1 : 0}\n`;
            // GLib.file_set_contents writes atomically and needs no hand-rolled
            // stream plumbing (Gio.DataOutputStream's constructor takes a
            // param-spec first, which is easy to get wrong with replace()).
            GLib.file_set_contents(STATE_PATH, content);
        } catch (e) {
            log(`[HidamariWindowState] writeState: ${e}`);
        }
    }
}
