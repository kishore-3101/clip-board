#!/usr/bin/env python3
import sqlite3, sys, os, hashlib, time, subprocess
from urllib.parse import unquote

RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "myclip")
DB = RUN + "/history.db"
MAX_ITEMS = 200
MAX_BYTES = 50_000_000
os.makedirs(RUN, mode=0o700, exist_ok=True)


def conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE,
        kind TEXT, mime TEXT, content TEXT, path TEXT, ts REAL)""")
    return c


def wl(*args):
    return subprocess.run(["wl-paste", *args], capture_output=True).stdout


def store():
    sys.stdin.buffer.read()  # drain the pipe from --watch
    types = wl("--list-types").decode().split()
    if not types or "x-kde-passwordManagerHint" in types:
        return
    kind = mime = content = path = None
    if "text/uri-list" in types:
        data = wl("--type", "text/uri-list")
        kind, mime, content = "files", "text/uri-list", data.decode("utf-8", "replace")
    elif any(t.startswith("image/") for t in types):
        mime = next(t for t in types if t.startswith("image/"))
        data = wl("--type", mime)
        kind = "image"
    elif any(t.startswith("text") or t in ("STRING", "UTF8_STRING") for t in types):
        data = wl("--type", "text")
        kind, mime, content = "text", "text/plain", data.decode("utf-8", "replace")
    else:
        return
    if not data.strip() or len(data) > MAX_BYTES:
        return
    h = hashlib.sha256(data).hexdigest()
    if kind == "image":
        path = f"{RUN}/{h}.{mime.split('/')[1]}"
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
    c = conn()
    c.execute("""INSERT INTO items(hash,kind,mime,content,path,ts) VALUES(?,?,?,?,?,?)
                 ON CONFLICT(hash) DO UPDATE SET ts=excluded.ts""",
              (h, kind, mime, content, path, time.time()))
    old = c.execute("SELECT id,path FROM items ORDER BY ts DESC LIMIT -1 OFFSET ?",
                    (MAX_ITEMS,)).fetchall()
    for id_, p in old:
        c.execute("DELETE FROM items WHERE id=?", (id_,))
        if p and os.path.exists(p):
            os.remove(p)
    c.commit()
    c.close()


def restore(item):
    _, kind, mime, content, path = item
    if kind == "image":
        with open(path, "rb") as f:
            subprocess.run(["wl-copy", "--type", mime], stdin=f)
    else:
        subprocess.run(["wl-copy", "--type", mime], input=content.encode())


def delete_item(item_id):
    c = conn()
    row = c.execute("SELECT path FROM items WHERE id=?", (item_id,)).fetchone()
    if row:
        c.execute("DELETE FROM items WHERE id=?", (item_id,))
        c.commit()
        if row[0] and os.path.exists(row[0]):
            os.remove(row[0])
    c.close()


def clear_history():
    c = conn()
    rows = c.execute("SELECT path FROM items").fetchall()
    c.execute("DELETE FROM items")
    c.execute("DELETE FROM sqlite_sequence WHERE name='items'")
    c.commit()
    c.close()
    for (p,) in rows:
        if p and os.path.exists(p):
            os.remove(p)


CSS = b"""
window.main, window.main.background {
    background-color: rgba(0, 0, 0, 0.72);
    background-image: none;
    border-radius: 12px;
}
window.main box, window.main grid { background: transparent; }

.main label { color: #e6e6e6; font-size: 13px; }
.main label.muted { color: #777777; font-size: 12px; }

.main entry {
    background-color: rgba(255, 255, 255, 0.05);
    background-image: none;
    color: #f0f0f0;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    box-shadow: none;
    padding: 9px 12px;
    min-height: 20px;
}
.main entry:focus { border-color: rgba(255, 255, 255, 0.25); }

.main scrolledwindow, .main viewport, .main list {
    background: transparent;
}
.main row {
    background: transparent;
    border-radius: 0;
    margin: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.38);
}
.main row:last-child { border-bottom: none; }
.main row:hover { background-color: rgba(255, 255, 255, 0.05); }
.main row:selected { background-color: rgba(255, 255, 255, 0.10); }
.main row:selected label { color: #ffffff; }

.main button {
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    text-shadow: none;
    border-radius: 6px;
    color: #777777;
    padding: 4px 8px;
}
.main button:hover { background-color: rgba(255, 255, 255, 0.08); color: #e6e6e6; }

.main row button.delete { opacity: 0; }
.main row:hover button.delete,
.main row:selected button.delete { opacity: 1; }

.main scrollbar { background: transparent; border: none; }
.main scrollbar slider {
    background-color: rgba(255, 255, 255, 0.18);
    border-radius: 4px;
    min-width: 4px;
    border: none;
}
"""


def ui():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
    GLib.set_prgname("myclip")  # becomes the Sway app_id

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    screen = Gdk.Screen.get_default()
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def add_class(widget, *names):
        for n in names:
            widget.get_style_context().add_class(n)
        return widget

    c = conn()
    items = c.execute(
        "SELECT id,kind,mime,content,path FROM items ORDER BY ts DESC").fetchall()
    c.close()

    win = Gtk.Window(title="Clipboard")
    add_class(win, "main")
    win.set_default_size(560, 640)
    win.set_decorated(False)
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)  # needed for transparency
    win.connect("destroy", Gtk.main_quit)

    state = {"busy": False}

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.set_border_width(12)
    win.add(box)

    # search
    search = Gtk.SearchEntry(placeholder_text="Search")
    box.pack_start(search, False, False, 0)

    # list
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    box.pack_start(scroll, True, True, 0)
    lb = Gtk.ListBox()
    lb.set_selection_mode(Gtk.SelectionMode.SINGLE)
    scroll.add(lb)

    empty = add_class(Gtk.Label(label="Nothing copied yet"), "muted")
    empty.set_no_show_all(True)
    box.pack_start(empty, True, True, 0)

    # footer
    footer = Gtk.Box(spacing=8)
    count = add_class(Gtk.Label(label="", xalign=0), "muted")
    footer.pack_start(count, True, True, 4)
    clear_btn = Gtk.Button(label="Clear all")
    clear_btn.set_tooltip_text("Delete all clipboard history")
    footer.pack_end(clear_btn, False, False, 0)
    box.pack_start(footer, False, False, 0)

    rows = []

    def refresh_state():
        n = len(rows)
        count.set_text(f"{n} item{'s' if n != 1 else ''}")
        scroll.set_visible(n > 0)
        empty.set_visible(n == 0)
        clear_btn.set_sensitive(n > 0)

    for it in items:
        item_id, kind, mime, content, path = it
        hb = Gtk.Box(spacing=12)
        hb.set_border_width(8)

        if kind == "image":
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 96, 64, True)
                hb.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
            except Exception:
                hb.pack_start(Gtk.Image.new_from_icon_name(
                    "image-x-generic-symbolic", Gtk.IconSize.LARGE_TOOLBAR),
                    False, False, 0)
            text = "Image (" + mime + ")"
        elif kind == "files":
            names = [unquote(u.strip().split("/")[-1])
                     for u in content.splitlines() if u.strip()]
            vid = any(n.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".mov"))
                      for n in names)
            icon = "video-x-generic-symbolic" if vid else "text-x-generic-symbolic"
            hb.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR),
                          False, False, 0)
            text = "\n".join(names[:5])
            if len(names) > 5:
                text += f"\n... +{len(names)-5} more"
        else:
            text = content.strip()[:300]

        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_line_wrap(True)
        lbl.set_lines(3)
        lbl.set_ellipsize(3)
        hb.pack_start(lbl, True, True, 0)

        delete_btn = add_class(Gtk.Button(), "delete")
        delete_btn.set_image(Gtk.Image.new_from_icon_name(
            "window-close-symbolic", Gtk.IconSize.BUTTON))
        delete_btn.set_tooltip_text("Delete")
        delete_btn.set_valign(Gtk.Align.CENTER)
        hb.pack_start(delete_btn, False, False, 0)

        row = Gtk.ListBoxRow()
        row.add(hb)
        row.item, row.search = it, text.lower()
        lb.add(row)
        rows.append(row)

        def delete_clicked(btn, current_id=item_id, current_row=row):
            delete_item(current_id)
            lb.remove(current_row)
            if current_row in rows:
                rows.remove(current_row)
            refresh_state()

        delete_btn.connect("clicked", delete_clicked)

    def pick(_, row):
        if row is None:
            return
        restore(row.item)
        Gtk.main_quit()

    lb.connect("row-activated", pick)
    lb.set_filter_func(lambda r: search.get_text().lower() in r.search)
    search.connect("search-changed", lambda e: lb.invalidate_filter())

    def clear_clicked(btn):
        state["busy"] = True
        dialog = Gtk.MessageDialog(
            transient_for=win, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Clear clipboard history?")
        dialog.format_secondary_text("This permanently deletes all entries.")
        response = dialog.run()
        dialog.destroy()
        state["busy"] = False
        if response == Gtk.ResponseType.YES:
            clear_history()
            for r in rows[:]:
                lb.remove(r)
            rows.clear()
            refresh_state()

    clear_btn.connect("clicked", clear_clicked)

    def first_visible():
        return next((r for r in rows if r.get_child_visible()
                     and search.get_text().lower() in r.search), None)

    def on_search_activate(e):
        r = first_visible()
        if r:
            pick(None, r)

    search.connect("activate", on_search_activate)

    def on_key(w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
        elif ev.keyval == Gdk.KEY_Down and search.is_focus():
            r = first_visible()
            if r:
                lb.select_row(r)
                r.grab_focus()
            return True
        return False

    win.connect("key-press-event", on_key)

    # close when the window loses focus (popup behaviour)
    def on_focus_out(w, ev):
        if not state["busy"]:
            Gtk.main_quit()
        return False

    win.connect("focus-out-event", on_focus_out)

    refresh_state()
    win.show_all()
    refresh_state()  # show_all() un-hides everything, so re-apply visibility
    search.grab_focus()
    Gtk.main()


def wipe():
    if os.path.exists(RUN):
        for f in os.listdir(RUN):
            p = os.path.join(RUN, f)
            if os.path.isfile(p):
                os.remove(p)


cmds = {"store": store, "ui": ui, "wipe": wipe}
if len(sys.argv) < 2 or sys.argv[1] not in cmds:
    sys.exit("usage: myclip {store|ui|wipe}")
cmds[sys.argv[1]]()