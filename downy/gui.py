"""Desktop front-end (tkinter, no extra dependencies).

The window is deliberately thin: it gathers Settings, calls
``engine.run_batch`` on a worker thread, and renders the events that come
back. All policy -- format ranking, naming, merging, pruning -- stays in
the engine, so this module contains no yt-dlp or ffmpeg knowledge.

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

MODE_HELP = {
    "best": "Highest resolution; AV1/VP9 allowed. Saved as MKV.",
    "efficient": "Highest resolution, preferring smaller AV1 encodes. MKV.",
    "compat": "H.264 + AAC first -- plays in the most players. MP4.",
}

POLL_MS = 80


class DownyApp:
    def __init__(self, root):
        self.root = root
        self.bridge = EventBridge()
        self.worker = None
        self.cancel_button = None
        self.current_output = None

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
            "max_height": self.quality_var.get() or None,
            "output_dir": self.output_var.get(),
            "use_archive": bool(self.archive_var.get()),
            "cookies_from_browser": self.cookies_var.get().strip() or None,
        })
        try:
            write_config(self._config_path(), data)
        except OSError:
            pass

    # ── layout ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.title(f"DownY {__version__}")
        self.root.geometry("920x780")
        self.root.minsize(760, 700)

        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        self._build_input(outer)
        self._build_options(outer)
        self._build_queue(outer)
        self._build_progress(outer)
        self._build_log(outer)
        self._build_actions(outer)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_input(self, parent):
        box = ttk.LabelFrame(parent, text="Video link", padding=8)
        box.pack(fill="x")

        row = ttk.Frame(box)
        row.pack(fill="x")

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(row, textvariable=self.url_var, font=("Segoe UI", 10))
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda _e: self.download_single())

        ttk.Button(row, text="Add to queue", command=self.add_to_queue).pack(
            side="left", padx=(6, 0))
        self.single_button = ttk.Button(
            row, text="Download now", command=self.download_single)
        self.single_button.pack(side="left", padx=(6, 0))

        hint = ttk.Label(
            box, foreground="#555",
            text="Paste one URL and press Enter to download it immediately.")
        hint.pack(anchor="w", pady=(6, 0))

    def _build_options(self, parent):
        box = ttk.LabelFrame(parent, text="Download settings", padding=8)
        box.pack(fill="x", pady=(8, 0))

        grid = ttk.Frame(box)
        grid.pack(fill="x")
        for col in (1, 3):
            grid.columnconfigure(col, weight=1)

        ttk.Label(grid, text="Quality:").grid(row=0, column=0, sticky="w")
        self.quality_var = tk.IntVar()
        saved_height = self.saved.get("max_height")
        labels = [label for label, _ in QUALITY_CHOICES]
        self.quality_combo = ttk.Combobox(
            grid, values=labels, state="readonly", width=18)
        self.quality_combo.current(
            next((i for i, (_, h) in enumerate(QUALITY_CHOICES)
                  if h == saved_height), 0))
        self.quality_combo.grid(row=0, column=1, sticky="w", padx=(6, 18))

        ttk.Label(grid, text="Mode:").grid(row=0, column=2, sticky="w")
        self.mode_var = tk.StringVar(value=self.saved.get("mode", "best"))
        mode_combo = ttk.Combobox(
            grid, textvariable=self.mode_var, values=sorted(selector.MODES),
            state="readonly", width=12)
        mode_combo.grid(row=0, column=3, sticky="w", padx=(6, 0))
        mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_mode_help())

        self.mode_help = ttk.Label(box, foreground="#555", text="")
        self.mode_help.pack(anchor="w", pady=(6, 0))
        self._update_mode_help()

        row2 = ttk.Frame(box)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Save to:").pack(side="left")
        self.output_var = tk.StringVar(
            value=self.saved.get("output_dir") or Settings().output_dir)
        # A long path must not widen the window: the entry alone shrinks.
        self.output_entry = ttk.Entry(row2, textvariable=self.output_var, width=20)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="Browse…", command=self.choose_output).pack(side="left")
        ttk.Button(row2, text="Open", command=self.open_output).pack(
            side="left", padx=(6, 0))

        row3 = ttk.Frame(box)
        row3.pack(fill="x", pady=(6, 0))
        self.archive_var = tk.IntVar(value=1 if self.saved.get("use_archive", True) else 0)
        ttk.Checkbutton(
            row3, text="Skip videos already downloaded",
            variable=self.archive_var).pack(side="left")
        ttk.Label(row3, text="   Browser cookies:").pack(side="left")
        self.cookies_var = tk.StringVar(
            value=self.saved.get("cookies_from_browser") or "")
        ttk.Combobox(
            row3, textvariable=self.cookies_var, width=12,
            values=["", "chrome", "firefox", "edge", "brave", "opera"],
        ).pack(side="left")

    def _build_queue(self, parent):
        box = ttk.LabelFrame(parent, text="Queue", padding=8)
        box.pack(fill="both", expand=False, pady=(8, 0))

        holder = ttk.Frame(box)
        holder.pack(fill="both", expand=True)

        self.queue_list = tk.Listbox(holder, height=5, activestyle="none")
        self.queue_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical",
                               command=self.queue_list.yview)
        scroll.pack(side="left", fill="y")
        self.queue_list.configure(yscrollcommand=scroll.set)

        side = ttk.Frame(holder)
        side.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(side, text="Remove", command=self.remove_selected).pack(fill="x")
        ttk.Button(side, text="Clear", command=self.clear_queue).pack(fill="x", pady=4)
        ttk.Button(side, text="Load links.txt",
                   command=self.load_links_file).pack(fill="x")
        ttk.Button(side, text="Paste list…", command=self.paste_list).pack(
            fill="x", pady=4)

    def _build_progress(self, parent):
        box = ttk.Frame(parent)
        box.pack(fill="x", pady=(8, 0))

        self.status_var = tk.StringVar(value="Idle.")
        ttk.Label(box, textvariable=self.status_var,
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.progress = ttk.Progressbar(box, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=4)

        self.detail_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.detail_var,
                  foreground="#333").pack(anchor="w")

    def _build_log(self, parent):
        box = ttk.LabelFrame(parent, text="Activity", padding=6)
        box.pack(fill="both", expand=True, pady=(8, 0))

        self.log = tk.Text(box, height=8, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        scroll.pack(side="left", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.tag_configure("error", foreground="#b00020")
        self.log.tag_configure("ok", foreground="#106b21")
        self.log.tag_configure("info", foreground="#333333")

    def _build_actions(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(8, 0))
        self.start_button = ttk.Button(
            row, text="Start queue", command=self.start_queue)
        self.start_button.pack(side="left")
        self.cancel_button = tk.Button(
            row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Clear activity", command=self.clear_log).pack(
            side="right")

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

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def queue_items(self):
        return list(self.queue_list.get(0, "end"))

    def add_to_queue(self):
        for url in engine_split_urls(self.url_var.get()):
            self.queue_list.insert("end", url)
        self.url_var.set("")

    def remove_selected(self):
        for index in reversed(self.queue_list.curselection()):
            self.queue_list.delete(index)

    def clear_queue(self):
        self.queue_list.delete(0, "end")

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

    def paste_list(self):
        win = tk.Toplevel(self.root)
        win.title("Paste URLs")
        win.geometry("520x300")
        win.transient(self.root)
        text = tk.Text(win, wrap="none", font=("Consolas", 9))
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.focus_set()

        def accept():
            for url in engine_split_urls(text.get("1.0", "end")):
                self.queue_list.insert("end", url)
            win.destroy()

        ttk.Button(win, text="Add", command=accept).pack(pady=(0, 8))

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
        self.cancel_button.configure(state="normal" if running else "disabled")
        self.root.configure(cursor="watch" if running else "")

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
            messagebox.showinfo(
                "DownY", "That is more than one link -- use Add to queue.")
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
        self.progress.configure(value=0)
        self.status_var.set(f"Starting {len(urls)} download(s)…")
        self.detail_var.set("")
        self.current_output = None
        self.append_log(f"── Starting {len(urls)} item(s), mode={settings.mode}, "
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
            self.status_var.set("Cancelling after the current item…")
            self.append_log("Cancel requested.", "error")

    # ── event pump ───────────────────────────────────────────────────

    def _poll(self):
        try:
            self.bridge.pump(self._handle_event)
            self._poll_id = self.root.after(POLL_MS, self._poll)
        except tk.TclError:
            # The window is going away; stop rescheduling.
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
        self.status_var.set(
            f"[{d['index']}/{d['total']}] Preparing…")
        self.progress.configure(value=0)

    def _on_probe(self, d):
        self.append_log(f"[{d['index']}] {d['title']}")
        self.append_log(f"      video: {d['video']}")
        if d.get("audio"):
            self.append_log(f"      audio: {d['audio']}")

    def _on_progress(self, d):
        pct = d.get("percent", "").replace("%", "").strip()
        try:
            value = float(pct)
        except ValueError:
            value = 0.0
        self.progress.configure(value=value)
        bits = [f"{d['label']} {d.get('percent', '')}"]
        if d.get("speed"):
            bits.append(d["speed"])
        if d.get("eta"):
            bits.append(f"ETA {d['eta']}")
        self.detail_var.set("   ".join(bits))
        self.status_var.set(f"[{d['index']}] Downloading…")

    def _on_stream_done(self, d):
        self.append_log(f"      {d['label']} {d['size']} done")

    def _on_retry(self, d):
        self.append_log(f"      {d['label']} interrupted; retry "
                        f"{d['attempt']}/{d['total'] - 1} in {d['wait']}s", "error")

    def _on_warning(self, d):
        message = d.get("message", "")
        if message:
            self.append_log(f"      warn: {message}", "error")

    def _on_merged(self, d):
        self.progress.configure(value=100)

    def _on_item_ok(self, d):
        report = d["report"]
        self.current_output = report.get("path")
        self.append_log(
            f"      OK {report['resolution']} @ {report['fps']}fps "
            f"{report['video_codec']}/{report['audio_codec']} "
            f"({report['duration']}s)", "ok")
        self.append_log(f"      saved: {os.path.basename(report['path'])}")

    def _on_item_failed(self, d):
        self.append_log(f"      FAILED: {d['reason']}", "error")
        detail = (d.get("detail") or "").splitlines()
        if detail:
            self.append_log(f"        {detail[0][:160]}")

    def _on_item_skipped(self, d):
        self.append_log(f"[{d['index']}] already downloaded -- skipped")

    def _on_recycle_pruned(self, d):
        self.append_log(f"Pruned {d['removed']} old file(s), freed {d['freed']}")

    def _on_orphans_removed(self, d):
        self.append_log(f"Removed {d['removed']} partial file(s), freed {d['freed']}")

    def _on_worker_done(self, d):
        summary = d.get("result") or {}
        self._finish()
        completed = summary.get("completed", 0)
        failed = summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        pending = summary.get("pending", 0)
        abandoned = summary.get("abandoned", 0)
        cancelled = summary.get("cancelled", False)

        parts = [f"{completed} completed"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        if abandoned:
            parts.append(f"{abandoned} interrupted")
        if pending:
            parts.append(f"{pending} not started")

        text = ("Cancelled" if cancelled else "Finished") + ": " + ", ".join(parts)
        self.status_var.set(text)
        self.detail_var.set("")
        self.append_log(text, "error" if (failed or cancelled) else "ok")

    def _on_worker_cancelled(self, d):
        self._finish()
        self.status_var.set("Cancelled.")
        self.detail_var.set("")
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


def engine_split_urls(text):
    """URLs out of pasted text -- same rules as the CLI."""
    return extract_urls(text)


def main():
    root = tk.Tk()
    DownyApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
