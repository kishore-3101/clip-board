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
#!/usr/bin/env python3

import sqlite3
import sys
import os
import hashlib
import time
import subprocess

from urllib.parse import unquote


RUN = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
    "myclip"
)

DB = RUN + "/history.db"

MAX_ITEMS = 200
MAX_BYTES = 50_000_000

os.makedirs(RUN, mode=0o700, exist_ok=True)


# ---------------- DATABASE ----------------

def conn():
    c = sqlite3.connect(DB)

    c.execute("""
        CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT UNIQUE,
            kind TEXT,
            mime TEXT,
            content TEXT,
            path TEXT,
            ts REAL
        )
    """)

    return c


# ---------------- CLIPBOARD ----------------

def wl(*args):
    return subprocess.run(
        ["wl-paste", *args],
        capture_output=True
    ).stdout


def store():

    sys.stdin.buffer.read()

    types = wl("--list-types").decode().split()

    if not types or "x-kde-passwordManagerHint" in types:
        return

    kind = mime = content = path = None

    if "text/uri-list" in types:

        data = wl("--type", "text/uri-list")

        kind = "files"
        mime = "text/uri-list"
        content = data.decode("utf-8", "replace")

    elif any(t.startswith("image/") for t in types):

        mime = next(
            t for t in types
            if t.startswith("image/")
        )

        data = wl("--type", mime)

        kind = "image"

    elif any(
        t.startswith("text") or
        t in ("STRING", "UTF8_STRING")
        for t in types
    ):

        data = wl("--type", "text")

        kind = "text"
        mime = "text/plain"
        content = data.decode("utf-8", "replace")

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

    c.execute("""
        INSERT INTO items(
            hash, kind, mime, content, path, ts
        )
        VALUES (?, ?, ?, ?, ?, ?)

        ON CONFLICT(hash)
        DO UPDATE SET ts=excluded.ts
    """, (
        h,
        kind,
        mime,
        content,
        path,
        time.time()
    ))

    # Remove old entries

    old = c.execute("""
        SELECT id, path
        FROM items
        ORDER BY ts DESC
        LIMIT -1 OFFSET ?
    """, (MAX_ITEMS,)).fetchall()

    for item_id, old_path in old:

        c.execute(
            "DELETE FROM items WHERE id=?",
            (item_id,)
        )

        if old_path and os.path.exists(old_path):
            os.remove(old_path)

    c.commit()
    c.close()


# ---------------- RESTORE ----------------

def restore(item):

    _, kind, mime, content, path = item

    if kind == "image":

        with open(path, "rb") as f:

            subprocess.run(
                ["wl-copy", "--type", mime],
                stdin=f
            )

    else:

        subprocess.run(
            ["wl-copy", "--type", mime],
            input=content.encode()
        )


# ---------------- DELETE ITEM ----------------

def delete_item(item_id):

    c = conn()

    row = c.execute(
        "SELECT path FROM items WHERE id=?",
        (item_id,)
    ).fetchone()

    if row:

        path = row[0]

        c.execute(
            "DELETE FROM items WHERE id=?",
            (item_id,)
        )

        c.commit()

        if path and os.path.exists(path):
            os.remove(path)

    c.close()


# ---------------- CLEAR HISTORY ----------------

def clear_history():

    c = conn()

    rows = c.execute(
        "SELECT path FROM items"
    ).fetchall()

    c.execute("DELETE FROM items")

    c.execute(
        "DELETE FROM sqlite_sequence WHERE name='items'"
    )

    c.commit()
    c.close()

    for (path,) in rows:

        if path and os.path.exists(path):
            os.remove(path)


# ---------------- GTK UI ----------------

def ui():

    import gi

    gi.require_version("Gtk", "3.0")

    from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

    GLib.set_prgname("myclip")

    items = conn().execute("""
        SELECT id, kind, mime, content, path
        FROM items
        ORDER BY ts DESC
    """).fetchall()

    win = Gtk.Window(title="Clipboard")

    win.set_default_size(600, 700)

    win.connect("destroy", Gtk.main_quit)

    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6
    )

    box.set_border_width(8)

    win.add(box)

    # SEARCH

    search = Gtk.SearchEntry(
        placeholder_text="Search clipboard..."
    )

    box.pack_start(search, False, False, 0)

    # HEADER

    header = Gtk.Box(spacing=6)

    title = Gtk.Label(label="Clipboard History")

    title.set_xalign(0)

    header.pack_start(title, True, True, 0)

    clear_btn = Gtk.Button(label="Clear History")

    clear_btn.set_tooltip_text(
        "Delete all clipboard history"
    )

    clear_btn.get_style_context().add_class(
        "destructive-action"
    )

    header.pack_end(clear_btn, False, False, 0)

    box.pack_start(header, False, False, 0)

    # SCROLL AREA

    scroll = Gtk.ScrolledWindow()

    box.pack_start(scroll, True, True, 0)

    lb = Gtk.ListBox()

    lb.set_selection_mode(Gtk.SelectionMode.SINGLE)

    scroll.add(lb)

    rows = []

    # ---------------- BUILD ITEMS ----------------

    for it in items:

        item_id, kind, mime, content, path = it

        hb = Gtk.Box(spacing=10)

        hb.set_border_width(6)

        # IMAGE PREVIEW

        if kind == "image":

            try:

                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path,
                    120,
                    80,
                    True
                )

                hb.pack_start(
                    Gtk.Image.new_from_pixbuf(pb),
                    False,
                    False,
                    0
                )

            except Exception:

                hb.pack_start(
                    Gtk.Image.new_from_icon_name(
                        "image-x-generic",
                        Gtk.IconSize.DIALOG
                    ),
                    False,
                    False,
                    0
                )

            text = "Image (" + mime + ")"

        # FILES

        elif kind == "files":

            names = [
                unquote(u.strip().split("/")[-1])
                for u in content.splitlines()
                if u.strip()
            ]

            vid = any(
                n.lower().endswith(
                    (".mp4", ".mkv", ".webm", ".avi", ".mov")
                )
                for n in names
            )

            icon = (
                "video-x-generic"
                if vid
                else "text-x-generic"
            )

            hb.pack_start(
                Gtk.Image.new_from_icon_name(
                    icon,
                    Gtk.IconSize.DIALOG
                ),
                False,
                False,
                0
            )

            text = "\n".join(names[:5])

            if len(names) > 5:
                text += f"\n... +{len(names)-5} more"

        # TEXT

        else:

            text = content.strip()[:300]

        # CONTENT LABEL

        lbl = Gtk.Label(
            label=text,
            xalign=0
        )

        lbl.set_line_wrap(True)
        lbl.set_lines(4)
        lbl.set_ellipsize(3)

        hb.pack_start(
            lbl,
            True,
            True,
            0
        )

        # DELETE BUTTON

        delete_btn = Gtk.Button()

        delete_btn.set_image(
            Gtk.Image.new_from_icon_name(
                "user-trash",
                Gtk.IconSize.BUTTON
            )
        )

        delete_btn.set_tooltip_text(
            "Delete this entry"
        )

        delete_btn.set_relief(
            Gtk.ReliefStyle.NONE
        )

        hb.pack_start(
            delete_btn,
            False,
            False,
            0
        )

        # LIST ROW

        row = Gtk.ListBoxRow()

        row.add(hb)

        row.item = it
        row.search = text.lower()

        lb.add(row)

        rows.append(row)

        # DELETE CALLBACK

        def delete_clicked(
            btn,
            current_id=item_id,
            current_row=row
        ):

            delete_item(current_id)

            lb.remove(current_row)

            if current_row in rows:
                rows.remove(current_row)

        delete_btn.connect(
            "clicked",
            delete_clicked
        )

    # ---------------- PICK ITEM ----------------

    def pick(_, row):

        if row is None:
            return

        restore(row.item)

        Gtk.main_quit()

    lb.connect(
        "row-activated",
        pick
    )

    # ---------------- SEARCH FILTER ----------------

    lb.set_filter_func(
        lambda r: search.get_text().lower() in r.search
    )

    search.connect(
        "search-changed",
        lambda e: lb.invalidate_filter()
    )

    # ---------------- CLEAR HISTORY ----------------

    def clear_clicked(btn):

        dialog = Gtk.MessageDialog(
            transient_for=win,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Clear clipboard history?"
        )

        dialog.format_secondary_text(
            "This will permanently delete all clipboard entries."
        )

        response = dialog.run()

        dialog.destroy()

        if response == Gtk.ResponseType.YES:

            clear_history()

            for row in rows[:]:
                lb.remove(row)

            rows.clear()

    clear_btn.connect(
        "clicked",
        clear_clicked
    )

    # ---------------- KEYBOARD NAVIGATION ----------------

    def first_visible():

        return next(
            (
                r for r in rows
                if r.get_child_visible()
                and search.get_text().lower() in r.search
            ),
            None
        )

    search.connect(
        "activate",
        lambda e: (
            lb.emit("row-activated", first_visible())
            if first_visible()
            else None
        )
    )

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

    win.connect(
        "key-press-event",
        on_key
    )

    # ---------------- SHOW WINDOW ----------------

    win.show_all()

    search.grab_focus()

    Gtk.main()


# ---------------- WIPE STORAGE ----------------

def wipe():

    if os.path.exists(RUN):

        for f in os.listdir(RUN):

            path = os.path.join(RUN, f)

            if os.path.isfile(path):
                os.remove(path)


# ---------------- COMMANDS ----------------

cmds = {
    "store": store,
    "ui": ui,
    "wipe": wipe
}


if len(sys.argv) < 2 or sys.argv[1] not in cmds:

    sys.exit(
        "usage: myclip {store|ui|wipe}"
    )


cmds[sys.argv[1]]()


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