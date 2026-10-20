"""Small Canvas-based widget kit.

Why hand-drawn widgets: ttk's native Windows themes ignore colour styling,
so a stock button stays grey with a hard rectangle no matter what you set.
These draw their own rounded background, animate their fill between states,
and pick up palette changes at runtime.

Every widget owns a ``tk.Canvas`` sized to its content and implements
``repaint()``; changing the theme simply re-runs it.
"""

import tkinter as tk
import tkinter.font as tkfont

from . import theme


def _rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """Draw a rounded rectangle as a smoothed polygon.

    Canvas has no native rounded rectangle; a polygon with smooth=True and
    duplicated corner points gives clean, antialias-free corners at any size.
    """
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Animated:
    """Shared helper for widgets that ease between two colors."""

    def __init__(self):
        self._anim_id = None
        self._anim_step = 0
        self._anim_from = None
        self._anim_to = None

    def animate_to(self, target, steps=6, interval=16):
        current = getattr(self, "_current_color", None)
        if current is None or current == target:
            self._current_color = target
            self.repaint()
            return
        self._anim_from = current
        self._anim_to = target
        self._anim_step = 0
        self._steps = steps
        self._interval = interval
        # Cancel any in-flight animation so fast hovering cannot stack timers.
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:  # noqa: BLE001 - widget may be destroyed
                pass
            self._anim_id = None
        self._tick()

    def _tick(self):
        self._anim_step += 1
        t = theme.ease_out_cubic(self._anim_step / self._steps)
        self._current_color = theme.lerp_color(self._anim_from, self._anim_to, t)
        try:
            self.repaint()
        except tk.TclError:
            return
        if self._anim_step >= self._steps:
            self._current_color = self._anim_to
            self._anim_id = None
        else:
            self._anim_id = self.after(self._interval, self._tick)


class Button(Animated, tk.Canvas):
    """A filled rounded button with hover/press animation."""

    def __init__(self, parent, text, command, palette, fonts,
                 kind="primary", width=None, height=38, radius=theme.RADIUS_SM,
                 icon=None, font_key="button", padding=(16, 0)):
        tk.Canvas.__init__(self, parent, highlightthickness=0, bd=0,
                           height=height, cursor="hand2")
        Animated.__init__(self)
        self.command = command
        self.palette = palette
        self.fonts = fonts
        self.kind = kind
        self.text = text
        self.icon = icon
        self.radius = radius
        self.height = height
        self.padding = padding
        self.font_key = font_key
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._font = tkfont.Font(family=fonts[font_key][0],
                                 size=fonts[font_key][1],
                                 weight=fonts[font_key][2] if len(fonts[font_key]) > 2 else "normal")
        self._width = width or self._measure()
        self.configure(width=self._width, height=height, bg=palette["bg"])
        self._current_color = self._base_color()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.repaint()

    # -- geometry --

    def _measure(self):
        return self._font.measure(self.text) + self.padding[0] * 2

    def _base_color(self):
        p = self.palette
        if self.kind == "primary":
            return p["accent"]
        if self.kind == "danger":
            return theme.mix_alpha(p["danger"], p["surface"], 0.16)
        return p["surface_alt"]

    def _hover_color(self):
        p = self.palette
        if self.kind == "primary":
            return p["accent_hover"]
        if self.kind == "danger":
            return theme.mix_alpha(p["danger"], p["surface"], 0.30)
        return p["surface_hi"]

    def _text_color(self):
        p = self.palette
        if self.kind == "primary":
            return p["accent_text"] if self._enabled else p["text_faint"]
        if self.kind == "danger":
            return p["danger"] if self._enabled else p["text_faint"]
        return p["text"] if self._enabled else p["text_faint"]

    # -- interaction --

    def _on_enter(self, _e=None):
        if not self._enabled:
            return
        self._hover = True
        self.animate_to(self._hover_color())

    def _on_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self.animate_to(self._base_color())

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self.repaint()

    def _on_release(self, _e=None):
        was_pressed = self._pressed
        self._pressed = False
        self.animate_to(self._hover_color() if self._hover else self._base_color())
        if was_pressed and self._enabled and self.command:
            self.command()

    # -- state --

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "")
        if not enabled:
            self._hover = False
            self._pressed = False
        self._current_color = (self._hover_color() if (self._hover and enabled)
                               else self._base_color())
        self.repaint()

    def set_text(self, text):
        self.text = text
        self._width = max(self._width, self._measure())
        self.configure(width=self._width)
        self.repaint()

    def set_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["bg"])
        self._current_color = (self._hover_color() if self._hover
                               else self._base_color())
        self.repaint()

    def repaint(self):
        self.delete("all")
        w, h = self._width, self.height
        p = self.palette
        fill = self._current_color
        # A pressed button sits 1px lower for tactile feedback.
        offset = 1 if self._pressed else 0
        _rounded_rect(self, 0, offset, w, h - 1 + offset, self.radius,
                      fill=fill, outline="")
        label = f"{self.icon}  {self.text}" if self.icon else self.text
        self.create_text(w / 2, (h - 1) / 2 + offset, text=label,
                         fill=self._text_color(), font=self._font)


class IconButton(Animated, tk.Canvas):
    """A small square ghost button -- used for the theme toggle."""

    def __init__(self, parent, glyph, command, palette, size=32,
                 font_family="Segoe UI Symbol", font_size=12):
        tk.Canvas.__init__(self, parent, highlightthickness=0, bd=0,
                           width=size, height=size, cursor="hand2")
        Animated.__init__(self)
        self.glyph = glyph
        self.command = command
        self.palette = palette
        self.size = size
        self._glyph_font = tkfont.Font(family=font_family, size=font_size)
        self.configure(bg=palette["bg"])
        self._current_color = palette["bg"]
        self.bind("<Enter>", lambda _e: self.animate_to(self.palette["surface_alt"]))
        self.bind("<Leave>", lambda _e: self.animate_to(self.palette["bg"]))
        self.bind("<ButtonRelease-1>", lambda _e: self.command())
        self.repaint()

    def set_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["bg"])
        self._current_color = palette["bg"]
        self.repaint()

    def set_glyph(self, glyph):
        self.glyph = glyph
        self.repaint()

    def repaint(self):
        self.delete("all")
        s = self.size
        _rounded_rect(self, 1, 1, s - 1, s - 1, theme.RADIUS_SM,
                      fill=self._current_color, outline="")
        self.create_text(s / 2, s / 2, text=self.glyph,
                         fill=self.palette["text_dim"], font=self._glyph_font)


class Chip(Animated, tk.Canvas):
    """A selectable pill -- used for the quality row."""

    def __init__(self, parent, text, value, command, palette, fonts,
                 selected=False, height=28):
        pad = 14
        tk.Canvas.__init__(self, parent, highlightthickness=0, bd=0,
                           height=height, cursor="hand2")
        Animated.__init__(self)
        self.text = text
        self.value = value
        self.command = command
        self.palette = palette
        self.fonts = fonts
        self.height = height
        self._font = tkfont.Font(family=fonts["chip"][0], size=fonts["chip"][1],
                                 weight="bold")
        self._width = self._font.measure(text) + pad * 2
        self.configure(width=self._width, bg=palette["bg"])
        self._selected = selected
        self._hover = False
        self._current_color = self._rest_color()
        self.bind("<Enter>", lambda _e: self._enter())
        self.bind("<Leave>", lambda _e: self._leave())
        self.bind("<ButtonRelease-1>", lambda _e: self.command(self.value) if self.command else None)
        self.repaint()

    def _rest_color(self):
        p = self.palette
        if self._selected:
            return theme.mix_alpha(p["accent"], p["surface"], 0.22)
        return p["surface"] if self._hover else p["surface_alt"]

    def _enter(self):
        self._hover = True
        self.animate_to(self._rest_color(), steps=4)

    def _leave(self):
        self._hover = False
        self.animate_to(self._rest_color(), steps=4)

    def set_selected(self, selected, animate=True):
        if self._selected == selected:
            return
        self._selected = selected
        target = self._rest_color()
        if animate:
            self.animate_to(target, steps=5)
        else:
            self._current_color = target
            self.repaint()

    def set_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["bg"])
        self._current_color = self._rest_color()
        self.repaint()

    def repaint(self):
        self.delete("all")
        w, h = self._width, self.height
        p = self.palette
        outline = p["accent"] if self._selected else p["border"]
        _rounded_rect(self, 1, 1, w - 1, h - 1, h / 2,
                      fill=self._current_color, outline=outline, width=1)
        color = p["accent"] if self._selected else p["text_dim"]
        self.create_text(w / 2, h / 2, text=self.text, fill=color, font=self._font)

    def set_available(self, available):
        """None = unchecked, True = offered, False = not offered."""
        self._available = available
        self._current_color = self._rest_color()
        self.repaint()


class ProgressBar(tk.Canvas):
    """Smoothly animated rounded progress bar.

    ``set(value)`` eases toward the target so short download jumps look
    fluid rather than teleporting, and an indeterminate shimmer runs while a
    stage has no known percentage (merging, probing).
    """

    def __init__(self, parent, palette, height=8):
        tk.Canvas.__init__(self, parent, highlightthickness=0, bd=0,
                           height=height)
        self.palette = palette
        self.bar_height = height
        self._value = 0.0        # currently drawn
        self._target = 0.0       # where we are heading
        self._indeterminate = False
        self._shimmer = 0.0
        self._anim_id = None
        self.configure(bg=palette["bg"])
        self.bind("<Configure>", lambda _e: self.repaint())
        self.repaint()

    def set(self, value, immediate=False):
        self._target = max(0.0, min(100.0, float(value)))
        if immediate:
            self._value = self._target
            self.repaint()
            return
        self._ensure_anim()

    def set_indeterminate(self, active):
        self._indeterminate = bool(active)
        if active:
            self._ensure_anim()
        else:
            self._cancel_anim()
            self.repaint()

    def _ensure_anim(self):
        if self._anim_id is None:
            self._tick()

    def _cancel_anim(self):
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:  # noqa: BLE001
                pass
            self._anim_id = None

    def stop(self):
        """Stop animating -- call when the window closes."""
        self._cancel_anim()

    def _tick(self):
        try:
            if self._indeterminate:
                self._shimmer = (self._shimmer + 0.035) % 1.0
                self.repaint()
                self._anim_id = self.after(24, self._tick)
                return
            delta = self._target - self._value
            if abs(delta) < 0.4:
                self._value = self._target
                self._anim_id = None
                self.repaint()
                return
            self._value += delta * 0.28
            self.repaint()
            self._anim_id = self.after(16, self._tick)
        except tk.TclError:
            self._anim_id = None

    def set_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["bg"])
        self.repaint()

    def repaint(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.bar_height
        if w <= 1:
            return
        p = self.palette
        r = h / 2
        _rounded_rect(self, 0, 0, w, h, r, fill=p["track"], outline="")

        if self._indeterminate:
            seg = w * 0.32
            travel = w + seg
            x = self._shimmer * travel - seg
            x1, x2 = max(0, x), min(w, x + seg)
            if x2 > x1:
                _rounded_rect(self, x1, 0, x2, h, r,
                              fill=theme.mix_alpha(p["accent"], p["track"], 0.55),
                              outline="")
            return

        filled = w * (self._value / 100.0)
        if filled >= 1:
            r = min(r, filled / 2)
            _rounded_rect(self, 0, 0, filled, h, r, fill=p["accent"], outline="")


class AnimatedLabel(tk.Label):
    """A label whose text fades between colors when its state changes.

    Used for the status line so switching from "Downloading" to "Done"
    reads as an event rather than a silent text swap.
    """

    def __init__(self, parent, palette, fonts, font_key="body_bold", **kwargs):
        tk.Label.__init__(self, parent, bg=palette["bg"], fg=palette["text"],
                          font=fonts[font_key], anchor="w",
                          highlightthickness=0, bd=0, **kwargs)
        self.palette = palette
        self._fade_id = None
        self._current_fg = palette["text"]

    def flash(self, text, color_key="accent"):
        """Set text and pulse its color toward ``color_key`` then back."""
        self.configure(text=text)
        target = self.palette.get(color_key, self.palette["text"])
        self._fade_to(target, self.palette["text"], out=True)

    def _fade_to(self, color, back, out=False, step=0, steps=10):
        if self._fade_id is not None:
            try:
                self.after_cancel(self._fade_id)
            except Exception:  # noqa: BLE001
                pass
        start = self._current_fg
        t = theme.ease_in_out(step / steps)
        mid = theme.lerp_color(start, color, t)
        try:
            self.configure(fg=mid)
        except tk.TclError:
            return
        self._current_fg = mid
        if step < steps:
            self._fade_id = self.after(18, lambda: self._fade_to(
                color, back, out, step + 1, steps))
        else:
            self._fade_id = None
            if out:
                self.after(220, lambda: self._fade_to(
                    back, back, False, 0, steps))

    def set_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["bg"], fg=palette["text"])
        self._current_fg = palette["text"]


class NeonFrame(tk.Frame):
    """A quiet outer outline with brighter curved green corners. No timers."""
    def __init__(self, parent, palette):
        super().__init__(parent, bd=0, highlightthickness=0)
        self.edges = [tk.Canvas(self, bd=0, highlightthickness=0) for _ in range(4)]
        self._edge_keys = {}
        self.edges[0].place(x=24, y=0, relwidth=1, width=-48, height=5)
        self.edges[1].place(x=24, rely=1, y=-5, relwidth=1, width=-48, height=5)
        self.edges[2].place(x=0, y=24, relheight=1, height=-48, width=5)
        self.edges[3].place(relx=1, x=-5, y=24, relheight=1, height=-48, width=5)
        self.corners = []
        for right, bottom in ((False, False), (True, False), (False, True), (True, True)):
            corner = tk.Canvas(self, width=24, height=24, bd=0, highlightthickness=0)
            corner.place(relx=int(right), rely=int(bottom),
                         anchor=("s" if bottom else "n") + ("e" if right else "w"))
            self.corners.append((corner, right, bottom))
        self.set_palette(palette)
        for edge in self.edges:
            edge.bind("<Configure>", lambda event: self._paint_edge(event.widget))

    def _paint_edge(self, edge):
        w, h = edge.winfo_width(), edge.winfo_height()
        key = (w, h, self._edge_background, self._edge_neon)
        if self._edge_keys.get(edge) == key:
            return
        self._edge_keys[edge] = key
        edge.delete("all")
        points = (0, 2, w, 2) if edge in self.edges[:2] else (2, 0, 2, h)
        for width, opacity in ((4.2, 0.08), (2.8, 0.18), (1.4, 0.95)):
            edge.create_line(*points, width=width,
                fill=theme.mix_alpha(self._edge_neon, self._edge_background, opacity))

    def set_palette(self, palette):
        background = palette["bg"]
        neon = "#39ff9b" if background != theme.LIGHT["bg"] else "#087c4b"
        self.configure(bg=background)
        self._edge_background, self._edge_neon = background, neon
        for edge in self.edges:
            edge.configure(bg=background)
            self._paint_edge(edge)
        for corner, right, bottom in self.corners:
            corner.configure(bg=background)
            corner.delete("all")
            angle = {(False, False): 90, (True, False): 0,
                     (False, True): 180, (True, True): 270}[right, bottom]
            def point(x, y):
                return (24-x if right else x, 24-y if bottom else y)
            for width, opacity in ((5, 0.08), (3, 0.18), (1.4, 1.0)):
                color = theme.mix_alpha(neon, background, opacity)
                corner.create_arc(2, 2, 22, 22, start=angle, extent=90,
                                  style="arc", outline=color, width=width)
                corner.create_line(*point(12, 2), *point(24, 2), fill=color, width=width)
                corner.create_line(*point(2, 12), *point(2, 24), fill=color, width=width)
