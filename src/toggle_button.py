#!/usr/bin/env python3
"""
NStatus mode toggle button.

A GTK TOPLEVEL window (with GDK_BACKEND=x11) positioned over the Conky
widget's button area.  Uses CSS + a label for rendering so it works reliably
under XWayland where Cairo ARGB painting fails silently.

Clicking toggles the simple_mode flag file and immediately regenerates
conky_data.txt so the display updates within ~2 s.
"""
import os, subprocess
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

# Force XWayland backend so absolute screen positioning works.
os.environ.setdefault("GDK_BACKEND", "x11")

from pathlib import Path
FLAG  = Path.home() / ".local/share/nstatus/simple_mode"
REGEN = Path.home() / ".config/nstatus/scripts/regen_conky.sh"

# ── must match conky/nstatus.conf ──────────────────────────────────────── #
CONKY_GAP_X  = 20
CONKY_WIDTH  = 290
LINE_PX      = 14
MARGIN_PX    = 6
BTN_HEIGHT   = LINE_PX + 4

# ── colours ────────────────────────────────────────────────────────────── #
BG_CSS   = "rgba(8, 8, 16, 0.85)"
C_INDIGO = "#7986cb"
C_GREEN  = "#00e676"
C_DIM    = "#888888"

CSS = f"""
window {{
    background-color: {BG_CSS};
}}
label {{
    font-family: "DejaVu Sans Mono";
    font-size: 9pt;
    padding: 1px 0px 0px 0px;
    color: {C_DIM};
}}
""".encode()


def _conky_window_y() -> int | None:
    """Return the absolute Y of the top of the Conky window via xwininfo."""
    try:
        out = subprocess.check_output(
            ["xwininfo", "-name", "conky"],
            stderr=subprocess.DEVNULL, text=True
        )
        for line in out.splitlines():
            if "Absolute upper-left Y:" in line:
                return int(line.split(":")[1].strip())
    except Exception:
        pass
    return None


class ToggleButton(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()

        self.resize(CONKY_WIDTH, BTN_HEIGHT)

        self._label = Gtk.Label()
        self._label.set_use_markup(True)
        self._label.set_halign(Gtk.Align.START)
        self._label.set_xalign(0.0)
        self.add(self._label)

        self._simple = FLAG.exists()
        self._update_label()

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._click)

        self.show_all()

        GLib.idle_add(self._position)
        GLib.timeout_add(2000, self._sync)

    # ── positioning ────────────────────────────────────────────────────── #

    def _position(self):
        # X: right-align matching Conky
        display = Gdk.Display.get_default()
        monitor = None
        if display:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            geo = monitor.get_geometry()
            x = geo.x + geo.width - CONKY_GAP_X - CONKY_WIDTH
        else:
            x = 1918 - CONKY_GAP_X - CONKY_WIDTH

        # Y: read Conky's actual window top, then skip title box (3 lines + margin)
        conky_top = _conky_window_y()
        if conky_top is not None:
            y = conky_top + MARGIN_PX + 3 * LINE_PX
        else:
            # Fallback: use gap_y - 7 (observed offset on this system)
            y = 43 + MARGIN_PX + 3 * LINE_PX

        self.move(x, y)
        return False

    # ── rendering ──────────────────────────────────────────────────────── #

    def _update_label(self):
        if self._simple:
            markup = (
                f'<span foreground="{C_DIM}">  [○ Full]  </span>'
                f'<span foreground="{C_GREEN}">[● Simple]</span>'
            )
        else:
            markup = (
                f'<span foreground="{C_INDIGO}">  [● Full]  </span>'
                f'<span foreground="{C_DIM}">[○ Simple]</span>'
            )
        self._label.set_markup(markup)

    # ── interaction ────────────────────────────────────────────────────── #

    def _click(self, widget, event):
        if event.button != 1:
            return False
        self._simple = not self._simple
        if self._simple:
            FLAG.touch()
        elif FLAG.exists():
            FLAG.unlink()
        subprocess.Popen(["bash", str(REGEN)])
        self._update_label()
        return True

    def _sync(self):
        current = FLAG.exists()
        if current != self._simple:
            self._simple = current
            self._update_label()
        return True


if __name__ == "__main__":
    ToggleButton()
    Gtk.main()
