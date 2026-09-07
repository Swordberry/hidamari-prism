import os
from gettext import gettext as _

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from hidamari_prism.commons import (
    CONFIG_KEY_PLAYLISTS,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_SHUFFLE_ACTIVE,
    CONFIG_KEY_SHUFFLE_ENABLED,
    CONFIG_KEY_SHUFFLE_INTERVAL,
    SHUFFLE_INTERVAL_MIN_MIN,
    SHUFFLE_INTERVAL_STEP_MIN,
)


class PlaylistEditorDialog:
    """Named playlist editor with per-playlist shuffle.

    `config` is the GUI's live config dict; `on_apply` is called whenever the
    user saves so the caller can persist and push the changes to the player.
    """

    def __init__(self, parent, config, on_apply):
        self.parent = parent
        self.config = config
        self.on_apply = on_apply
        self.playlists = config.get(CONFIG_KEY_PLAYLISTS, {}) or {}
        self.shuffle = config.get(CONFIG_KEY_SHUFFLE, {}) or {}
        self.current = self.shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE, "")

        self.builder = Gtk.Builder()
        self._build_ui()
        self._reload_playlist_combo()

    def _build_ui(self):
        win = Gtk.Dialog(
            title=_("Playlists"),
            transient_for=self.parent,
            modal=True,
        )
        win.set_default_size(520, 420)
        self.win = win

        box = win.get_content_area()
        box.set_spacing(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        # ---- Playlist selector row ----
        sel_row = Gtk.Box(spacing=8)
        self.combo = Gtk.ComboBoxText()
        self.combo.set_hexpand(True)
        self.combo.connect("changed", self._on_combo_changed)
        sel_row.pack_start(self.combo, True, True, 0)

        add_btn = Gtk.Button(label=_("New"))
        add_btn.connect("clicked", lambda *_: self._new_playlist())
        sel_row.pack_start(add_btn, False, False, 0)
        rename_btn = Gtk.Button(label=_("Rename"))
        rename_btn.connect("clicked", lambda *_: self._rename_playlist())
        sel_row.pack_start(rename_btn, False, False, 0)
        del_btn = Gtk.Button(label=_("Delete"))
        del_btn.connect("clicked", lambda *_: self._delete_playlist())
        sel_row.pack_start(del_btn, False, False, 0)
        box.pack_start(sel_row, False, False, 0)

        # ---- Wallpapers list ----
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        self.store = Gtk.ListStore(str)
        tree = Gtk.TreeView(model=self.store)
        col = Gtk.TreeViewColumn(_("Wallpapers"), Gtk.CellRendererText(), text=0)
        tree.append_column(col)
        tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree = tree
        sw.add(tree)
        box.pack_start(sw, True, True, 0)

        # ---- Wallpaper action row ----
        act_row = Gtk.Box(spacing=8)
        add_vid = Gtk.Button(label=_("Add Wallpapers..."))
        add_vid.connect("clicked", lambda *_: self._add_videos())
        act_row.pack_start(add_vid, False, False, 0)
        rm_vid = Gtk.Button(label=_("Remove Selected"))
        rm_vid.connect("clicked", lambda *_: self._remove_selected())
        act_row.pack_start(rm_vid, False, False, 0)
        box.pack_start(act_row, False, False, 0)

        # ---- Shuffle options ----
        shuf_row = Gtk.Box(spacing=8)
        shuf_lbl = Gtk.Label(label=_("Shuffle this playlist"))
        shuf_row.pack_start(shuf_lbl, False, False, 0)
        self.switch = Gtk.Switch()
        self.switch.connect("state-set", self._on_shuffle_toggled)
        shuf_row.pack_start(self.switch, False, False, 0)

        self.intv_lbl = Gtk.Label(label=_("Interval (minutes)"))
        shuf_row.pack_start(self.intv_lbl, False, False, 0)

        adj = Gtk.Adjustment(
            value=SHUFFLE_INTERVAL_STEP_MIN,
            lower=SHUFFLE_INTERVAL_MIN_MIN,
            upper=60 * 24,
            step_increment=SHUFFLE_INTERVAL_STEP_MIN,
            page_increment=SHUFFLE_INTERVAL_STEP_MIN,
        )
        self.spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.0, digits=0)
        self.spin.set_increments(SHUFFLE_INTERVAL_STEP_MIN, SHUFFLE_INTERVAL_STEP_MIN)
        self.spin.connect("value-changed", self._on_interval_changed)
        shuf_row.pack_start(self.spin, False, False, 0)
        box.pack_start(shuf_row, False, False, 0)

        # ---- Apply / Close ----
        btn_row = Gtk.Box(spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        close_btn = Gtk.Button(label=_("Close"))
        close_btn.connect("clicked", lambda *_: win.destroy())
        btn_row.pack_start(close_btn, False, False, 0)
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.get_style_context().add_class("suggested-action")
        apply_btn.connect("clicked", lambda *_: self._apply())
        btn_row.pack_start(apply_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        win.show_all()
        self._update_shuffle_sensitivity()

    def _reload_playlist_combo(self):
        self.combo.handler_block_by_func(self._on_combo_changed)
        self.combo.remove_all()
        for name in self.playlists.keys():
            self.combo.append_text(name)
        if self.current in self.playlists:
            self.combo.set_active_id(self.current)
        else:
            self.combo.set_active(0 if self.playlists else -1)
        self.combo.handler_unblock_by_func(self._on_combo_changed)
        self._refresh_videos()

    def _current(self):
        name = self.combo.get_active_text()
        if not name:
            return None
        return self.playlists.get(name)

    def _refresh_videos(self):
        name = self.combo.get_active_text()
        self.store.clear()
        videos = self.playlists.get(name, []) if name else []
        for v in list(videos):
            if os.path.isfile(v):
                self.store.append([v])
        if name:
            self.current = name
        self.switch.handler_block_by_func(self._on_shuffle_toggled)
        self.switch.set_active(
            bool(self.shuffle.get(CONFIG_KEY_SHUFFLE_ENABLED)) and name == self.shuffle.get(
                CONFIG_KEY_SHUFFLE_ACTIVE, ""
            )
        )
        self.switch.handler_unblock_by_func(self._on_shuffle_toggled)
        interval = self.shuffle.get(CONFIG_KEY_SHUFFLE_INTERVAL, SHUFFLE_INTERVAL_STEP_MIN) or SHUFFLE_INTERVAL_STEP_MIN
        self.spin.handler_block_by_func(self._on_interval_changed)
        self.spin.set_value(interval)
        self.spin.handler_unblock_by_func(self._on_interval_changed)
        self._update_shuffle_sensitivity()

    def _update_shuffle_sensitivity(self):
        active = bool(self.combo.get_active_text())
        for w in (self.switch, self.spin, self.intv_lbl):
            w.set_sensitive(active)

    def _on_combo_changed(self, *_):
        self._refresh_videos()

    def _on_shuffle_toggled(self, widget, state, *_):
        name = self.combo.get_active_text()
        if not name:
            return
        self.shuffle[CONFIG_KEY_SHUFFLE_ENABLED] = bool(state)
        if state:
            self.shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = name
        else:
            if self.shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE) == name:
                self.shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = ""
        return False

    def _on_interval_changed(self, *_):
        self.shuffle[CONFIG_KEY_SHUFFLE_INTERVAL] = int(
            max(self.spin.get_value(), SHUFFLE_INTERVAL_MIN_MIN)
        )

    def _new_playlist(self):
        dialog = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("New playlist name"),
        )
        entry = Gtk.Entry()
        entry.set_activates_default(True)
        dialog.get_content_area().pack_end(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        new_name = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not new_name:
            return
        if new_name not in self.playlists:
            self.playlists[new_name] = []
        self._reload_playlist_combo()
        self.combo.set_active_id(new_name)

    def _rename_playlist(self):
        old = self.combo.get_active_text()
        if not old:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("Rename playlist"),
        )
        entry = Gtk.Entry()
        entry.set_text(old)
        entry.set_activates_default(True)
        dialog.get_content_area().pack_end(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        new_name = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not new_name or new_name == old:
            return
        videos = self.playlists.pop(old)
        self.playlists[new_name] = videos
        if self.shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE) == old:
            self.shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = new_name
        self._reload_playlist_combo()
        self.combo.set_active_id(new_name)

    def _delete_playlist(self):
        name = self.combo.get_active_text()
        if not name:
            return
        self.playlists.pop(name, None)
        if self.shuffle.get(CONFIG_KEY_SHUFFLE_ACTIVE) == name:
            self.shuffle[CONFIG_KEY_SHUFFLE_ACTIVE] = ""
        self._reload_playlist_combo()

    def _add_videos(self):
        name = self.combo.get_active_text()
        if not name:
            return
        dialog = Gtk.FileChooserDialog(
            title=_("Add Wallpapers"),
            transient_for=self.win,
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
        existing = set(self.playlists.get(name, []))
        for path in paths:
            if path not in existing:
                self.playlists.setdefault(name, []).append(path)
                existing.add(path)
        self._refresh_videos()

    def _remove_selected(self):
        name = self.combo.get_active_text()
        if not name:
            return
        paths = set()
        for tp in self.tree.get_selection().get_selected_rows()[1]:
            paths.add(self.store[tp][0])
        if not paths:
            return
        videos = self.playlists.get(name, [])
        self.playlists[name] = [v for v in videos if v not in paths]
        self._refresh_videos()

    def _selected_video(self):
        for tp in self.tree.get_selection().get_selected_rows()[1]:
            return self.store[tp][0]
        return None

    def _apply(self):
        # Persist current dialog state back into config
        self.config[CONFIG_KEY_PLAYLISTS] = self.playlists
        self.config[CONFIG_KEY_SHUFFLE] = self.shuffle
        if self.on_apply:
            # Pass the specifically-selected video (if any) so Apply can play it.
            self.on_apply(self._selected_video())
