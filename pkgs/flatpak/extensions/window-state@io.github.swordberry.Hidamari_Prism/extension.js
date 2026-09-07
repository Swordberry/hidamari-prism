/*
 * Hidamari Prism Window State
 *
 * GNOME intentionally hides window introspection from (sandboxed) apps:
 * org.gnome.Shell.Eval, Introspect.GetWindows and GetRunningApplications all
 * answer "not allowed". The only supported channel for a wallpaper app to know
 * whether a window is maximized or fullscreen is a GNOME Shell extension.
 *
 * When the user enables "Pause when maximized window" on Wayland, Hidamari
 * Prism installs and enables this extension, which watches native window
 * events and mirrors the result to a tiny state file inside the app's own
 * data directory (which the sandbox can read):
 *
 *   ~/.var/app/io.github.swordberry.Hidamari_Prism/window_state
 *
 *   m=0|1   any (non-wallpaper) window on the active workspace is maximized
 *   f=0|1   any (non-wallpaper) window on the active workspace is fullscreen
 *
 * The wallpaper windows themselves are Gdk DESKTOP-hinted and their wm_class
 * matches "hidamari_prism", so they never count as covering windows.
 */
const { Gio, GLib, Meta } = imports.gi;

const APP_DIR = GLib.get_home_dir() + '/.var/app/io.github.swordberry.Hidamari_Prism';
const STATE_PATH = APP_DIR + '/window_state';
const TEMP_PATH  = APP_DIR + '/window_state.tmp';

let _signals = [];
let _windowSignals = new Map();

function skipWindow(w) {
    if (!w || w.minimized)
        return true;
    const type = w.get_window_type();
    if (type === Meta.WindowType.DESKTOP ||
        type === Meta.WindowType.DOCK ||
        type === Meta.WindowType.PANEL)
        return true;
    // Our own wallpaper windows (XWayland, desktop-hinted) never "cover".
    try {
        const cls = String((w.get_wm_class && w.get_wm_class()) ||
                           (w.get_wm_class_instance && w.get_wm_class_instance()) || '');
        if (cls.toLowerCase().includes('hidamari_prism'))
            return true;
    } catch (e) { }
    return false;
}

function isMaximized(w) {
    try {
        if (w.maximized === true)
            return true;
        return (Number(w.maximized) || 0) !== 0;
    } catch (e) {
        return false;
    }
}

function isCovering(w, ws) {
    if (skipWindow(w))
        return false;
    if (!w.is_on_all_workspaces() && w.get_workspace() !== ws)
        return false;
    return true;
}

function compute() {
    let anyMax = false;
    let anyFs = false;
    const ws = global.workspace_manager.get_workspace_by_index(
        global.workspace_manager.get_active_workspace_index());
    try {
        for (const actor of global.get_window_actors()) {
            const w = actor.meta_window;
            if (!isCovering(w, ws))
                continue;
            try {
                if (w.fullscreen)
                    anyFs = true;
                if (isMaximized(w))
                    anyMax = true;
            } catch (e) { }
        }
    } catch (e) { }
    writeState(anyMax, anyFs);
}

function writeState(max, fs) {
    try {
        GLib.mkdir_with_parents(APP_DIR, 0o755);
        const tmp = Gio.File.new_for_path(TEMP_PATH);
        const out = new Gio.DataOutputStream(
            tmp.replace(null, false, Gio.FileCreateFlags.NONE, null));
        out.put_string(`m=${max ? 1 : 0}\nf=${fs ? 1 : 0}\n`, null);
        out.close(null);
        tmp.move(Gio.File.new_for_path(STATE_PATH), Gio.FileCopyFlags.OVERWRITE, null, null);
    } catch (e) {
        log(`[HidamariWindowState] writeState: ${e}`);
    }
}

function connectWindow(w) {
    if (!w || _windowSignals.has(w))
        return;
    const ids = [];
    for (const prop of ['maximized', 'fullscreen', 'minimized', 'workspace']) {
        try {
            ids.push(w.connect(`notify::${prop}`, () => compute()));
        } catch (e) { }
    }
    _windowSignals.set(w, ids);
}

function disconnectWindow(w) {
    for (const id of (_windowSignals.get(w) || [])) {
        try { w.disconnect(id); } catch (e) { }
    }
    _windowSignals.delete(w);
}

function enable() {
    const onCreated = (display, w) => { connectWindow(w); compute(); };
    const onDestroyed = () => compute();
    _signals.push([global.display, global.display.connect('window-created', onCreated)]);
    _signals.push([global.display, global.display.connect('window-destroyed', onDestroyed)]);
    try {
        _signals.push([global.display, global.display.connect('window-unmanaging', (d, w) => {
            disconnectWindow(w);
            compute();
        })]);
    } catch (e) { }
    try {
        _signals.push([global.display, global.display.connect('window-state-changed', onDestroyed)]);
    } catch (e) { }
    _signals.push([global.workspace_manager,
                   global.workspace_manager.connect('notify::active-workspace-index', onDestroyed)]);

    for (const actor of global.get_window_actors()) {
        connectWindow(actor.meta_window);
    }
    compute();
}

function disable() {
    for (const [w, ids] of _windowSignals) {
        for (const id of ids) {
            try { w.disconnect(id); } catch (e) { }
        }
    }
    _windowSignals.clear();
    for (const [obj, id] of _signals) {
        try { obj.disconnect(id); } catch (e) { }
    }
    _signals = [];
    try {
        GLib.unlink(STATE_PATH);
    } catch (e) { }
}