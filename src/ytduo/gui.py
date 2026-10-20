"""Desktop front-end -- modern dark/light UI drawn on Canvas.

Why this looks the way it does: tkinter's ttk widgets delegate to Windows
Visual Styles and ignore colour, so a stock ttk window always looks like a
system dialog. Every interactive control here is drawn on a ``tk.Canvas``
(see ``ytduo.widgets``) which buys real colour, rounded corners, hover
easing and an animated progress bar.

The module is still a thin shell: it gathers a ``Settings``, runs
``engine.run_batch`` on a worker thread and renders the events. All policy
lives in the engine.

    python -m ytduo --gui
"""

import os
import sys
import subprocess
import threading
import tempfile
import webbrowser
from urllib.parse import urlsplit
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from . import __version__, engine, selector, theme, widgets, updates, desktop
from .cli import extract_urls
from .config import Settings, read_config_file, write_config
from .gui_worker import EventBridge, Worker

POLL_MS = 80

QUALITY_CHOICES = [
    ("Best", None),
    ("2160p", 2160),
    ("1440p", 1440),
    ("1080p", 1080),
    ("720p", 720),
    ("480p", 480),
    ("360p", 360),
]

MODE_CHOICES = [
    ("best", "Best quality", "Highest resolution available (MKV)"),
    ("efficient", "Smaller file", "Same resolution, AV1 where offered (MKV)"),
    ("compat", "Most compatible", "H.264 + AAC for any player (MP4)"),
]
MODE_HELP = {key: help_text for key, _label, help_text in MODE_CHOICES}
MODE_LABELS = [label for _key, label, _help in MODE_CHOICES]
LABEL_TO_MODE = {label: key for key, label, _help in MODE_CHOICES}
MODE_TO_LABEL = {key: label for key, label, _help in MODE_CHOICES}


class YtDuoApp:
    def __init__(self, root, background_checks=True):
        self.root = root
        self.bridge = EventBridge()
        self.worker = None
        self.current_output = None
        self.background_checks = background_checks
        self.service_bridge = EventBridge()
        self._destroyed = False
        self._preview_id = None
        self._preview_generation = 0
        self._preview_info = None
        self._preview_request = None
        self._preview_thread = None
        self._update_thread = None
        self._update_start_id = None
        self._update_result = None
        self._external_pending = set()
        self._layout_id = None
        self._auto_generation = None
        self._pause_requested = False
        self._redownload_request = None
        self._redownload_dialog = None
        self._redownload_timer = None
        self._scan_active = False
        self._scan_anim_id = None
        self._scan_angle = 0
        self.update_checker = updates.UpdateChecker(__version__)
        # Links pasted while a run is in flight wait here for the worker.
        self._pending_lock = threading.Lock()
        self._pending_links = []

        self._load_settings()
        self.palette = theme.PALETTES.get(self.saved.get("theme", "dark"),
                                          theme.PALETTES["dark"])
        self.font_scale = self.saved.get("font_scale", 100)
        if self.font_scale not in (90, 100, 110, 125):
            self.font_scale = 100
        self.fonts = theme.build_fonts(set(tkfont.families()), self.font_scale)

        self._build_ui()
        self.url_var.trace_add("write", lambda *_: self._schedule_preview())
        self.cookies_var.trace_add("write", lambda *_: self._schedule_preview())
        self.root.attributes("-topmost", bool(self.saved.get("always_on_top", False)))
        self.apply_font_scale(self.font_scale, save=False)
        self.toggle_log(True)
        self.root.bind("<Map>", self._on_window_map, add="+")
        self._schedule_layout()
        self._poll()
        if background_checks:
            def clean_startup():
                try:
                    engine.clear_recycle(Settings())
                except OSError as exc:
                    self.service_bridge.emit("cleanup_warning", {"message": str(exc)})
            threading.Thread(target=clean_startup, daemon=True).start()
            self._update_start_id = self.root.after(1000, lambda: self.check_updates(True))

    # ── settings ─────────────────────────────────────────────────────

    def _config_path(self):
        return Settings().config_file

    def _load_settings(self):
        self.saved = read_config_file(self._config_path())

    def _save_settings(self):
        data = dict(self.saved)
        data.update({
            "destination_layout": 1,
            "mode": self.mode_var.get(),
            "max_height": self._selected_height(),
            "output_dir": self.output_var.get(),
            "use_archive": bool(self.archive_var.get()),
            "cookies_from_browser": self.cookies_var.get().strip() or None,
            "theme": self.theme_name,
            "audio_format": self.audio_format_var.get(),
            "audio_bitrate": self.audio_bitrate_var.get() or None,
            "font_scale": self.font_scale,
            "always_on_top": bool(self.topmost_var.get()),
            "open_after_download": bool(self.open_after_var.get()),
        })
        try:
            write_config(self._config_path(), data)
        except OSError:
            pass

    @property
    def theme_name(self):
        for name, pal in theme.PALETTES.items():
            if pal is self.palette:
                return name
        return "dark"

    # ── construction ─────────────────────────────────────────────────

    def _build_ui(self):
        self.root.title(f"YT-Duo {__version__}")
        asset_dir = os.path.join(os.path.dirname(__file__), "assets")
        self.app_icon = tk.PhotoImage(master=self.root, file=os.path.join(asset_dir, "icon.png"))
        self.header_icon = self.app_icon.subsample(4, 4)
        self.root.iconphoto(True, self.app_icon)
        self.root.geometry("640x280")
        self.root.minsize(600, 260)
        self.root.configure(bg=self.palette["bg"])

        self.window_edge = widgets.NeonFrame(self.root, self.palette)
        self.window_edge.pack(fill="both", expand=True)
        self.shell = tk.Frame(self.window_edge, bg=self.palette["bg"])
        self.shell.pack(fill="both", expand=True, padx=3, pady=3)
        for corner, _right, _bottom in self.window_edge.corners:
            tk.Misc.tkraise(corner)

        self.settings_visible = False
        self._log_visible = False
        self._progress_visible = False
        self._actions_visible = False

        self.chrome = desktop.WindowChrome(self.root) if sys.platform == "win32" else None
        self._build_header()
        self._build_input()
        self._build_quality()
        self._build_destination()
        self._build_progress()
        self._build_advanced()
        self._build_actions()
        self._build_footer()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._set_progress_visible(False)
        self._update_queue_hint()
        self.url_entry.focus_set()

    def _pack_order(self):
        """Section order, top to bottom, excluding anything not yet shown.

        pack() appends, so a panel revealed later would otherwise land below
        the activity log. Every show/hide re-packs against this order.
        """
        return [
            self.header_row,
            self.input_row,
            self.media_row,
            self.quality_row,
            self.format_row,
            self.music_row,
            self.audio_quality_row,
            self.destination_row,
            self.progress_frame,
            self.advanced,
            self.terminal_heading,
            self.log_frame,
            self._actions_row,
            self.footer_row,
        ]

    def _repack(self):
        """Re-lay the shell in canonical order, honouring visible sections.

        Visibility is decided by state flags rather than by what happens to
        be managed, so re-packing is idempotent and always correct.
        """
        wanted = {
            self.header_row: True,
            self.input_row: True,
            self.media_row: True,
            self.quality_row: self.media_var.get() == "video",
            self.format_row: self.media_var.get() == "video",
            self.music_row: self.media_var.get() == "audio",
            self.audio_quality_row: self.media_var.get() == "audio",
            self.footer_row: True,
            self.destination_row: True,
            self.progress_frame: getattr(self, "_progress_visible", False),
            self.advanced: getattr(self, "settings_visible", False),
            self.terminal_heading: True,
            self.log_frame: getattr(self, "_log_visible", False),
            self._actions_row: getattr(self, "_actions_visible", False),
        }
        ordered = [w for w in self._pack_order() if wanted.get(w)]
        for widget in self._pack_order():
            widget.pack_forget()
        for widget in ordered:
            self._pack_section(widget)

    def _pack_section(self, widget):
        compact = self.settings_visible
        if widget is self.header_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        elif widget is self.input_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(8 if compact else 16, 0))
        elif widget in (self.media_row, self.music_row, self.audio_quality_row):
            widget.pack(fill="x", padx=theme.PAD, pady=(6 if compact else 10, 0))
        elif widget is self.footer_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(8, 8) if compact else (14, 14))
        elif widget is self.quality_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(8 if compact else 14, 0))
        elif widget is self.format_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(6 if compact else 10, 0))
        elif widget is self.destination_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(8 if compact else 12, 0))
        elif widget is self.progress_frame:
            widget.pack(fill="x", padx=theme.PAD)
        elif widget is self.advanced:
            widget.pack(fill="x", padx=theme.PAD, pady=(8 if compact else 16, 0))
        elif widget is self.log_frame:
            widget.pack(fill="x", padx=theme.PAD, pady=(6, 0))
        elif widget is self.terminal_heading:
            widget.pack(fill="x", padx=theme.PAD, pady=(8 if compact else 14, 0))
        elif widget is self._actions_row:
            widget.pack(fill="x", padx=theme.PAD, pady=(8, 12) if compact else (14, theme.PAD))

    def _label(self, parent, text, font_key="body", color_key="text", bg_key=None):
        label = tk.Label(
            parent, text=text, bg=self.palette[bg_key or "bg"],
            fg=self.palette[color_key], font=self.fonts[font_key],
            anchor="w", highlightthickness=0, bd=0)
        label._font_key = font_key
        return label

    def _on_media(self, value):
        if self.worker and self.worker.is_alive():
            self.append_log("Finish or cancel this batch before changing Video / Music.", "muted")
            return
        self.media_var.set(value)
        self._refresh_destination()
        for chip in self.media_chips:
            chip.set_selected(chip.value == value)
        self._refresh_preview_feedback()
        self._repack()
        self._autosize()

    def _on_audio_format(self, value):
        if self.worker and self.worker.is_alive():
            return
        self.audio_format_var.set(value)
        for chip in self.audio_chips:
            chip.set_selected(chip.value == value)
        if value == "original":
            self.audio_bitrate_var.set(0)
            for chip in self.audio_quality_chips:
                chip.set_selected(chip.value is None)
        self._refresh_preview_feedback()

    def _on_audio_quality(self, bitrate):
        if self.worker and self.worker.is_alive():
            return
        if bitrate:
            self._on_audio_format("mp3")
        self.audio_bitrate_var.set(bitrate or 0)
        for chip in self.audio_quality_chips:
            chip.set_selected(chip.value == bitrate)
        self._refresh_preview_feedback()

    def _schedule_preview(self):
        """Debounce edits and invalidate old results immediately."""
        if self._destroyed:
            return
        self._preview_generation += 1
        generation = self._preview_generation
        self._preview_info = None
        self._preview_request = None
        if self._preview_id:
            self.root.after_cancel(self._preview_id)
            self._preview_id = None
        for chip in self.chips:
            chip.set_available(None)
        urls = extract_urls(self.url_var.get())
        valid = False
        if len(urls) == 1:
            try:
                parsed = urlsplit(urls[0])
                valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
            except ValueError:
                pass
        if not valid:
            self._set_link_status("Invalid link" if self.url_var.get().strip() else "Paste a link",
                                  "danger" if self.url_var.get().strip() else "text_dim")
            return
        self._set_link_status("Scanning…", "text_dim")
        if self.background_checks:
            settings = self._make_settings().replace(socket_timeout=12, download_attempts=1)
            self._preview_id = self.root.after(300,
                lambda: self._queue_preview(generation, urls[0], settings))

    def _queue_preview(self, generation, url, settings):
        self._preview_id = None
        if self._destroyed or generation != self._preview_generation:
            return
        self._preview_request = (generation, url, settings)
        self._drain_preview()

    def _drain_preview(self):
        if self._destroyed or self._preview_thread is not None or not self._preview_request:
            return
        generation, url, settings = self._preview_request
        self._preview_request = None
        bridge = self.service_bridge

        def work():
            result = {"generation": generation, "url": url}
            try:
                if not engine.is_host_supported(url):
                    raise engine.UnsupportedHostError("no extractor for this host")
                result["info"] = engine.probe(url, settings)
            except Exception as exc:
                result["error"] = engine.classify_error(exc)
            bridge.emit("preview_result", result)
            bridge.emit("preview_finished", {})

        self._preview_thread = threading.Thread(target=work, daemon=True)
        self._preview_thread.start()

    def _on_preview_result(self, result):
        if self._destroyed or result["generation"] != self._preview_generation:
            return
        if "error" in result:
            # A failed network probe is not proof that a URL is invalid.
            error = result["error"].lower()
            invalid = any(word in error for word in ("no extractor", "unsupported", "invalid", "not available", "unavailable video", "video unavailable", "removed", "private video"))
            self._set_link_status("Invalid / unavailable" if invalid else "Could not verify",
                                  "danger" if invalid else "text_dim")
            return
        self._preview_info = result["info"]
        self._refresh_preview_feedback()
        self._autosize()
        self._maybe_auto_download()

    def _maybe_auto_download(self):
        if (self._destroyed or not self.auto_download_var.get()
                or self._preview_info is None or self._link_status_color != "success"
                or self._auto_generation == self._preview_generation):
            return
        # Consume before starting: re-entrant Tk updates and repeated service
        # events must never start the same paste twice.
        self._auto_generation = self._preview_generation
        self.download_single()

    def _on_preview_finished(self, _result):
        self._preview_thread = None
        self._drain_preview()

    def _on_cleanup_warning(self, result):
        self.append_log("Recycle cleanup: " + result["message"], "muted")

    def _set_scan_busy(self, busy):
        if self._scan_active == busy:
            return
        self._scan_active = busy
        if self._scan_anim_id:
            self.root.after_cancel(self._scan_anim_id)
            self._scan_anim_id = None
        if busy:
            self._scan_tick()

    def _scan_tick(self):
        self._scan_anim_id = None
        if self._destroyed or not self._scan_active:
            return
        self._scan_angle = (self._scan_angle + 35) % 360
        self._paint_input()
        self._scan_anim_id = self.root.after(80, self._scan_tick)

    def _set_link_status(self, text, color):
        self._set_scan_busy(text == "Scanning…")
        self._link_status_text, self._link_status_color = text, color
        self.quality_feedback.configure(text=text, fg=self.palette[color])
        self._sync_placeholder()
        self._paint_input()

    def _refresh_preview_feedback(self):
        if not hasattr(self, "quality_feedback"):
            return
        if self._preview_info is None:
            self._set_link_status(self._link_status_text, self._link_status_color)
            return
        formats = self._preview_info.get("formats") or []
        try:
            if self.media_var.get() == "audio":
                selector.pick_music(formats, "best")
            else:
                heights = selector.available_heights(formats, self.mode_var.get())
                for chip in self.chips:
                    # Best is automatic, not a particular offered resolution.
                    chip.set_available(None if chip.value is None else chip.value in heights)
                selector.pick_streams(formats, self.mode_var.get(), self._selected_height())
            self._set_link_status("Ready to download", "success")
        except (selector.NoSuitableFormat, ValueError):
            self._set_link_status("No playable media", "danger")

    def _build_footer(self):
        self.footer_row = tk.Frame(self.shell, bg=self.palette["bg"])
        self.update_status = self._label(self.footer_row, "v" + __version__, "small", "text_dim")
        self.update_status.pack(side="left")
        self.star_button = widgets.Button(self.footer_row, "★ Star on GitHub",
            lambda: self._open_project_url(updates.REPO_URL), self.palette, self.fonts,
            kind="ghost", height=26, font_key="small", padding=(10, 0))
        self.star_button.pack(side="right")
        self.update_button = widgets.Button(self.footer_row, "Check updates",
            self.check_updates, self.palette, self.fonts,
            kind="ghost", height=26, font_key="small", padding=(10, 0))
        self.update_button.pack(side="right", padx=(0, 6))
        self.release_button = widgets.Button(self.footer_row, "View release ↗",
            lambda: self._open_project_url(updates.RELEASE_URL), self.palette, self.fonts,
            kind="ghost", height=26, font_key="small", padding=(10, 0))

    def _open_project_url(self, url):
        self._external_action("url", url, lambda: desktop.open_url(url))

    def _external_action(self, kind, target, action):
        if self._destroyed or kind in self._external_pending:
            return
        self._external_pending.add(kind)
        bridge = self.service_bridge
        def work():
            result = {"kind": kind, "target": target}
            try:
                action()
            except Exception as exc:
                result["error"] = str(exc)
            bridge.emit("external_result", result)
        threading.Thread(target=work, daemon=True).start()

    def _on_external_result(self, result):
        self._external_pending.discard(result["kind"])
        if self._destroyed:
            return
        if result.get("error"):
            title = "Could not open the save folder." if result["kind"] == "folder" else "Could not open your browser."
            self.append_log(title + " " + result["error"], "error")
            messagebox.showerror("YT-Duo", title + "\n" + result["target"] + "\n\n" + result["error"], parent=self.root)

    def check_updates(self, force=True):
        if self._destroyed or (self._update_thread and self._update_thread.is_alive()):
            return
        self.release_button.pack_forget()
        self.update_status.configure(text="Checking updates…", fg=self.palette["text_dim"])
        self.update_button.set_enabled(False)
        bridge = self.service_bridge

        def work():
            try:
                result = self.update_checker.check(force)
            except Exception:
                result = {"status": "unavailable"}
            bridge.emit("update_result", result)

        self._update_thread = threading.Thread(target=work, daemon=True)
        self._update_thread.start()

    def _on_update_result(self, result):
        if self._destroyed:
            return
        self._update_result = result
        self._update_thread = None
        self.update_button.set_enabled(True)
        self.release_button.pack_forget()
        status = result.get("status")
        if status == "available":
            text = "Update " + result["latest"] + " available"
            self.release_button.pack(side="right", padx=(0, 6))
        else:
            text = {"current": "Up to date", "unpublished": "No release published"}.get(
                status, "Update check unavailable")
        self.update_status.configure(text="v" + __version__ + " · " + text,
            fg=self.palette["success"] if status == "available" else self.palette["text_dim"])
        self._autosize()

    def _build_header(self):
        row = tk.Frame(self.shell, bg=self.palette["bg"])
        self.header_row = row
        row.pack(fill="x", padx=theme.PAD, pady=(8, 0))

        tools = tk.Frame(row, bg=self.palette["bg"])
        tools.pack(fill="x", pady=(0, 6))
        self.brand_icon = tk.Label(tools, image=self.header_icon,
                                   bg=self.palette["bg"], borderwidth=0)
        self.brand_icon.pack(side="left", padx=(0, 7))
        self.title_label = self._label(tools, "YT-Duo", "hero")
        self.title_label.pack(side="left")
        self.close_button = self.minimize_button = None
        if self.chrome:
            self.close_button = widgets.Button(tools, "×", self.on_close,
                self.palette, self.fonts, kind="danger", width=34, height=30,
                font_key="button", padding=(0, 0))
            self.close_button.pack(side="right", padx=(6, 0))
            self.minimize_button = widgets.Button(tools, "−", self.chrome.minimize,
                self.palette, self.fonts, kind="ghost", width=34, height=30, padding=(0, 0))
            self.minimize_button.pack(side="right", padx=(6, 0))
        self.theme_button = widgets.Button(tools, "Themes ▾", self.show_themes,
            self.palette, self.fonts, kind="ghost", height=30, padding=(10, 0))
        self.theme_button.pack(side="right", padx=(6, 0))
        self.settings_button = widgets.Button(tools, "Options ▾", self.toggle_settings,
            self.palette, self.fonts, kind="ghost", height=30, padding=(10, 0))
        self.settings_button.pack(side="right", padx=(12, 0))
        self.subtitle = self._label(row,
            "Download YouTube videos in the best available quality.", "subtitle", "text_dim")
        self.subtitle.pack(anchor="w")
        if self.chrome:
            for surface in (row, tools, self.title_label, self.subtitle):
                self.chrome.bind_drag(surface)

    def show_themes(self):
        if getattr(self, "theme_menu", None):
            self.theme_menu.destroy()
        menu = tk.Menu(self.root, tearoff=False, bg=self.palette["surface"],
                       fg=self.palette["text"], activebackground=self.palette["accent"],
                       activeforeground=self.palette["accent_text"])
        self.theme_menu = menu
        for name, label in theme.THEME_LABELS.items():
            selected = "✓ " if name == self.theme_name else "   "
            menu.add_command(label=selected+label,
                             command=lambda value=name: self.select_theme(value))
        try:
            menu.tk_popup(self.theme_button.winfo_rootx(),
                          self.theme_button.winfo_rooty()+self.theme_button.winfo_height())
        finally:
            menu.grab_release()

    def select_theme(self, name):
        self.apply_theme(name)
        self._save_settings()

    def _build_input(self):
        row = tk.Frame(self.shell, bg=self.palette["bg"])
        self.input_row = row
        row.pack(fill="x", padx=theme.PAD, pady=(16, 0))

        # The entry sits inside a rounded Canvas so it matches the buttons.
        self.input_canvas = tk.Canvas(
            row, height=44, highlightthickness=0, bd=0,
            bg=self.palette["bg"])
        row.grid_columnconfigure(0, weight=1)
        self.input_canvas.grid(row=0, column=0, sticky="ew")

        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(
            self.input_canvas, textvariable=self.url_var, bd=0,
            highlightthickness=0, bg=self.palette["surface_alt"],
            fg=self.palette["text"], insertbackground=self.palette["text"],
            font=self.fonts["entry"])
        self._entry_window = self.input_canvas.create_window(
            34, 22, window=self.url_entry, anchor="w", height=26)
        self.placeholder = self._label(self.input_canvas, "Paste a YouTube link…",
                                       "entry", "text_faint", "surface_alt")
        self.placeholder.bind("<Button-1>", lambda _event: self.url_entry.focus_set())
        self._placeholder_window = self.input_canvas.create_window(
            34, 22, window=self.placeholder, anchor="w", height=26)
        self.input_canvas.bind("<Configure>", self._layout_input)
        self.url_entry.bind("<Return>", lambda _e: self.download_single())
        self.url_entry.bind("<KP_Enter>", lambda _e: self.download_single())
        self.url_entry.bind("<FocusIn>", lambda _e: self._paint_input(True))
        self.url_entry.bind("<FocusOut>", lambda _e: self._paint_input(False))
        self._input_focused = False

        self.single_button = widgets.Button(
            row, "Download", self.download_single, self.palette, self.fonts,
            height=44, radius=theme.RADIUS)
        self.single_button.grid(row=0, column=1, padx=(10, 0))
        self.add_button = widgets.Button(
            row, "+ Queue", self.add_to_queue, self.palette, self.fonts,
            kind="ghost", height=44, radius=theme.RADIUS)
        self.add_button.grid(row=0, column=2, padx=(8, 0))
        self._link_status_text = "Paste a link"
        self._link_status_color = "text_dim"
        self.quality_feedback = self._label(row, "Paste a link", "small", "text_dim")
        # Status is represented by the leading indicator; this label retains
        # descriptive state for diagnostics without a duplicate row below.

    def _sync_placeholder(self):
        self.input_canvas.itemconfigure(self._placeholder_window,
            state="hidden" if self.url_var.get() else "normal")

    def _layout_input(self, event=None):
        w = self.input_canvas.winfo_width()
        h = int(self.input_canvas.cget("height"))
        for item in (self._entry_window, self._placeholder_window):
            self.input_canvas.coords(item, 34, h / 2)
            self.input_canvas.itemconfigure(item, width=max(40, w - 50),
                height=max(26, tkfont.Font(root=self.root, font=self.fonts["entry"]).metrics("linespace") + 2))
        self._paint_input(self._input_focused)

    def _paint_input(self, focused=None):
        if focused is not None:
            self._input_focused = focused
        c = self.input_canvas
        c.delete("bg")
        c.delete("indicator")
        w = c.winfo_width() or 400
        h = int(c.cget("height"))
        border = self.palette["accent"] if self._input_focused else self.palette["border"]
        widgets._rounded_rect(c, 1, 1, w - 1, h - 1, theme.RADIUS,
                              fill=self.palette["surface_alt"], outline=border,
                              width=1, tags="bg")
        c.tag_lower("bg")
        if self._scan_active:
            c.create_arc(8, h/2-9, 26, h/2+9, start=self._scan_angle, extent=110,
                style="arc", outline=self.palette["accent"], width=2, tags="indicator")
            return
        color = self.palette[getattr(self, "_link_status_color", "text_faint")]
        widgets._rounded_rect(c, 13, h/2-8, 19, h/2+8, 3,
                              fill=color, outline="", tags="indicator")

    def _build_quality(self):
        self.media_var = tk.StringVar(value="video")
        self.audio_format_var = tk.StringVar(value=self.saved.get("audio_format", "original"))
        if self.audio_format_var.get() not in ("original", "mp3"):
            self.audio_format_var.set("original")
        bitrate = self.saved.get("audio_bitrate")
        if bitrate not in (128, 192, 256, 320) or self.audio_format_var.get() != "mp3":
            bitrate = None
        self.audio_bitrate_var = tk.IntVar(value=bitrate or 0)
        self.media_row = tk.Frame(self.shell, bg=self.palette["bg"])
        self._label(self.media_row, "Download", "small", "text_faint").pack(side="left", padx=(2, 10))
        self.media_chips = []
        for label, value in (("Video", "video"), ("Music", "audio")):
            chip = widgets.Chip(self.media_row, label, value, self._on_media,
                                self.palette, self.fonts, selected=value == "video")
            chip.pack(side="left", padx=(0, 6))
            self.media_chips.append(chip)
        # Deliberately off on every launch; only a checked box authorizes
        # automatic downloads for this session.
        self.auto_download_var = tk.BooleanVar(value=False)
        self.auto_download_check = tk.Checkbutton(self.media_row, text="Auto download",
            variable=self.auto_download_var, command=self._maybe_auto_download,
            bg=self.palette["bg"], fg=self.palette["text"],
            activebackground=self.palette["bg"], activeforeground=self.palette["text"],
            selectcolor=self.palette["surface_alt"], font=self.fonts["small"],
            bd=0, highlightthickness=0, cursor="hand2")
        self.auto_download_check.pack(side="left", padx=(14, 0))
        self.music_row = tk.Frame(self.shell, bg=self.palette["bg"])
        self._label(self.music_row, "Audio", "small", "text_faint").pack(side="left", padx=(2, 10))
        self.audio_chips = []
        for label, value in (("Original quality", "original"), ("MP3", "mp3")):
            chip = widgets.Chip(self.music_row, label, value, self._on_audio_format,
                                self.palette, self.fonts,
                                selected=value == self.audio_format_var.get())
            chip.pack(side="left", padx=(0, 6))
            self.audio_chips.append(chip)
        self.audio_quality_row = tk.Frame(self.shell, bg=self.palette["bg"])
        self._label(self.audio_quality_row, "Music quality", "small", "text_faint").pack(side="left", padx=(2, 10))
        self.audio_quality_chips = []
        for label, value in (("Best", None), ("320k", 320), ("256k", 256), ("192k", 192), ("128k", 128)):
            chip = widgets.Chip(self.audio_quality_row, label, value, self._on_audio_quality,
                                self.palette, self.fonts, selected=value == bitrate)
            chip.pack(side="left", padx=(0, 6))
            self.audio_quality_chips.append(chip)
        self._label(self.music_row, "Bitrate choices convert to MP3.", "small", "text_faint").pack(side="left", padx=(10, 0))
        row = tk.Frame(self.shell, bg=self.palette["bg"])
        row.pack(fill="x", padx=theme.PAD, pady=(14, 0))
        self.quality_row = row

        self._label(row, "Quality", "small", "text_faint").pack(side="left",
                                                                padx=(2, 10))
        self.chips = []
        saved_height = self.saved.get("max_height")
        for label, value in QUALITY_CHOICES:
            chip = widgets.Chip(
                row, label, value, self._on_quality, self.palette, self.fonts,
                selected=(value == saved_height))
            chip.pack(side="left", padx=(0, 6))
            self.chips.append(chip)

        fmt_row = tk.Frame(self.shell, bg=self.palette["bg"])
        fmt_row.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        self.format_row = fmt_row

        self._label(fmt_row, "Format", "small", "text_faint").pack(side="left",
                                                                   padx=(2, 12))
        saved_mode = self.saved.get("mode", "best")
        self.mode_var = tk.StringVar(value=saved_mode)
        # Keep the help text truthful even when something sets mode_var
        # directly (config load, or a test).
        self.mode_var.trace_add("write", lambda *_: self._update_mode_help())
        self.mode_chips = []
        for key, label, _help in MODE_CHOICES:
            chip = widgets.Chip(
                fmt_row, label, key, self._on_mode, self.palette, self.fonts,
                selected=(key == saved_mode), height=26)
            chip.pack(side="left", padx=(0, 6))
            self.mode_chips.append(chip)

        self.mode_help = self._label(fmt_row, "", "small", "text_faint")
        self.mode_help.pack(side="left", padx=(6, 0))
        self._update_mode_help()

    def _build_destination(self):
        """Where files land, always visible -- not buried in Options."""
        defaults = Settings()
        destination = self.saved.get("output_dir") or defaults.output_dir
        legacy_default = os.path.join(defaults.root, "downloads")
        if not self.saved.get("destination_layout") and os.path.normcase(os.path.abspath(destination)) == os.path.normcase(legacy_default):
            destination = defaults.output_dir
        self.output_var = tk.StringVar(value=destination)

        row = tk.Frame(self.shell, bg=self.palette["bg"])
        self.destination_row = row

        self._label(row, "Save to", "small", "text_faint").pack(side="left",
                                                                padx=(2, 12))

        self.dest_label = self._label(row, "", "small", "text_dim")
        self.dest_label.pack(side="left", fill="x", expand=True)
        self.dest_browse = widgets.Button(
            row, "Browse", self.choose_output, self.palette, self.fonts,
            kind="ghost", height=26, padding=(10, 0))
        self.dest_browse.pack(side="right", padx=(6, 0))

        self.dest_open = widgets.Button(
            row, "Open", self.open_output, self.palette, self.fonts,
            kind="ghost", height=22, radius=6)
        self.dest_open.pack(side="right")
        self.output_var.trace_add("write", lambda *_: self._refresh_destination())
        self._refresh_destination()

    def _refresh_destination(self):
        """Show the folder name, with the full path as a tooltip."""
        path = self.output_var.get() if hasattr(self, "output_var") else ""
        if self.media_var.get() == "audio":
            path = os.path.join(path, "Audio")
        shown = self._short_path(path)
        self.dest_label.configure(text=shown)
        self.dest_label._full_path = path

    @staticmethod
    def _short_path(path, keep=44):
        """Middle-truncate a long path so it fits without widening the UI."""
        path = path or ""
        if len(path) <= keep:
            return path
        head = path[: keep // 2 - 2]
        tail = path[-(keep // 2 - 1):]
        return f"{head}…{tail}"

    def _build_progress(self):
        self.progress_frame = tk.Frame(self.shell, bg=self.palette["bg"])

        top = tk.Frame(self.progress_frame, bg=self.palette["bg"])
        top.pack(fill="x", pady=(8, 6))
        self.status_label = widgets.AnimatedLabel(
            top, self.palette, self.fonts, "body_bold")
        self.status_label.configure(text="Ready.")
        self.status_label.pack(side="left")
        self.detail_label = self._label(top, "", "small", "text_dim")
        self.detail_label.pack(side="right")

        self.progress_line = tk.Frame(self.progress_frame, bg=self.palette["bg"])
        self.progress_line.pack(fill="x")
        self.run_controls = tk.Frame(self.progress_line, bg=self.palette["bg"])
        self.run_controls.pack(side="right", padx=(8, 0))
        self.progress = widgets.ProgressBar(self.progress_line, self.palette, height=8)
        self.progress.pack(side="left", fill="x", expand=True)

    def _build_advanced(self):
        self.advanced = tk.Frame(self.shell, bg=self.palette["bg"])
        self.options_heading = self._section_heading(self.advanced, "Options")
        self.options_heading.pack(fill="x")

        card = tk.Frame(self.advanced, bg=self.palette["surface"],
                        highlightbackground=self.palette["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(6, 0))
        self.advanced_card = card

        inner = tk.Frame(card, bg=self.palette["surface"])
        inner.pack(fill="x", padx=16, pady=6)
        inner.columnconfigure(1, weight=1)

        self._label(inner, "Text size", "small", "text_dim", "surface").grid(row=0, column=0, sticky="w")
        preferences = tk.Frame(inner, bg=self.palette["surface"])
        preferences.grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.font_scale_var = tk.StringVar(value=f"{self.font_scale}%")
        self.scale_menu = tk.OptionMenu(preferences, self.font_scale_var,
            "90%", "100%", "110%", "125%",
            command=lambda value: self.apply_font_scale(int(value.rstrip("%"))))
        self.scale_menu.pack(side="left")
        self.topmost_var = tk.BooleanVar(value=bool(self.saved.get("always_on_top", False)))
        self.open_after_var = tk.BooleanVar(value=bool(self.saved.get("open_after_download", False)))
        self.preference_checks = []
        preference_bar = tk.Frame(inner, bg=self.palette["surface"])
        preference_bar.grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 0))
        for text, variable, command in (
            ("Always on top", self.topmost_var, self._toggle_topmost),
            ("Open folder after downloads", self.open_after_var, self._save_settings)):
            check = tk.Checkbutton(preference_bar, text=text, variable=variable, command=command,
                bg=self.palette["surface"], fg=self.palette["text"],
                activebackground=self.palette["surface"], activeforeground=self.palette["text"],
                selectcolor=self.palette["surface_alt"], font=self.fonts["small"], bd=0)
            check.pack(side="left", padx=(0, 12))
            self.preference_checks.append(check)

        self._label(inner, "Cookies", "small", "text_dim", "surface").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        cookie_row = tk.Frame(inner, bg=self.palette["surface"])
        cookie_row.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=(6, 0))
        self.cookies_var = tk.StringVar(
            value=self.saved.get("cookies_from_browser") or "")
        browser_values = ["", "chrome", "firefox", "edge", "brave", "opera"]
        self.cookies_combo = tk.OptionMenu(
            cookie_row, self.cookies_var, *browser_values)
        self.cookies_combo.configure(
            bg=self.palette["surface_alt"], fg=self.palette["text"],
            activebackground=self.palette["surface_hi"],
            activeforeground=self.palette["text"],
            highlightthickness=0, bd=0, font=self.fonts["small"],
            width=10, anchor="w")
        self.cookies_combo["menu"].configure(
            bg=self.palette["surface_alt"], fg=self.palette["text"],
            activebackground=self.palette["accent"],
            activeforeground=self.palette["accent_text"],
            font=self.fonts["small"], bd=0)
        self.cookies_combo.pack(side="left")
        self._label(cookie_row, "only needed for age-gated videos",
                    "small", "text_faint", "surface").pack(side="left",
                                                          padx=(10, 0))

        self.archive_var = tk.IntVar(
            value=1 if self.saved.get("use_archive", True) else 0)
        self.archive_check = tk.Checkbutton(
            inner, text="Skip videos already downloaded",
            variable=self.archive_var, bg=self.palette["surface"],
            fg=self.palette["text"], activebackground=self.palette["surface"],
            activeforeground=self.palette["text"],
            selectcolor=self.palette["surface_alt"],
            highlightthickness=0, bd=0, font=self.fonts["small"],
            anchor="w")
        self.archive_check.grid(row=2, column=0, columnspan=2, sticky="w",
                                pady=(6, 0))

        self._label(inner, "Queue", "small", "text_dim", "surface").grid(
            row=3, column=0, sticky="nw", pady=(4, 0))
        queue_row = tk.Frame(inner, bg=self.palette["surface"])
        queue_row.grid(row=3, column=1, sticky="ew", padx=(14, 0), pady=(4, 0))
        widgets.Button(queue_row, "Add link", self.add_to_queue, self.palette,
                       self.fonts, kind="ghost", height=30).pack(side="left")
        widgets.Button(queue_row, "Paste list", self.paste_list, self.palette,
                       self.fonts, kind="ghost", height=30).pack(
            side="left", padx=(6, 0))
        widgets.Button(queue_row, "Load links.txt", self.load_links_file,
                       self.palette, self.fonts, kind="ghost",
                       height=30).pack(side="left", padx=(6, 0))

        self.queue_frame = tk.Frame(inner, bg=self.palette["surface"])
        self.queue_frame.grid(row=4, column=0, columnspan=2, sticky="ew",
                              pady=(6, 0))
        holder = tk.Frame(self.queue_frame, bg=self.palette["surface"])
        holder.pack(fill="x")
        self.queue_list = tk.Listbox(
            holder, height=2, activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=self.palette["border"],
            bg=self.palette["surface_alt"], fg=self.palette["text"],
            selectbackground=self.palette["accent"],
            selectforeground=self.palette["accent_text"],
            font=self.fonts["small"])
        self.queue_list.pack(side="left", fill="both", expand=True)
        self.queue_scroll = tk.Scrollbar(
            holder, command=self.queue_list.yview, bd=0, width=12,
            troughcolor=self.palette["surface"],
            bg=self.palette["surface_hi"],
            activebackground=self.palette["border_hi"],
            highlightthickness=0, relief="flat")
        self.queue_scroll.pack(side="left", fill="y")
        self.queue_list.configure(yscrollcommand=self.queue_scroll.set)

        qbuttons = tk.Frame(self.queue_frame, bg=self.palette["surface"])
        qbuttons.pack(fill="x", pady=(4, 0))
        widgets.Button(qbuttons, "Remove selected", self.remove_selected,
                       self.palette, self.fonts, kind="ghost",
                       height=30).pack(side="left")
        widgets.Button(qbuttons, "Clear", self.clear_queue, self.palette,
                       self.fonts, kind="ghost", height=30).pack(
            side="left", padx=(6, 0))
        self.queue_count = self._label(self.queue_frame, "", "small",
                                       "text_faint", "surface")

        self.settings_visible = False

    def _toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost_var.get()))
        self._save_settings()

    def apply_font_scale(self, scale, save=True):
        self.font_scale = scale if scale in (90, 100, 110, 125) else 100
        self.fonts = theme.build_fonts(set(tkfont.families()), self.font_scale)
        self.font_scale_var.set(f"{self.font_scale}%")
        def walk(parent):
            for child in parent.winfo_children():
                if hasattr(child, "set_fonts"):
                    child.set_fonts(self.fonts)
                elif hasattr(child, "_font_key"):
                    child.configure(font=self.fonts[child._font_key])
                walk(child)
        walk(self.root)
        for widget, key in ((self.url_entry, "entry"), (self.queue_list, "small"),
                            (self.log, "mono"), (self.status_label, "body_bold"),
                            (self.archive_check, "small"), (self.cookies_combo, "small"),
                            (self.auto_download_check, "small"),
                            (self.scale_menu, "small")):
            widget.configure(font=self.fonts[key])
        for check in self.preference_checks:
            check.configure(font=self.fonts["small"])
        for menu in (self.cookies_combo["menu"], self.scale_menu["menu"]):
            menu.configure(font=self.fonts["small"])
        self._layout_input()
        self.apply_theme(self.theme_name)
        self._autosize()
        if save:
            self._save_settings()

    def _build_actions(self):
        row = tk.Frame(self.shell, bg=self.palette["bg"])
        self._actions_row = row

        self.start_button = widgets.Button(
            row, "Start queue", self.start_queue, self.palette, self.fonts,
            height=34)
        self.cancel_button = widgets.Button(
            self.run_controls, "■", self.cancel, self.palette, self.fonts,
            kind="danger", height=26, width=30, padding=(0, 0))
        self.pause_button = widgets.Button(self.run_controls, "Ⅱ", self.toggle_pause,
            self.palette, self.fonts, kind="ghost", height=26, width=30, padding=(0, 0))

        self.queue_hint = self._label(row, "", "small", "text_faint")

        self.terminal_heading = self._section_heading(self.shell, "Terminal", self.toggle_log)
        self.terminal_button = self.terminal_heading.disclosure
        # Auto-open on first activity each run, unless the user hides it.
        self.log_frame = tk.Frame(self.shell, bg=self.palette["bg"])
        self.log = tk.Text(
            self.log_frame, height=6, width=1, wrap="word", state="disabled",
            font=self.fonts["mono"], bd=0, highlightthickness=1,
            highlightbackground=self.palette["border"],
            bg=self.palette["surface"], fg=self.palette["text"],
            insertbackground=self.palette["text"], padx=10, pady=8)
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("error", foreground=self.palette["danger"])
        self.log.tag_configure("ok", foreground=self.palette["success"])
        self.log.tag_configure("info", foreground=self.palette["text"])
        self.log.tag_configure("muted", foreground=self.palette["text_dim"])
        self._log_visible = False
        self._log_has_content = False
        self._log_user_hidden = False

    def _section_heading(self, parent, title, command=None):
        row = tk.Frame(parent, bg=self.palette["bg"])
        if command:
            button = widgets.Button(row, title + " ▸", command, self.palette, self.fonts,
                kind="ghost", height=26, font_key="small", padding=(8, 0))
            button.pack(side="left", padx=(0, 10))
            row.disclosure = button
        else:
            self._label(row, title, "small", "text_dim").pack(side="left", padx=(0, 10))
        rule = tk.Frame(row, height=1, bg=self.palette["border"])
        rule._section_rule = True
        rule.pack(side="left", fill="x", expand=True)
        return row

    # ── theme switching ──────────────────────────────────────────────

    def toggle_theme(self):
        new_name = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme(new_name)
        self._save_settings()

    def apply_theme(self, name):
        self.palette = theme.PALETTES[name]
        p = self.palette
        self.root.configure(bg=p["bg"])
        self.window_edge.set_palette(p)


        def walk(w):
            cls = w.winfo_class()
            try:
                if cls == "Frame":
                    w.configure(bg=p["border"] if getattr(w, "_section_rule", False) else p["bg"])
                elif cls == "Label":
                    # Labels keep their own foreground role; only the backing
                    # colour follows the parent surface.
                    w.configure(bg=p["bg"])
                elif cls == "Text":
                    w.configure(bg=p["surface"], fg=p["text"])
                elif cls == "Listbox":
                    w.configure(bg=p["surface_alt"], fg=p["text"],
                                selectbackground=p["accent"],
                                selectforeground=p["accent_text"],
                                highlightbackground=p["border"])
            except tk.TclError:
                pass
            for c in w.winfo_children():
                walk(c)

        walk(self.shell)

        # Widgets living inside the options card sit on the surface colour,
        # not the window background.
        def walk_surface(w):
            for c in w.winfo_children():
                try:
                    if c.winfo_class() in ("Frame", "Label", "Checkbutton"):
                        c.configure(bg=p["surface"])
                        if c.winfo_class() == "Checkbutton":
                            c.configure(activebackground=p["surface"])
                except tk.TclError:
                    pass
                walk_surface(c)

        walk_surface(self.advanced_card)

        # Re-apply the labels whose colour depends on their role, not just
        # the background (the title was being reset to the dim text colour).
        self.title_label.configure(bg=p["bg"], fg=p["text"])
        self.brand_icon.configure(bg=p["bg"])
        for label in (self.subtitle, self.mode_help, self.detail_label,
                      self.queue_hint):
            label.configure(fg=p["text_dim"], bg=p["bg"])
        self.queue_count.configure(fg=p["text_faint"], bg=p["surface"])

        self.advanced_card.configure(bg=p["surface"],
                                     highlightbackground=p["border"])
        self.dest_label.configure(bg=p["bg"], fg=p["text_dim"])
        self.update_status.configure(bg=p["bg"], fg=p["text_dim"])
        self.quality_feedback.configure(bg=p["bg"], fg=p["text_dim"])
        self._refresh_preview_feedback()
        self.log.configure(bg=p["surface"], fg=p["text"],
                           highlightbackground=p["border"])
        for tag, key in (("error", "danger"), ("ok", "success"),
                         ("info", "text"), ("muted", "text_dim")):
            self.log.tag_configure(tag, foreground=p[key])

        self.archive_check.configure(
            bg=p["surface"], fg=p["text"], activebackground=p["surface"],
            activeforeground=p["text"], selectcolor=p["surface_alt"])
        self.auto_download_check.configure(bg=p["bg"], fg=p["text"],
            activebackground=p["bg"], activeforeground=p["text"],
            selectcolor=p["surface_alt"])
        self.cookies_combo.configure(
            bg=p["surface_alt"], fg=p["text"],
            activebackground=p["surface_hi"], activeforeground=p["text"])
        self.cookies_combo["menu"].configure(
            bg=p["surface_alt"], fg=p["text"],
            activebackground=p["accent"], activeforeground=p["accent_text"])
        self.scale_menu.configure(bg=p["surface_alt"], fg=p["text"],
            activebackground=p["surface_hi"], activeforeground=p["text"],
            highlightthickness=0, bd=0)
        self.scale_menu["menu"].configure(bg=p["surface_alt"], fg=p["text"],
            activebackground=p["accent"], activeforeground=p["accent_text"])
        for check in self.preference_checks:
            check.configure(bg=p["surface"], fg=p["text"], activebackground=p["surface"],
                activeforeground=p["text"], selectcolor=p["surface_alt"])
        self.queue_scroll.configure(
            troughcolor=p["surface"], bg=p["surface_hi"],
            activebackground=p["border_hi"])

        self.input_canvas.configure(bg=p["bg"])
        self.url_entry.configure(bg=p["surface_alt"], fg=p["text"],
                                 insertbackground=p["text"])
        self.placeholder.configure(bg=p["surface_alt"], fg=p["text_faint"])
        self._paint_input()

        for widget in self._all_themed_widgets():
            try:
                widget.set_palette(p)
            except (AttributeError, tk.TclError):
                pass
        self.status_label.set_palette(p)
        self.progress.set_palette(p)

    def _all_themed_widgets(self):
        out = []

        def walk(w):
            if isinstance(w, (widgets.Button, widgets.Chip, widgets.IconButton,
                              widgets.ProgressBar)):
                out.append(w)
            for c in w.winfo_children():
                walk(c)

        walk(self.root)
        # IconButton/other canvases may not be in the tree yet during build.
        return out

    # ── layout helpers ───────────────────────────────────────────────

    def show_ready(self):
        """Reveal one completed layout, including Windows' native frame pass."""
        masked = False
        try:
            self.root.attributes("-alpha", 0.0)
            masked = True
        except tk.TclError:
            pass
        self.root.deiconify()
        self.root.update()
        if self.chrome:
            self.chrome.apply()
        self._autosize()
        self.root.update()
        if masked:
            self.root.attributes("-alpha", 1.0)
        self.url_entry.focus_set()

    def _on_window_map(self, event):
        if event.widget is self.root:
            self._schedule_layout()

    def _schedule_layout(self):
        if self._layout_id is None and not self._destroyed:
            self._layout_id = self.root.after_idle(self._settle_layout)

    def _settle_layout(self):
        self._layout_id = None
        if not self._destroyed:
            self._autosize()

    def _autosize(self):
        try:
            self.root.update_idletasks()
            # Tk's cached non-client border size can differ after Windows
            # caption removal. Reusing the observed client width repeatedly
            # would grow the window each time Music or Options is toggled.
            # Only one large panel is open. Give its content a compact
            # viewport of its own (queue / log), never a scrolling app page.
            cap = max(260, self.root.winfo_screenheight() - 64)
            self.queue_list.configure(height=2)
            self.root.update_idletasks()
            if self.settings_visible and self.shell.winfo_reqheight() + 6 > cap:
                self.queue_list.configure(height=1)
                self.root.update_idletasks()
            width = max(self.shell.winfo_reqwidth() + 6, 640)
            height = self.shell.winfo_reqheight() + 6
            geometry = f"{width}x{height}"
            if self.root.winfo_ismapped():
                x, y = self.root.winfo_x(), self.root.winfo_y()
                fitted_y = max(0, min(y, self.root.winfo_screenheight() - height - 48))
                if fitted_y != y:
                    geometry += f"+{max(0, x)}+{fitted_y}"
            self.root.geometry(geometry)
            self.root.update_idletasks()
            if self.chrome:
                self.chrome.keep_on_screen()
        except tk.TclError:
            pass

    def toggle_settings(self, force=None):
        self.settings_visible = (not self.settings_visible
                                 if force is None else bool(force))
        if self.settings_visible:
            self._log_visible = False
            self.terminal_button.set_text("Terminal ▸")
            self.terminal_button.set_active(False)
        self.settings_button.set_text("Options ▴" if self.settings_visible else "Options ▾")
        self.settings_button.set_active(self.settings_visible)
        self._repack()
        self._autosize()

    def toggle_log(self, force=None):
        self._log_visible = (not self._log_visible
                             if force is None else bool(force))
        if self._log_visible:
            self.settings_visible = False
            self.settings_button.set_text("Options ▾")
            self.settings_button.set_active(False)
        if force is None:
            self._log_user_hidden = not self._log_visible
        self.terminal_button.set_text("Terminal ▾" if self._log_visible else "Terminal ▸")
        self.terminal_button.set_active(self._log_visible)
        self._repack()
        self._autosize()

    def _set_progress_visible(self, visible):
        self._progress_visible = bool(visible)
        if not visible:
            self.progress.set_indeterminate(False)
        self._repack()
        self._autosize()

    def _update_queue_hint(self, running=None):
        count = len(self.queue_items())
        if count:
            self.queue_hint.configure(text=f"{count} queued")
            self.start_button.pack(side="left")
            if not self.queue_hint.winfo_manager():
                self.queue_hint.pack(side="left", padx=(10, 0))
        else:
            self.queue_hint.configure(text="")
            self.start_button.pack_forget()
            self.queue_hint.pack_forget()

        if running is None:
            running = bool(self.worker and self.worker.is_alive())
        self._actions_visible = bool(count and not running)
        self._repack()
        if running and not self._log_visible:
            pass

    # ── small actions ────────────────────────────────────────────────

    def _on_quality(self, value):
        for chip in self.chips:
            chip.set_selected(chip.value == value)
        self._refresh_preview_feedback()

    def _selected_height(self):
        for chip in self.chips:
            if chip._selected:
                return chip.value
        return None

    def _on_mode(self, key):
        self.mode_var.set(key)
        for chip in self.mode_chips:
            chip.set_selected(chip.value == key)
        self._update_mode_help()
        self._refresh_preview_feedback()

    def _update_mode_help(self):
        self.mode_help.configure(text=MODE_HELP.get(self.mode_var.get(), ""))

    def choose_output(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(initialdir=self.output_var.get() or None)
        if chosen:
            self.output_var.set(chosen)

    def open_output(self):
        raw_path = self.output_var.get().strip() or Settings().output_dir
        path = os.path.abspath(os.path.expanduser(os.path.expandvars(raw_path)))
        if self.media_var.get() == "audio":
            path = os.path.join(path, "Audio")
        self._open_folder_action(path)

    def _open_folder_action(self, path):
        platform = sys.platform
        def action():
            os.makedirs(path, exist_ok=True)
            if platform == "win32":
                desktop.open_folder_windows(path)
            else:
                command = "open" if platform == "darwin" else "xdg-open"
                result = subprocess.run([command, path], capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise OSError((result.stderr or "Folder opener failed").strip()[-200:])
        self._external_action("folder", path, action)

    def append_log(self, text, tag="info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        if not self._log_has_content:
            self._log_has_content = True
            if not self._log_user_hidden and not self.settings_visible:
                self.toggle_log(True)

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._log_has_content = False

    def queue_items(self):
        return list(self.queue_list.get(0, "end"))

    def add_to_queue(self):
        urls = engine_split_urls(self.url_var.get())
        for url in urls:
            self.queue_list.insert("end", url)
        if urls:
            self.url_var.set("")
            self._enqueue_for_running_job(urls)
        self._refresh_queue_ui()

    def _enqueue_for_running_job(self, urls):
        """Hand links to a live run so they download without a restart.

        The worker thread owns the download loop, so the Tk thread must not
        touch its list. A lock-protected buffer that the engine drains
        between items keeps that hand-off safe.
        """
        if not (self.worker and self.worker.is_alive()):
            return
        with self._pending_lock:
            self._pending_links.extend(urls)
        self.append_log(f"{len(urls)} link(s) added to the running queue",
                        "muted")

    def _take_pending_links(self):
        """Called from the worker thread; returns and clears buffered URLs."""
        with self._pending_lock:
            if not self._pending_links:
                return []
            taken = list(self._pending_links)
            self._pending_links.clear()
            return taken

    def remove_selected(self):
        for index in reversed(self.queue_list.curselection()):
            self.queue_list.delete(index)
        self._refresh_queue_ui()

    def clear_queue(self):
        self.queue_list.delete(0, "end")
        self._refresh_queue_ui()

    def _refresh_queue_ui(self):
        count = len(self.queue_items())
        self.queue_count.configure(text=f"{count} link(s) queued" if count else "")
        if count:
            self.queue_count.pack(anchor="w", pady=(4, 0))
        else:
            self.queue_count.pack_forget()
        if count and not self.settings_visible:
            self.toggle_settings(True)
        self._update_queue_hint()

    def load_links_file(self):
        from .cli import read_queue
        path = Settings().link_file
        urls = read_queue(path)
        if not urls:
            return
        for url in urls:
            self.queue_list.insert("end", url)
        self.append_log(f"Loaded {len(urls)} URL(s) from links.txt", "muted")
        self._refresh_queue_ui()

    def paste_list(self):
        win = tk.Toplevel(self.root)
        win.title("Paste links")
        win.geometry("560x320")
        win.configure(bg=self.palette["bg"])
        win.transient(self.root)
        win.grab_set()

        self._label(win, "Paste any number of links, one per line.",
                    "small", "text_dim").pack(anchor="w", padx=16, pady=(14, 8))
        text = tk.Text(win, wrap="none", font=self.fonts["mono"], bd=0,
                       highlightthickness=1,
                       highlightbackground=self.palette["border"],
                       bg=self.palette["surface"], fg=self.palette["text"],
                       insertbackground=self.palette["text"])
        text.pack(fill="both", expand=True, padx=16)
        text.focus_set()

        bar = tk.Frame(win, bg=self.palette["bg"])
        bar.pack(fill="x", padx=16, pady=14)

        def accept(_e=None):
            for url in engine_split_urls(text.get("1.0", "end")):
                self.queue_list.insert("end", url)
            win.destroy()
            self._refresh_queue_ui()

        widgets.Button(bar, "Add to queue", accept, self.palette, self.fonts,
                       height=32).pack(side="right")
        widgets.Button(bar, "Cancel", win.destroy, self.palette, self.fonts,
                       kind="ghost", height=32).pack(side="right", padx=(0, 8))

    # ── run control ──────────────────────────────────────────────────

    def _make_settings(self):
        return Settings(
            mode="best" if self.media_var.get() == "audio" else self.mode_var.get(),
            output_dir=self.output_var.get(),
            max_height=self._selected_height(),
            use_archive=bool(self.archive_var.get()),
            cookies_from_browser=self.cookies_var.get().strip() or None,
            media_type=self.media_var.get(),
            audio_format=self.audio_format_var.get(),
            audio_bitrate=self.audio_bitrate_var.get() or None,
        )

    def _set_running(self, running):
        # The link box and Queue stay usable while downloading: pasting
        # another link adds it to the live queue instead of being blocked
        # for the whole run.
        self.single_button.set_enabled(True)
        self.add_button.set_enabled(True)
        self.start_button.set_enabled(not running)
        if running:
            self.cancel_button.set_enabled(True)
            if not self.cancel_button.winfo_manager():
                self.cancel_button.pack(side="right")
                self.pause_button.pack(side="right", padx=(0, 8))
            self.pause_button.set_enabled(True)
        else:
            self.cancel_button.set_enabled(False)
            self.cancel_button.pack_forget()
            self.pause_button.pack_forget()
            self._pause_requested = False
            self.pause_button.set_text("Ⅱ")
            self.pause_button.set_active(False)
        self.root.configure(cursor="")
        # Setting the action row's visibility may add the Cancel button,
        # which changes the required height -- size after, not before.
        self._update_queue_hint(running=running)
        self._autosize()

    def _preflight(self, settings):
        try:
            os.makedirs(settings.media_output_dir, exist_ok=True)
            with tempfile.TemporaryFile(dir=settings.media_output_dir):
                pass
        except OSError:
            from tkinter import filedialog
            chosen = filedialog.askdirectory(parent=self.root,
                title="Download folder unavailable — choose another folder",
                initialdir=os.path.expanduser("~"))
            if not chosen:
                return False
            self.output_var.set(chosen)
            try:
                destination = self._make_settings().media_output_dir
                os.makedirs(destination, exist_ok=True)
                with tempfile.TemporaryFile(dir=destination):
                    pass
            except OSError:
                return False
        if not engine.find_ffmpeg(settings) or not engine.find_ffprobe(settings):
            messagebox.showerror(
                "YT-Duo", "ffmpeg/ffprobe not found.\n"
                         "Run YT-Duo.bat to install the dependencies.")
            return False
        return True

    def download_single(self):
        urls = engine_split_urls(self.url_var.get())
        if not urls:
            return

        # A run is already going: queue these instead of refusing them, so
        # pasting a second link mid-download Just Works.
        if self.worker and self.worker.is_alive():
            for url in urls:
                self.queue_list.insert("end", url)
            self._enqueue_for_running_job(urls)
            self.url_var.set("")
            self._refresh_queue_ui()
            return

        if len(urls) > 1:
            for url in urls:
                self.queue_list.insert("end", url)
            self.url_var.set("")
            self._refresh_queue_ui()
            self.start_queue()
            return
        if self._start([urls[0]]):
            self.url_var.set("")

    def start_queue(self):
        urls = self.queue_items()
        if not urls:
            messagebox.showinfo("YT-Duo", "The queue is empty.")
            return
        self._start(urls)

    def _start(self, urls):
        if self.worker and self.worker.is_alive():
            return
        settings = self._make_settings()
        if not self._preflight(settings):
            return
        settings = self._make_settings()
        self._save_settings()

        self.bridge.reset()
        self._log_user_hidden = False
        self.toggle_log(True)
        self._set_running(True)
        self._set_progress_visible(True)
        self.progress.set(0, immediate=True)
        self.progress.set_indeterminate(True)
        self.status_label.configure(text=f"Starting {len(urls)} download(s)…")
        self.detail_label.configure(text="")
        self.current_output = None
        self.append_log(f"Starting {len(urls)} item(s) — format {settings.mode}, "
                        f"quality {settings.max_height or 'best'}", "muted")

        bridge = self.bridge

        def work():
            settings.ensure_dirs()
            return engine.run_batch(
                urls, settings, emit=bridge.emit,
                should_stop=bridge.checkpoint,
                take_more=self._take_pending_links,
                confirm_redownload=self._confirm_redownload)

        self.worker = Worker(work, bridge)
        self.worker.start()
        return True

    def cancel(self):
        if self.worker and self.worker.is_alive():
            self.bridge.request_cancel()
            self._finish_redownload(False)
            self.pause_button.set_enabled(False)
            self.cancel_button.set_enabled(False)
            self.status_label.configure(text="Stopping…")
            self.append_log("Stop requested.", "error")

    def toggle_pause(self):
        if not (self.worker and self.worker.is_alive()) or self.bridge.cancelled:
            return
        self._pause_requested = not self._pause_requested
        self.pause_button.set_text("▶" if self._pause_requested else "Ⅱ")
        self.pause_button.set_active(self._pause_requested)
        if self._pause_requested:
            self.bridge.request_pause()
            self.progress.set_indeterminate(False)
            self.status_label.configure(text="Pausing at the next checkpoint…")
        else:
            self.bridge.request_resume()
            self.status_label.configure(text="Resuming…")

    def _on_pause_state(self, data):
        if self.bridge.cancelled:
            return
        self.status_label.configure(text="Paused" if data["paused"] and self._pause_requested else "Resuming…")

    def _confirm_redownload(self, url):
        request = {"url": url, "event": threading.Event(), "approved": False}
        self.bridge.emit("redownload_request", request)
        while not request["event"].wait(0.1):
            if self.bridge.cancelled or self._destroyed:
                return False
        return request["approved"] and not self.bridge.cancelled

    def _on_redownload_request(self, request):
        if self._destroyed or self.bridge.cancelled:
            request["event"].set()
            return
        self._redownload_request = request
        win = tk.Toplevel(self.root)
        self._redownload_dialog = win
        win.title("Already downloaded")
        win.configure(bg=self.palette["bg"])
        win.transient(self.root)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", lambda: self._finish_redownload(False))
        self._label(win, "Download again?", "body_bold").pack(
            anchor="w", padx=20, pady=(18, 8))
        self._redownload_countdown = self._label(win, "", "small", "text_dim")
        self._redownload_countdown.pack(anchor="w", padx=20, pady=(10, 0))
        buttons = tk.Frame(win, bg=self.palette["bg"])
        buttons.pack(fill="x", padx=20, pady=16)
        widgets.Button(buttons, "Again", lambda: self._finish_redownload(True),
            self.palette, self.fonts, height=32).pack(side="right")
        skip = tk.Button(buttons, text="Skip", command=lambda: self._finish_redownload(False),
            bg=self.palette["surface_alt"], fg=self.palette["text"], font=self.fonts["button"],
            relief="flat", padx=12)
        skip.pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda _e: self._finish_redownload(False))
        win.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_reqheight()) // 2)
        win.geometry(f"+{x}+{y}")
        skip.focus_set()
        self.status_label.configure(text="Already downloaded")
        self._redownload_tick(10)

    def _redownload_tick(self, remaining):
        self._redownload_timer = None
        if not self._redownload_request:
            return
        if remaining <= 0:
            self._finish_redownload(False)
            return
        self._redownload_countdown.configure(text=f"Skip in {remaining}s")
        self._redownload_timer = self.root.after(1000, lambda: self._redownload_tick(remaining - 1))

    def _finish_redownload(self, approved):
        if self._redownload_timer:
            self.root.after_cancel(self._redownload_timer)
            self._redownload_timer = None
        if self._redownload_dialog:
            self._redownload_dialog.destroy()
            self._redownload_dialog = None
        request, self._redownload_request = self._redownload_request, None
        if request:
            request["approved"] = bool(approved)
            request["event"].set()

    # ── event pump ───────────────────────────────────────────────────

    def _poll(self):
        try:
            self.bridge.pump(self._handle_event)
            self.service_bridge.pump(self._handle_event)
            self._poll_id = self.root.after(POLL_MS, self._poll)
        except tk.TclError:
            self._poll_id = None

    def destroy(self):
        self._destroyed = True
        self.bridge.request_cancel()
        self._finish_redownload(False)
        self._preview_generation += 1
        for timer in (self._preview_id, self._update_start_id, self._layout_id, self._scan_anim_id):
            if timer:
                try:
                    self.root.after_cancel(timer)
                except tk.TclError:
                    pass
        for widget in self._all_themed_widgets():
            try:
                if isinstance(widget, widgets.ProgressBar):
                    widget.stop()
                if isinstance(widget, widgets.Animated):
                    widget.stop_animation()
            except tk.TclError:
                pass
        self.status_label.stop()
        if getattr(self, "_poll_id", None):
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None

    def _handle_event(self, name, data):
        handler = getattr(self, f"_on_{name}", None)
        if handler:
            handler(data)

    def _on_item_start(self, d):
        self._log_user_hidden = False
        self.toggle_log(True)
        self.status_label.configure(text=f"[{d['index']}/{d['total']}] Preparing…")
        self.progress.set(0, immediate=True)
        self.progress.set_indeterminate(True)

    def _on_probe(self, d):
        self.append_log(f"[{d['index']}] {d['title']}", "info")
        self.append_log(f"    {d['video']}", "muted")
        if d.get("audio"):
            self.append_log(f"    {d['audio']}", "muted")

    def _on_progress(self, d):
        pct = d.get("percent", "").replace("%", "").strip()
        try:
            value = float(pct)
        except ValueError:
            value = 0.0
        self.progress.set_indeterminate(False)
        self.progress.set(value)
        self.status_label.configure(text=f"[{d['index']}] Downloading…")
        bits = [d.get("percent", "")]
        if d.get("speed"):
            bits.append(d["speed"])
        if d.get("eta"):
            bits.append(f"ETA {d['eta']}")
        self.detail_label.configure(text="   ".join(b for b in bits if b))

    def _on_stream_done(self, d):
        self.append_log(f"    {d['label']} {d['size']} done", "muted")

    def _on_retry(self, d):
        self.append_log(f"    {d['label']} interrupted — retry "
                        f"{d['attempt']}/{d['total'] - 1} in {d['wait']}s", "error")

    def _on_warning(self, d):
        message = d.get("message", "")
        if message:
            self.append_log(f"    {message}", "error")

    def _on_merged(self, d):
        self.progress.set(100)

    def _on_item_ok(self, d):
        report = d["report"]
        self.current_output = report.get("path")
        if report.get("media_type") == "audio":
            self.append_log(f"    music saved · {report['audio_codec']} · {report['duration']}s", "ok")
        else:
            self.append_log(
                f"    done {report['resolution']} @ {report['fps']}fps "
                f"{report['video_codec']}/{report['audio_codec']}", "ok")
        self.append_log(f"    {os.path.basename(report['path'])}", "muted")

    def _on_item_failed(self, d):
        self.append_log(f"    failed: {d['reason']}", "error")

    def _on_item_skipped(self, d):
        self.append_log(f"[{d['index']}] already downloaded — skipped", "muted")

    def _on_queue_grew(self, d):
        self.append_log(
            f"Queued {d['added']} more link(s) — {d['total']} total in this run",
            "info")

    def _on_recycle_pruned(self, d):
        self.append_log(f"Pruned {d['removed']} old file(s), freed {d['freed']}",
                        "muted")

    def _on_orphans_removed(self, d):
        self.append_log(f"Removed {d['removed']} partial file(s), freed {d['freed']}",
                        "muted")

    def _on_worker_done(self, d):
        summary = d.get("result") or {}
        self._finish()
        completed = summary.get("completed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        pending = summary.get("pending", 0)
        abandoned = summary.get("abandoned", 0)
        cancelled = summary.get("cancelled", False)

        parts = [f"{completed} downloaded"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        if abandoned:
            parts.append(f"{abandoned} interrupted")
        if pending:
            parts.append(f"{pending} not started")

        prefix = "Cancelled — " if cancelled else "Done — "
        text = prefix + ", ".join(parts)
        self.status_label.flash(text, "danger" if (failed or cancelled) else "success")
        self.detail_label.configure(text="")
        self.append_log(text, "error" if (failed or cancelled) else "ok")
        if completed and not cancelled and self.open_after_var.get() and self.current_output:
            self._open_folder_action(os.path.dirname(self.current_output))

    def _on_worker_cancelled(self, d):
        self._finish()
        self.status_label.flash("Cancelled", "warning")
        self.detail_label.configure(text="")
        self.append_log("Cancelled by user.", "error")

    def _on_worker_error(self, d):
        self._finish()
        exc = d.get("error")
        reason = engine.classify_error(exc) if exc else "unknown error"
        self.status_label.flash(f"Stopped — {reason}", "danger")
        self.append_log(f"Stopped: {reason}", "error")
        messagebox.showerror("YT-Duo", f"The run stopped:\n\n{reason}")

    def _finish(self):
        self._set_running(False)
        self.worker = None
        self.progress.set_indeterminate(False)
        self.progress.set(0, immediate=True)
        self._set_progress_visible(False)
        self._autosize()

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("YT-Duo",
                                       "A download is running. Quit anyway?"):
                return
            self.bridge.request_cancel()
        self._save_settings()
        self.destroy()
        self.root.destroy()


def engine_split_urls(text):
    """URLs out of pasted text -- same rules as the CLI."""
    return extract_urls(text)


def main():
    root = tk.Tk()
    # Construction calls update_idletasks for font/widget measurement. Keep
    # those passes hidden, then settle the mapped native frame transparently.
    root.withdraw()
    app = YtDuoApp(root)
    app.show_ready()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
