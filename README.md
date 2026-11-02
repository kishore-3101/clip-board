# myclip

A lightweight clipboard history manager for **Sway / Wayland** with a floating GTK window.
Stores copied **text, images, and files/videos** in RAM, and clears everything on shutdown.

## Requirements

| Package | Purpose |
|---|---|
| `wl-clipboard` | `wl-copy` / `wl-paste` |
| `python` (3.8+) | runs the script |
| `python-gobject`, `gtk3` | popup window |

Arch:
```bash
sudo pacman -S wl-clipboard python-gobject gtk3
```

Debian/Ubuntu:
```bash
sudo apt install wl-clipboard python3-gi gir1.2-gtk-3.0
```

## Install

```bash
git clone https://github.com/kishore-3101/myclip.git ~/projects/myclip
mkdir -p ~/.local/bin
ln -sf ~/projects/myclip/myclip.py ~/.local/bin/myclip
chmod +x ~/projects/myclip/myclip.py
```

Make sure `~/.local/bin` is in your `PATH`:
```bash
echo $PATH | grep -q .local/bin || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

## Sway setup

Add to `~/.config/sway/config`:

```
exec_always "pkill -x wl-paste; wl-paste --watch ~/.local/bin/myclip store"
for_window [app_id="myclip"] floating enable, resize set 600 700, move position center
bindsym $mod+v exec ~/.local/bin/myclip ui
```

Reload sway `$mod+Shift+R`

> [!NOTE]
> Use your reload shortcut if customized.

## ⭐ Show Your Support

Give a ⭐ if this project helped you!

<p align="center">
  <b><i>Made with ❤️ for the Linux community</i></b>
</p>