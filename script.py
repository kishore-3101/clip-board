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
        return  # skip password managers
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
    # trim old entries and their image files
    old = c.execute("""SELECT id,path FROM items ORDER BY ts DESC LIMIT -1 OFFSET ?""",
                    (MAX_ITEMS,)).fetchall()
    for id_, p in old:
        c.execute("DELETE FROM items WHERE id=?", (id_,))
        if p and os.path.exists(p):
            os.remove(p)
    c.commit()

def restore(item):
    _, kind, mime, content, path = item
    if kind == "image":
        with open(path, "rb") as f:
            subprocess.run(["wl-copy", "--type", mime], stdin=f)
    else:
        subprocess.run(["wl-copy", "--type", mime], input=content.encode())

def ui():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
    GLib.set_prgname("myclip")  # becomes the Sway app_id

    items = conn().execute(
        "SELECT id,kind,mime,content,path FROM items ORDER BY ts DESC").fetchall()

    win = Gtk.Window(title="Clipboard")
    win.set_default_size(600, 700)
    win.connect("destroy", Gtk.main_quit)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_border_width(8)
    win.add(box)

    search = Gtk.SearchEntry(placeholder_text="Search…")
    box.pack_start(search, False, False, 0)
    scroll = Gtk.ScrolledWindow()
    box.pack_start(scroll, True, True, 0)
    lb = Gtk.ListBox()
    scroll.add(lb)

    rows = []
    for it in items:
        _, kind, mime, content, path = it
        hb = Gtk.Box(spacing=10)
        hb.set_border_width(6)
        if kind == "image":
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 120, 80, True)
                hb.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
            except Exception:
                hb.pack_start(Gtk.Image.new_from_icon_name("image-x-generic", Gtk.IconSize.DIALOG), False, False, 0)
            text = "Image (" + mime + ")"
        elif kind == "files":
            names = [unquote(u.strip().split("/")[-1])
                     for u in content.splitlines() if u.strip()]
            vid = any(n.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")) for n in names)
            icon = "video-x-generic" if vid else "text-x-generic"
            hb.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DIALOG), False, False, 0)
            text = "\n".join(names[:5]) + (f"\n… +{len(names)-5} more" if len(names) > 5 else "")
        else:
            text = content.strip()[:300]
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_line_wrap(True)
        lbl.set_lines(4)
        lbl.set_ellipsize(3)
        hb.pack_start(lbl, True, True, 0)
        row = Gtk.ListBoxRow()
        row.add(hb)
        row.item, row.search = it, text.lower()
        lb.add(row)
        rows.append(row)

    def pick(_, row):
        restore(row.item)
        Gtk.main_quit()

    lb.connect("row-activated", pick)
    lb.set_filter_func(lambda r: search.get_text().lower() in r.search)
    search.connect("search-changed", lambda e: lb.invalidate_filter())

    def first_visible():
        return next((r for r in rows if r.get_child_visible()
                     and search.get_text().lower() in r.search), None)

    search.connect("activate", lambda e: (first_visible() or Gtk.Widget).activate()
                   if first_visible() else None)

    def on_key(w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
        elif ev.keyval == Gdk.KEY_Down and search.is_focus():
            r = first_visible()
            if r:
                lb.select_row(r); r.grab_focus()
            return True
    win.connect("key-press-event", on_key)

    win.show_all()
    search.grab_focus()
    Gtk.main()

def wipe():
    for f in os.listdir(RUN):
        os.remove(os.path.join(RUN, f))

cmds = {"store": store, "ui": ui, "wipe": wipe}
if len(sys.argv) < 2 or sys.argv[1] not in cmds:
    sys.exit("usage: myclip {store|ui|wipe}")
cmds[sys.argv[1]]()