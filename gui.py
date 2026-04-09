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

class JiraDuplicateGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Jira Duplicate Finder")
        self.root.geometry("800x700")
        self.root.minsize(600, 500)

        # Config paths
        self.env_path = Path(".env")
        load_dotenv(self.env_path)

        # Variables
        self.jira_url = tk.StringVar(value=os.getenv("JIRA_URL", ""))
        self.jira_token = tk.StringVar(value=os.getenv("JIRA_TOKEN", ""))
        self.jira_project = tk.StringVar(value=os.getenv("JIRA_PROJECT_KEY", ""))
        self.jira_user = tk.StringVar(value=os.getenv("JIRA_USER", ""))
        self.title_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.force_refresh_cache = tk.BooleanVar(value=False)
        self.last_fetch_var = tk.StringVar(value="Last fetch: never")
        
        self.model = None
        self.vectors = None
        self.metadata = None
        
        self._setup_ui()
        self._update_last_fetch_label()

    def _setup_ui(self):
        # Main container with padding
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Connection Settings ---
        settings_frame = ttk.LabelFrame(main_frame, text="Jira Connection", padding="10")
        settings_frame.pack(fill=tk.X, pady=(0, 10))

        # URL
        ttk.Label(settings_frame, text="Jira URL:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(settings_frame, textvariable=self.jira_url).grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
        
        # Project Key
        ttk.Label(settings_frame, text="Project Key:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(settings_frame, textvariable=self.jira_project).grid(row=0, column=3, sticky=tk.EW, padx=5, pady=2)

        # Token
        ttk.Label(settings_frame, text="Token / PAT:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.token_entry = ttk.Entry(settings_frame, textvariable=self.jira_token, show="*")
        self.token_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)

        # User (optional)
        ttk.Label(settings_frame, text="User (optional):").grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)
        ttk.Entry(settings_frame, textvariable=self.jira_user).grid(row=1, column=3, sticky=tk.EW, padx=5, pady=2)

        settings_frame.columnconfigure(1, weight=1)
        settings_frame.columnconfigure(3, weight=1)

        # Save settings button
        ttk.Button(settings_frame, text="Save Settings", command=self._save_settings).grid(row=2, column=0, columnspan=4, pady=5)

        # --- Issue Input ---
        input_frame = ttk.LabelFrame(main_frame, text="New Issue Details", padding="10")
        input_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 10))

        ttk.Label(input_frame, text="Title:").pack(fill=tk.X)
        ttk.Entry(input_frame, textvariable=self.title_var).pack(fill=tk.X, pady=(0, 5))

        ttk.Label(input_frame, text="Description:").pack(fill=tk.X)
        self.desc_text = tk.Text(input_frame, height=8, font=("TkDefaultFont", 10))
        self.desc_text.pack(fill=tk.BOTH, expand=True)

        # --- Actions ---
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        self.check_btn = ttk.Button(btn_frame, text="Check for Duplicates", command=self._start_check)
        self.check_btn.pack(side=tk.LEFT, padx=5)

        ttk.Checkbutton(
            btn_frame,
            text="Force refresh cache",
            variable=self.force_refresh_cache
        ).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Label(
            btn_frame,
            text="Warning: This can take some time!",
            foreground="#b45309"
        ).pack(side=tk.LEFT, padx=(8, 0))
        
        ttk.Label(btn_frame, textvariable=self.status_var, font=("TkDefaultFont", 9, "italic")).pack(side=tk.LEFT, padx=10)

        ttk.Label(main_frame, textvariable=self.last_fetch_var, font=("TkDefaultFont", 9)).pack(fill=tk.X, pady=(0, 10))

        # --- Results ---
        results_frame = ttk.LabelFrame(main_frame, text="Similar Issues Found", padding="10")
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

        self.results_canvas.create_window((0, 0), window=self.results_scrollable_frame, anchor="nw")
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
            set_key(str(self.env_path), "JIRA_USER", self.jira_user.get())
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
        self.status_var.set("Loading model and fetching issues...")
        
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

    def _perform_check(self, title, description):
        try:
            cfg = duplicate.Config(
                jira_url=self.jira_url.get(),
                jira_token=self.jira_token.get(),
                jira_project_key=self.jira_project.get(),
                jira_user=self.jira_user.get() if self.jira_user.get() else None,
                model_name=duplicate.DEFAULT_MODEL,
                top_k=5,
                min_score=0.4, # More lenient for UI
                refresh_cache=self.force_refresh_cache.get(),
                build_only=False,
                exclude_done=True,
                issue_file="",
                title=title,
                description=description
            )

            if self.model is None:
                self.root.after(0, lambda: self.status_var.set("Initializing AI model..."))
                self.model = SentenceTransformer(cfg.model_name)
            
            self.root.after(0, lambda: self.status_var.set("Fetching/Indexing Jira issues..."))
            self.vectors, self.metadata = duplicate.build_or_load_index(cfg, self.model)
            last_fetch = duplicate.get_cache_last_fetch()
            self.root.after(0, lambda ts=last_fetch: self._update_last_fetch_label(ts))

            self.root.after(0, lambda: self.status_var.set("Comparing issues..."))
            results = duplicate.rank_similar(
                model=self.model,
                vectors=self.vectors,
                metadata=self.metadata,
                title=title,
                description=description,
                top_k=5,
                min_score=cfg.min_score
            )

            self.root.after(0, lambda: self._display_results(results))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.root.after(0, lambda: self.status_var.set("Error occurred"))
        finally:
            self.root.after(0, lambda: self.check_btn.state(['!disabled']))

    def _display_results(self, results):
        if not results:
            ttk.Label(self.results_scrollable_frame, text="No similar issues found.").pack(pady=20)
            self.status_var.set("Search complete - No duplicates")
            return

        self.status_var.set(f"Search complete - Found {len(results)} potential duplicates")

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
            ttk.Label(frame, text=item['summary'], wraplength=700).pack(anchor=tk.W, padx=10)
            
            # Metadata
            meta_text = f"Status: {item['status']} | Type: {item['issue_type']} | Updated: {item['updated']}"
            ttk.Label(frame, text=meta_text, font=("TkDefaultFont", 8, "italic")).pack(anchor=tk.W, padx=10)
            
            ttk.Separator(self.results_scrollable_frame, orient="horizontal").pack(fill=tk.X, pady=5)

if __name__ == "__main__":
    root = tk.Tk()
    app = JiraDuplicateGUI(root)
    root.mainloop()
