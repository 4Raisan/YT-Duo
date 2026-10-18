"""Desktop front-end (tkinter, no extra dependencies).

The window is deliberately thin: it gathers Settings, calls
``engine.run_batch`` on a worker thread, and renders the events that come
back. All policy -- format ranking, naming, merging, pruning -- stays in
the engine, so this module contains no yt-dlp or ffmpeg knowledge.

Layout philosophy: one obvious action (paste a link, press the button) with
everything else out of the way. Advanced options live behind a collapsible
panel, the activity log only appears when there is something to say, and
the window is a single 620-wide column rather than a wall of label-frames.

    python -m downy.gui          or          runner.bat --gui
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, engine, selector
from .cli import extract_urls
from .config import Settings, read_config_file, write_config
from .gui_worker import EventBridge, Worker


QUALITY_CHOICES = [
    ("Best available", None),
    ("2160p (4K)", 2160),
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

POLL_MS = 80

# Palette: a calm neutral column with one accent.
BG = "#f7f7f8"
CARD = "#ffffff"
BORDER = "#e3e3e6"
TEXT = "#1f1f23"
MUTED = "#6b6b76"
ACCENT = "#2563eb"
OK = "#15803d"
ERROR = "#b91c1c"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_SUB = ("Segoe UI", 9)
FONT_ENTRY = ("Segoe UI", 11)
FONT_MONO = ("Consolas", 9)


class DownyApp:
    def __init__(self, root):
        self.root = root
        self.bridge = EventBridge()
        self.worker = None
        self.cancel_button = None
        self.current_output = None
        self.settings_visible = False

        self.settings_var = {}
        self._load_settings()
        self._build_ui()
        self._poll()

    # ── settings persistence ─────────────────────────────────────────

    def _config_path(self):
        return Settings().config_file

    def _load_settings(self):
        self.saved = read_config_file(self._config_path())

    def _save_settings(self):
        data = dict(self.saved)
        data.update({
            "mode": self.mode_var.get(),
            "max_height": self._selected_height(),
            "output_dir": self.output_var.get(),
            "use_archive": bool(self.archive_var.get()),
            "cookies_from_browser": self.cookies_var.get().strip() or None,
        })
        try:
            write_config(self._config_path(), data)
        except OSError:
            pass

    # ── theme ────────────────────────────────────────────────────────

    def _apply_theme(self):
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")

        self.root.configure(bg=BG)
        style.configure(".", font=FONT, background=BG, foreground=TEXT)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=FONT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SUB)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=FONT_SUB)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
        style.configure("TButton", padding=(10, 5), font=FONT)
        style.configure("TCheckbutton", background=BG, font=FONT)
        style.configure("Card.TCheckbutton", background=CARD, font=FONT)
        style.configure("TCombobox", font=FONT)
        style.configure("Small.TButton", padding=(6, 2), font=FONT)

    # ── layout ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.title(f"DownY {__version__}")
        # Start at the idle size, then let the window track its content as
        # panels open and close (see _autosize).
        self.root.geometry("680x210")
        self.root.minsize(620, 200)
        self._apply_theme()

        column = ttk.Frame(self.root, padding=(18, 14, 18, 12))
        column.pack(fill="both", expand=True)
        column.columnconfigure(0, weight=1)

        self._build_header(column)
        self._build_link_row(column)
        self._build_settings_strip(column)
        self._build_advanced(column)
        self._build_progress(column)
        self._build_log(column)
        self._build_actions(column)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.url_entry.focus_set()

    def _build_header(self, parent):
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(0, 12))

        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="DownY", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="Paste a link. Press Enter.",
                  style="Muted.TLabel").pack(anchor="w")

        right = ttk.Frame(header)
        right.pack(side="right")
        self.settings_button = ttk.Button(
            right, text="⚙  Options", style="Small.TButton",
            command=self.toggle_settings)
        self.settings_button.pack(side="right")

    def _build_link_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x")

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(
            row, textvariable=self.url_var, font=FONT_ENTRY)
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.url_entry.bind("<Return>", lambda _e: self.download_single())
        self.url_entry.bind("<KP_Enter>", lambda _e: self.download_single())

        self.single_button = ttk.Button(
            row, text="Download", command=self.download_single)
        self.single_button.pack(side="left", padx=(8, 0))

        # Queue controls sit on the same line as the link, collapsed until used.
        self.add_button = ttk.Button(
            row, text="+ Queue", style="Small.TButton", command=self.add_to_queue)
        self.add_button.pack(side="left", padx=(6, 0))

    def _build_settings_strip(self, parent):
        """One compact row: quality + mode. Always visible, never bulky."""
        strip = ttk.Frame(parent)
        strip.pack(fill="x", pady=(10, 0))

        self.quality_var = tk.IntVar()
        saved_height = self.saved.get("max_height")
        ttk.Label(strip, text="Quality", style="Muted.TLabel").pack(side="left")
        self.quality_combo = ttk.Combobox(
            strip, values=[label for label, _ in QUALITY_CHOICES],
            state="readonly", width=14)
        self.quality_combo.current(
            next((i for i, (_, h) in enumerate(QUALITY_CHOICES)
                  if h == saved_height), 0))
        self.quality_combo.pack(side="left", padx=(6, 16))

        saved_mode = self.saved.get("mode", "best")
        self.mode_var = tk.StringVar(value=saved_mode)
        self._mode_label_var = tk.StringVar(
            value=MODE_TO_LABEL.get(saved_mode, "Best quality"))

        ttk.Label(strip, text="Format", style="Muted.TLabel").pack(side="left")
        self.mode_combo = ttk.Combobox(
            strip, textvariable=self._mode_label_var,
            values=MODE_LABELS, state="readonly", width=17)
        self.mode_combo.pack(side="left", padx=(6, 8))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_selected)

        self.mode_help = ttk.Label(strip, text="", style="Muted.TLabel")
        self.mode_help.pack(side="left", padx=(2, 0))
        self._update_mode_help()

        # Keep the public var authoritative no matter how it was changed.
        self.mode_var.trace_add("write", self._on_mode_var_written)

    def _on_mode_selected(self, _event=None):
        self.mode_var.set(LABEL_TO_MODE.get(self._mode_label_var.get(), "best"))
        self._update_mode_help()

    def _on_mode_var_written(self, *_args):
        label = MODE_TO_LABEL.get(self.mode_var.get())
        if label and self._mode_label_var.get() != label:
            self._mode_label_var.set(label)
        self._update_mode_help()

    def _build_advanced(self, parent):
        """Collapsible options panel -- hidden until asked for."""
        self.advanced = ttk.Frame(parent)

        body = tk.Frame(self.advanced, bg=CARD,
                        highlightbackground=BORDER, highlightthickness=1)
        body.pack(fill="x", pady=(10, 0))

        inner = ttk.Frame(body, style="Card.TFrame", padding=12)
        inner.pack(fill="x")
        inner.columnconfigure(1, weight=1)

        ttk.Label(inner, text="Save to", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=2)
        self.output_var = tk.StringVar(
            value=self.saved.get("output_dir") or Settings().output_dir)
        folder_row = ttk.Frame(inner, style="Card.TFrame")
        folder_row.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=2)
        self.output_entry = ttk.Entry(folder_row, textvariable=self.output_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(folder_row, text="Browse", style="Small.TButton",
                   command=self.choose_output).pack(side="left", padx=(6, 0))
        ttk.Button(folder_row, text="Open", style="Small.TButton",
                   command=self.open_output).pack(side="left", padx=(4, 0))

        ttk.Label(inner, text="Cookies", style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=2)
        cookies_row = ttk.Frame(inner, style="Card.TFrame")
        cookies_row.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=2)
        self.cookies_var = tk.StringVar(
            value=self.saved.get("cookies_from_browser") or "")
        ttk.Combobox(
            cookies_row, textvariable=self.cookies_var, width=12,
            values=["", "chrome", "firefox", "edge", "brave", "opera"],
            state="readonly",
        ).pack(side="left")
        ttk.Label(cookies_row, text="only needed for age-gated videos",
                  style="CardMuted.TLabel").pack(side="left", padx=(8, 0))

        checkbox_row = ttk.Frame(inner, style="Card.TFrame")
        checkbox_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.archive_var = tk.IntVar(
            value=1 if self.saved.get("use_archive", True) else 0)
        ttk.Checkbutton(
            checkbox_row, text="Skip videos already downloaded",
            variable=self.archive_var, style="Card.TCheckbutton").pack(side="left")

        ttk.Label(inner, text="Queue", style="CardMuted.TLabel").grid(
            row=3, column=0, sticky="nw", pady=(10, 2))
        queue_row = ttk.Frame(inner, style="Card.TFrame")
        queue_row.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=(10, 2))
        ttk.Button(queue_row, text="Add link", style="Small.TButton",
                   command=self.add_to_queue).pack(side="left")
        ttk.Button(queue_row, text="Paste list", style="Small.TButton",
                   command=self.paste_list).pack(side="left", padx=(6, 0))
        ttk.Button(queue_row, text="Load links.txt", style="Small.TButton",
                   command=self.load_links_file).pack(side="left", padx=(6, 0))

        # The queue list itself only appears once it has items.
        self.queue_frame = ttk.Frame(inner, style="Card.TFrame")
        self.queue_frame.grid(row=4, column=0, columnspan=2,
                              sticky="ew", pady=(6, 0))
        self.queue_frame.columnconfigure(0, weight=1)

        holder = ttk.Frame(self.queue_frame, style="Card.TFrame")
        holder.pack(fill="x")
        self.queue_list = tk.Listbox(
            holder, height=4, activestyle="none", bd=1,
            relief="solid", highlightthickness=0,
            font=FONT, selectbackground="#dbeafe", selectforeground=TEXT)
        self.queue_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical",
                               command=self.queue_list.yview)
        scroll.pack(side="left", fill="y")
        self.queue_list.configure(yscrollcommand=scroll.set)

        queue_buttons = ttk.Frame(self.queue_frame, style="Card.TFrame")
        queue_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(queue_buttons, text="Remove selected", style="Small.TButton",
                   command=self.remove_selected).pack(side="left")
        ttk.Button(queue_buttons, text="Clear", style="Small.TButton",
                   command=self.clear_queue).pack(side="left", padx=(6, 0))

        self.queue_count = ttk.Label(self.queue_frame, text="",
                                     style="CardMuted.TLabel")
        self.queue_count.pack(anchor="w", pady=(6, 0))

        self.settings_visible = False

    def _build_progress(self, parent):
        self.progress_frame = ttk.Frame(parent)
        self.progress_frame.pack(fill="x")

        self.status_var = tk.StringVar(value="Ready.")
        self.status_label = ttk.Label(
            self.progress_frame, textvariable=self.status_var, font=FONT_BOLD)

        self.progress = ttk.Progressbar(
            self.progress_frame, mode="determinate", maximum=100)

        self.detail_var = tk.StringVar(value="")
        self.detail_label = ttk.Label(self.progress_frame,
                                      textvariable=self.detail_var,
                                      style="Muted.TLabel")

    def _set_progress_visible(self, visible):
        """An empty progress bar reads as broken; show it only while working."""
        changed = False
        if visible:
            if not self.status_label.winfo_manager():
                self.status_label.pack(anchor="w", pady=(12, 0))
                self.progress.pack(fill="x", pady=(4, 2))
                changed = True
        else:
            if self.status_label.winfo_manager():
                self.status_label.pack_forget()
                changed = True
            self.progress.pack_forget()
            self.detail_label.pack_forget()
        if changed:
            self._autosize()

    def _show_detail(self, text):
        """The speed/ETA line is pointless when blank -- hide it instead."""
        self.detail_var.set(text)
        if text and not self.detail_label.winfo_manager():
            self.detail_label.pack(anchor="w")
        elif not text and self.detail_label.winfo_manager():
            self.detail_label.pack_forget()

    def _build_log(self, parent):
        """Activity log, hidden until there is something worth showing."""
        self.log_frame = ttk.Frame(parent)

        top = ttk.Frame(self.log_frame)
        top.pack(fill="x", pady=(10, 4))
        ttk.Label(top, text="Activity", style="Muted.TLabel").pack(side="left")
        ttk.Button(top, text="Clear", style="Small.TButton",
                   command=self.clear_log).pack(side="right")
        self.log_toggle = ttk.Button(
            top, text="Hide", style="Small.TButton", command=self.toggle_log)
        self.log_toggle.pack(side="right", padx=(0, 6))

        self.log = tk.Text(
            self.log_frame, height=6, width=1, wrap="word", state="disabled",
            font=FONT_MONO, bd=1, relief="solid", highlightthickness=0,
            bg=CARD, fg=TEXT, padx=8, pady=6)
        self.log.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(self.log_frame, orient="vertical",
                               command=self.log.yview)
        scroll.pack(fill="y", side="right")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.tag_configure("error", foreground=ERROR)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("info", foreground=TEXT)
        self.log.tag_configure("muted", foreground=MUTED)
        self._log_visible = False
        self._log_has_content = False

    def _build_actions(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(12, 0))
        self._actions_row = row

        # Never pack a widget into the action row until it is wanted: an
        # unmanaged sibling still occupies a pixel and shows as a stub.
        self.start_button = ttk.Button(
            row, text="Start queue", command=self.start_queue)

        self.cancel_button = ttk.Button(
            row, text="Cancel", command=self.cancel, state="disabled")
        # Not packed: _set_running reveals it only while a run is live.

        self.queue_hint = ttk.Label(row, text="", style="Muted.TLabel")
        self._update_queue_hint()

    # ── show/hide helpers ────────────────────────────────────────────

    def on_close(self):
        """Confirm before abandoning a running download."""
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                    "DownY", "A download is running. Quit anyway?"):
                return
            self.bridge.request_cancel()
        self._save_settings()
        self.destroy()
        self.root.destroy()

    def _autosize(self):
        """Grow or shrink the window to match the visible content.

        A collapsing layout that keeps a fixed frame would either clip when
        panels open or leave a dead void when they close. The user's own
        resize is respected by remembering the width they chose.
        """
        try:
            self.root.update_idletasks()
            width = max(self.root.winfo_width(),
                        self.root.winfo_reqwidth(), 620)
            # Cap growth so the log cannot drag the window off-screen.
            height = min(self.root.winfo_reqheight() + 4, 900)
            if (width, height) != (self.root.winfo_width(),
                                   self.root.winfo_height()):
                self.root.geometry(f"{width}x{height}")
                # A resize re-lays-out children; settling here stops widgets
                # that were just packed from keeping a half-height geometry.
                self.root.update_idletasks()
        except tk.TclError:
            pass

    def toggle_settings(self, force=None):
        self.settings_visible = (not self.settings_visible
                                 if force is None else bool(force))
        if self.settings_visible:
            self.advanced.pack(fill="x", before=self.progress_frame)
            self.settings_button.configure(text="⚙  Options  ▴")
        else:
            self.advanced.pack_forget()
            self.settings_button.configure(text="⚙  Options")
        self._autosize()

    def toggle_log(self, force=None):
        visible = (not self._log_visible) if force is None else bool(force)
        if visible and not self._log_visible:
            # Sit above the action row when it is shown; otherwise append.
            if self._actions_row.winfo_manager():
                self.log_frame.pack(fill="both", expand=True,
                                    before=self._actions_row)
            else:
                self.log_frame.pack(fill="both", expand=True)
        elif not visible and self._log_visible:
            self.log_frame.pack_forget()
        self._log_visible = visible
        if hasattr(self, "log_toggle"):
            self.log_toggle.configure(text="Hide" if visible else "Show")
        self._autosize()

    def _ensure_log_visible(self):
        if not self._log_visible and self.log.get("1.0", "end").strip():
            self.toggle_log(True)

    def _update_queue_hint(self, running=None):
        """The action row only exists when it has something to say."""
        count = len(self.queue_items())
        if count:
            self.queue_hint.configure(text=f"{count} in queue")
            self.start_button.pack(side="left")
            if not self.queue_hint.winfo_manager():
                self.queue_hint.pack(side="left", padx=(10, 0))
        else:
            self.queue_hint.configure(text="")
            self.start_button.pack_forget()
            self.queue_hint.pack_forget()

        if running is None:
            running = bool(self.worker and self.worker.is_alive())
        if count or running:
            if not self._actions_row.winfo_manager():
                self._actions_row.pack(fill="x", pady=(12, 0))
        else:
            self._actions_row.pack_forget()

    def _refresh_queue_ui(self):
        count = len(self.queue_items())
        if count:
            self.queue_count.configure(text=f"{count} link(s) queued")
            if not self.queue_frame.winfo_manager():
                self.queue_frame.pack(fill="x")
            # Show the queue panel so the user sees what was added.
            if not self.settings_visible:
                self.toggle_settings(True)
        else:
            self.queue_count.configure(text="")
        self._update_queue_hint()

    # ── small UI actions ─────────────────────────────────────────────

    def _update_mode_help(self):
        mode = self.mode_var.get()
        self.mode_help.configure(text=MODE_HELP.get(mode, ""))

    def _selected_height(self):
        return QUALITY_CHOICES[self.quality_combo.current()][1]

    def choose_output(self):
        chosen = filedialog.askdirectory(initialdir=self.output_var.get() or None)
        if chosen:
            self.output_var.set(chosen)

    def open_output(self):
        path = self.output_var.get()
        if not os.path.isdir(path):
            messagebox.showwarning("DownY", "That folder does not exist yet.")
            return
        try:
            os.startfile(path)  # noqa: S606 - Windows shell open
        except (AttributeError, OSError):
            messagebox.showinfo("DownY", path)

    def append_log(self, text, tag="info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        # Grow the box with its content, up to a readable maximum, so a
        # three-line log does not leave a tall empty rectangle.
        lines = int(self.log.index("end-1c").split(".")[0])
        wanted = max(4, min(lines, 12))
        if int(self.log.cget("height")) != wanted:
            self.log.configure(height=wanted)
            self._autosize()
        if not self._log_has_content:
            self._log_has_content = True
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
        self._refresh_queue_ui()

    def remove_selected(self):
        for index in reversed(self.queue_list.curselection()):
            self.queue_list.delete(index)
        self._refresh_queue_ui()

    def clear_queue(self):
        self.queue_list.delete(0, "end")
        self._refresh_queue_ui()

    def load_links_file(self):
        from .cli import read_queue
        path = Settings().link_file
        urls = read_queue(path)
        if not urls:
            messagebox.showinfo("DownY", f"No URLs found in {path}")
            return
        for url in urls:
            self.queue_list.insert("end", url)
        self.append_log(f"Loaded {len(urls)} URL(s) from links.txt")
        self._refresh_queue_ui()

    def paste_list(self):
        win = tk.Toplevel(self.root)
        win.title("Paste links")
        win.geometry("560x320")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="Paste any number of links, one per line.",
                  style="Muted.TLabel").pack(anchor="w", padx=12, pady=(12, 6))
        text = tk.Text(win, wrap="none", font=FONT_MONO, bd=1, relief="solid",
                       highlightthickness=0)
        text.pack(fill="both", expand=True, padx=12)
        text.focus_set()

        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=12, pady=12)

        def accept(_event=None):
            urls = engine_split_urls(text.get("1.0", "end"))
            for url in urls:
                self.queue_list.insert("end", url)
            win.destroy()
            self._refresh_queue_ui()

        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Add to queue", command=accept).pack(
            side="right", padx=(0, 6))

    # ── run control ──────────────────────────────────────────────────

    def _make_settings(self):
        return Settings(
            mode=self.mode_var.get(),
            output_dir=self.output_var.get(),
            max_height=self._selected_height(),
            use_archive=bool(self.archive_var.get()),
            cookies_from_browser=self.cookies_var.get().strip() or None,
        )

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.single_button.configure(state=state)
        self.add_button.configure(state=state)
        self.queue_list.configure(state="disabled" if running else "normal")

        # Cancel is meaningless when idle; showing it greyed out is just a
        # dead control. It appears only while something is actually running.
        if running:
            self.cancel_button.configure(state="normal")
            if not self.cancel_button.winfo_manager():
                # Anchor before the hint only when the hint is actually shown.
                if self.queue_hint.winfo_manager():
                    self.cancel_button.pack(side="left", before=self.queue_hint)
                else:
                    self.cancel_button.pack(side="left")
        else:
            self.cancel_button.configure(state="disabled")
            self.cancel_button.pack_forget()

        self.root.configure(cursor="watch" if running else "")
        self._update_queue_hint(running=running)
        self._autosize()

    def _preflight(self, settings):
        if not engine.find_ffmpeg(settings) or not engine.find_ffprobe(settings):
            messagebox.showerror(
                "DownY", "ffmpeg/ffprobe not found.\n"
                         "Run runner.bat to install the dependencies.")
            return False
        return True

    def download_single(self):
        urls = engine_split_urls(self.url_var.get())
        if not urls:
            messagebox.showinfo("DownY", "Paste a video link first.")
            return
        if len(urls) > 1:
            # Being helpful beats an error: quietly queue them all instead.
            for url in urls:
                self.queue_list.insert("end", url)
            self.url_var.set("")
            self._refresh_queue_ui()
            self.start_queue()
            return
        self._start([urls[0]])
        self.url_var.set("")

    def start_queue(self):
        urls = self.queue_items()
        if not urls:
            messagebox.showinfo("DownY", "The queue is empty.")
            return
        self._start(urls)

    def _start(self, urls):
        if self.worker and self.worker.is_alive():
            return
        settings = self._make_settings()
        if not self._preflight(settings):
            return
        self._save_settings()

        self.bridge.reset()
        self._set_running(True)
        self._set_progress_visible(True)
        self.progress.configure(value=0)
        self.status_var.set(f"Starting {len(urls)} download(s)…")
        self._show_detail("")
        self.current_output = None
        self.append_log(f"Starting {len(urls)} item(s), mode={settings.mode}, "
                        f"quality={settings.max_height or 'best'}")

        bridge = self.bridge

        def work():
            settings.ensure_dirs()
            return engine.run_batch(
                urls, settings,
                emit=bridge.emit,
                should_stop=lambda: bridge.cancelled,
            )

        self.worker = Worker(work, bridge)
        self.worker.start()

    def cancel(self):
        if self.worker and self.worker.is_alive():
            self.bridge.request_cancel()
            self.status_var.set("Cancelling…")
            self.append_log("Cancel requested.", "error")

    # ── event pump ───────────────────────────────────────────────────

    def _poll(self):
        try:
            self.bridge.pump(self._handle_event)
            self._poll_id = self.root.after(POLL_MS, self._poll)
        except tk.TclError:
            self._poll_id = None

    def destroy(self):
        """Stop the pump timer so Tk does not warn about a dead callback."""
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
        self.status_var.set(f"[{d['index']}/{d['total']}] Preparing…")
        self.progress.configure(value=0)

    def _on_probe(self, d):
        self.append_log(f"[{d['index']}] {d['title']}")
        self.append_log(f"    video: {d['video']}", "muted")
        if d.get("audio"):
            self.append_log(f"    audio: {d['audio']}", "muted")

    def _on_progress(self, d):
        pct = d.get("percent", "").replace("%", "").strip()
        try:
            value = float(pct)
        except ValueError:
            value = 0.0
        self._set_progress_visible(True)
        self.progress.configure(value=value)
        bits = [f"{d['label']} {d.get('percent', '')}"]
        if d.get("speed"):
            bits.append(d["speed"])
        if d.get("eta"):
            bits.append(f"ETA {d['eta']}")
        self._show_detail("   ".join(bits))
        self.status_var.set(f"[{d['index']}] Downloading…")

    def _on_stream_done(self, d):
        self.append_log(f"    {d['label']} {d['size']} done", "muted")

    def _on_retry(self, d):
        self.append_log(f"    {d['label']} interrupted; retry "
                        f"{d['attempt']}/{d['total'] - 1} in {d['wait']}s", "error")

    def _on_warning(self, d):
        message = d.get("message", "")
        if message:
            self.append_log(f"    warning: {message}", "error")

    def _on_merged(self, d):
        self.progress.configure(value=100)

    def _on_item_ok(self, d):
        report = d["report"]
        self.current_output = report.get("path")
        self.append_log(
            f"    done {report['resolution']} @ {report['fps']}fps "
            f"{report['video_codec']}/{report['audio_codec']}", "ok")
        self.append_log(f"    saved: {os.path.basename(report['path'])}", "muted")

    def _on_item_failed(self, d):
        self.append_log(f"    failed: {d['reason']}", "error")

    def _on_item_skipped(self, d):
        self.append_log(f"[{d['index']}] already downloaded — skipped", "muted")

    def _on_recycle_pruned(self, d):
        self.append_log(f"Pruned {d['removed']} old file(s), freed {d['freed']}",
                        "muted")

    def _on_orphans_removed(self, d):
        self.append_log(f"Removed {d['removed']} partial file(s), "
                        f"freed {d['freed']}", "muted")

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

        text = ("Cancelled: " if cancelled else "Done: ") + ", ".join(parts)
        self.status_var.set(text)
        self._show_detail("")
        self.append_log(text, "error" if (failed or cancelled) else "ok")

    def _on_worker_cancelled(self, d):
        self._finish()
        self.status_var.set("Cancelled.")
        self._show_detail("")
        self.append_log("Cancelled by user.", "error")

    def _on_worker_error(self, d):
        self._finish()
        exc = d.get("error")
        reason = engine.classify_error(exc) if exc else "unknown error"
        self.status_var.set(f"Stopped: {reason}")
        self.append_log(f"Stopped: {reason}", "error")
        messagebox.showerror("DownY", f"The run stopped:\n\n{reason}")

    def _finish(self):
        self._set_running(False)
        self.worker = None
        # Swap the frozen progress bar for a plain summary line, so the
        # outcome stays readable without leaving a dead bar behind.
        self.progress.pack_forget()
        self.detail_label.pack_forget()
        if not self.status_label.winfo_manager():
            self.status_label.pack(anchor="w", pady=(12, 0))
        self._autosize()


def engine_split_urls(text):
    """URLs out of pasted text -- same rules as the CLI."""
    return extract_urls(text)


def main():
    root = tk.Tk()
    app = DownyApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

