import os
import threading
import webbrowser
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox
from pathlib import Path
from dotenv import load_dotenv, set_key
from sentence_transformers import SentenceTransformer
import duplicate


DEFAULT_JIRA_URL = "https://strive.devops.t-systems.net/jira"
DEFAULT_JIRA_PROJECT_KEY = "AIO"


class JiraDuplicateGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Jira Duplicate Finder")
        self.root.geometry("1200x760")
        self.root.minsize(980, 620)

        # Config paths
        self.env_path = Path(".env")
        load_dotenv(self.env_path)

        # Variables
        self.jira_url = tk.StringVar(value=os.getenv("JIRA_URL", DEFAULT_JIRA_URL))
        self.jira_token = tk.StringVar(value=os.getenv("JIRA_TOKEN", ""))
        self.jira_project = tk.StringVar(value=os.getenv("JIRA_PROJECT_KEY", DEFAULT_JIRA_PROJECT_KEY))
        self.title_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.force_refresh_cache = tk.BooleanVar(value=False)
        self.include_done_issues = tk.BooleanVar(value=False)
        self.last_fetch_var = tk.StringVar(value="Last fetch: never")
        self._auto_forced_refresh = False
        self._spinner_after_id = None
        self._spinner_phase = 0
        self._spinner_base_text = ""
        
        self.model = None
        self.vectors = None
        self.metadata = None
        
        self._setup_ui()
        self._sync_force_refresh_state()
        self._update_last_fetch_label()

    def _setup_ui(self):
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Split view: compact controls on the left, wide results on the right.
        content_pane = ttk.Panedwindow(main_frame, orient=tk.HORIZONTAL)
        content_pane.pack(fill=tk.BOTH, expand=True)

        left_panel = ttk.Frame(content_pane)
        right_panel = ttk.Frame(content_pane)
        content_pane.add(left_panel, weight=1)
        content_pane.add(right_panel, weight=2)

        # --- Connection Settings ---
        settings_frame = ttk.LabelFrame(left_panel, text="Jira Connection", padding="10")
        settings_frame.pack(fill=tk.X, pady=(0, 10))

        # URL
        ttk.Label(settings_frame, text="Jira URL:").grid(row=0, column=0, sticky=tk.W, padx=(5, 8), pady=3)
        ttk.Entry(settings_frame, textvariable=self.jira_url, width=48).grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)
        
        # Project Key
        ttk.Label(settings_frame, text="Project Key:").grid(row=1, column=0, sticky=tk.W, padx=(5, 8), pady=3)
        ttk.Entry(settings_frame, textvariable=self.jira_project, width=18).grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)

        # Token
        ttk.Label(settings_frame, text="Token / PAT:").grid(row=2, column=0, sticky=tk.W, padx=(5, 8), pady=3)
        self.token_entry = ttk.Entry(settings_frame, textvariable=self.jira_token, show="*", width=48)
        self.token_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=3)

        settings_frame.columnconfigure(1, weight=1)

        # Save settings button
        ttk.Button(settings_frame, text="Save Settings", command=self._save_settings).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky=tk.EW,
            pady=(8, 2)
        )

        # --- Issue Input ---
        input_frame = ttk.LabelFrame(left_panel, text="New Issue Details", padding="10")
        input_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 10))

        ttk.Label(input_frame, text="Title:").pack(fill=tk.X)
        ttk.Entry(input_frame, textvariable=self.title_var).pack(fill=tk.X, pady=(0, 5))

        ttk.Label(input_frame, text="Description:").pack(fill=tk.X)
        self.desc_text = tk.Text(input_frame, height=16, font=("TkDefaultFont", 10))
        self.desc_text.pack(fill=tk.BOTH, expand=True)

        # --- Actions ---
        btn_frame = ttk.Frame(left_panel)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        self.force_refresh_check = ttk.Checkbutton(
            btn_frame,
            text="Force refresh cache",
            variable=self.force_refresh_cache
        )
        self.force_refresh_check.grid(row=0, column=0, sticky=tk.W, pady=(0, 4))

        ttk.Label(
            btn_frame,
            textvariable=self.last_fetch_var,
            font=("TkDefaultFont", 8),
            foreground="#334155"
        ).grid(row=1, column=0, sticky=tk.W, padx=(24, 0), pady=(0, 8))

        ttk.Separator(btn_frame, orient="horizontal").grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(2, 8))

        ttk.Checkbutton(
            btn_frame,
            text="Include closed (done) issues in results",
            variable=self.include_done_issues
        ).grid(row=3, column=0, sticky=tk.W, pady=(0, 6))

        self.check_btn = ttk.Button(btn_frame, text="Check for Duplicates", command=self._start_check)
        self.check_btn.grid(row=5, column=0, sticky=tk.EW, pady=(6, 0))

        ttk.Separator(btn_frame, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky=tk.EW, pady=(2, 6))

        ttk.Label(
            btn_frame,
            text="Warning: This will remove the cached index\nand force a complete refresh on next search,\nwhich may take significantly longer.",
            foreground="#b45309",
            justify=tk.LEFT,
            wraplength=370,
            anchor="w"
        ).grid(row=0, column=1, rowspan=3, sticky=tk.NW, padx=(14, 0), pady=(0, 2))

        ttk.Label(btn_frame, textvariable=self.status_var, font=("TkDefaultFont", 9, "italic")).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=(8, 0)
        )

        # --- Results ---
        results_frame = ttk.LabelFrame(right_panel, text="Similar Issues Found", padding="10")
        results_frame.pack(fill=tk.BOTH, expand=True)

        # Scrollable list for results
        self.results_canvas = tk.Canvas(results_frame, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=self.results_canvas.yview)
        self.results_scrollable_frame = ttk.Frame(self.results_canvas)

        self.results_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.results_canvas.configure(
                scrollregion=self.results_canvas.bbox("all")
            )
        )

        self.results_canvas.bind(
            "<Configure>",
            lambda e: self.results_canvas.itemconfigure(self._results_window, width=e.width)
        )

        self._results_window = self.results_canvas.create_window((0, 0), window=self.results_scrollable_frame, anchor="nw")
        self.results_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.results_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def _save_settings(self):
        """Persist settings to .env file"""
        if not self.env_path.exists():
            self.env_path.touch()
        
        try:
            set_key(str(self.env_path), "JIRA_URL", self.jira_url.get())
            set_key(str(self.env_path), "JIRA_TOKEN", self.jira_token.get())
            set_key(str(self.env_path), "JIRA_PROJECT_KEY", self.jira_project.get())
            self.status_var.set("Settings saved to .env")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save settings: {e}")

    def _start_check(self):
        title = self.title_var.get().strip()
        description = self.desc_text.get("1.0", tk.END).strip()

        if not title:
            messagebox.showwarning("Warning", "Please provide at least a title.")
            return

        if not self.jira_url.get() or not self.jira_token.get():
            messagebox.showwarning("Warning", "Jira URL and Token are required.")
            return

        # Disable button and show loading
        self.check_btn.state(['disabled'])
        self._set_status("Starting duplicate check...")
        
        # Clear previous results
        for widget in self.results_scrollable_frame.winfo_children():
            widget.destroy()

        # Run in thread
        threading.Thread(target=self._perform_check, args=(title, description), daemon=True).start()

    def _format_fetch_timestamp(self, timestamp: str | None) -> str:
        if not timestamp:
            return "Last fetch: never"

        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            local_dt = parsed.astimezone()
            return f"Last fetch: {local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        except Exception:
            return "Last fetch: unknown"

    def _update_last_fetch_label(self, timestamp: str | None = None):
        if timestamp is None:
            timestamp = duplicate.get_cache_last_fetch()
        self.last_fetch_var.set(self._format_fetch_timestamp(timestamp))

    def _set_status(self, text: str):
        self.status_var.set(text)

    def _spinner_tick(self):
        dots = "." * (self._spinner_phase % 4)
        self.status_var.set(f"{self._spinner_base_text}{dots}")
        self._spinner_phase += 1
        self._spinner_after_id = self.root.after(350, self._spinner_tick)

    def _start_spinner(self, base_text: str):
        self._stop_spinner()
        self._spinner_base_text = base_text
        self._spinner_phase = 0
        self._spinner_tick()

    def _stop_spinner(self):
        if self._spinner_after_id is not None:
            self.root.after_cancel(self._spinner_after_id)
            self._spinner_after_id = None

    def _handle_backend_progress(self, stage: str, percent: float | None):
        # Any determinate update should stop spinner-based animation.
        self._stop_spinner()

        if stage == "cache_hit":
            self._set_status("Using cached index (100%)")
            return

        if stage == "cache_miss":
            self._set_status("Cache missing or stale, rebuilding index...")
            return

        if stage == "fetch":
            if percent is None:
                self._set_status("Fetching Jira issues...")
            else:
                self._set_status(f"Fetching Jira issues... {int(round(percent))}%")
            return

        if stage == "embed":
            if percent is None:
                self._set_status("Building semantic index...")
            else:
                self._set_status(f"Building semantic index... {int(round(percent))}%")
            return

        self._set_status("Processing...")

    def _sync_force_refresh_state(self):
        cache_exists = duplicate.CACHE_FILE.exists()

        if not cache_exists:
            self.force_refresh_cache.set(True)
            self._auto_forced_refresh = True
            self.force_refresh_check.state(["disabled"])
        else:
            self.force_refresh_check.state(["!disabled"])
            if self._auto_forced_refresh:
                self.force_refresh_cache.set(False)
                self._auto_forced_refresh = False

    def _perform_check(self, title, description):
        try:
            def progress_callback(stage: str, percent: float | None):
                self.root.after(0, lambda s=stage, p=percent: self._handle_backend_progress(s, p))

            cfg = duplicate.Config(
                jira_url=self.jira_url.get(),
                jira_token=self.jira_token.get(),
                jira_project_key=self.jira_project.get(),
                jira_user=None,
                model_name=duplicate.DEFAULT_MODEL,
                top_k=5,
                min_score=0.4, # More lenient for UI
                refresh_cache=self.force_refresh_cache.get(),
                build_only=False,
                exclude_done=False,
                issue_file="",
                title=title,
                description=description
            )

            if self.model is None:
                self.root.after(0, lambda: self._start_spinner("Initializing AI model"))
                self.model = SentenceTransformer(cfg.model_name)
                self.root.after(0, self._stop_spinner)
            
            self.root.after(0, lambda: self._set_status("Preparing index..."))
            self.vectors, self.metadata = duplicate.build_or_load_index(
                cfg,
                self.model,
                progress_callback=progress_callback,
            )
            last_fetch = duplicate.get_cache_last_fetch()
            self.root.after(0, lambda ts=last_fetch: self._update_last_fetch_label(ts))
            self.root.after(0, self._sync_force_refresh_state)

            self.root.after(0, lambda: self._start_spinner("Comparing issues"))
            results = duplicate.rank_similar(
                model=self.model,
                vectors=self.vectors,
                metadata=self.metadata,
                title=title,
                description=description,
                top_k=5,
                min_score=cfg.min_score,
                include_done=self.include_done_issues.get()
            )
            self.root.after(0, self._stop_spinner)

            self.root.after(0, lambda: self._display_results(results))
        except Exception as e:
            self.root.after(0, self._stop_spinner)
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.root.after(0, lambda: self._set_status("Error occurred"))
        finally:
            self.root.after(0, self._stop_spinner)
            self.root.after(0, lambda: self.check_btn.state(['!disabled']))

    def _display_results(self, results):
        if not results:
            ttk.Label(self.results_scrollable_frame, text="No similar issues found.").pack(pady=20)
            self.status_var.set("Search complete - No duplicates")
            return

        self.status_var.set(f"Search complete - Found {len(results)} potential duplicates")
        summary_wrap = max(520, self.results_canvas.winfo_width() - 80)

        for item in results:
            frame = ttk.Frame(self.results_scrollable_frame, padding=5)
            frame.pack(fill=tk.X, expand=True, pady=2)
            
            # Score and Key
            score_pct = item['score'] * 100
            header_text = f"[{item['confidence'].upper()} {score_pct:.1f}%] {item['key']}"
            
            # Link/Key
            link = ttk.Label(frame, text=header_text, foreground="blue", cursor="hand2", font=("TkDefaultFont", 10, "bold"))
            link.pack(anchor=tk.W)
            link.bind("<Button-1>", lambda e, url=item['url']: webbrowser.open_new(url))

            # Summary
            ttk.Label(frame, text=item['summary'], wraplength=summary_wrap, justify=tk.LEFT).pack(anchor=tk.W, padx=10)
            
            # Metadata
            meta_text = f"Status: {item['status']} | Type: {item['issue_type']} | Updated: {item['updated']}"
            ttk.Label(frame, text=meta_text, font=("TkDefaultFont", 8, "italic")).pack(anchor=tk.W, padx=10)
            
            ttk.Separator(self.results_scrollable_frame, orient="horizontal").pack(fill=tk.X, pady=5)

if __name__ == "__main__":
    root = tk.Tk()
    app = JiraDuplicateGUI(root)
    root.mainloop()
