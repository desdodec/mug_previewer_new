"""Small Tk front end for the resumable preprocessing workflow."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable

from ..datasets.discovery import discover_datasets
from ..datasets.models import Dataset
from ..preprocess import INDEX_FILENAME, PreprocessProgress, PreprocessSummary, preprocess_datasets


def default_output_path(dataset_root: Path | str) -> Path:
    return Path(dataset_root) / "svg_previews"


@dataclass(frozen=True)
class WorkerFinished:
    summary: PreprocessSummary | None
    error: Exception | None


class PreprocessWorker:
    """Background runner which only sends data through a thread-safe queue."""

    def __init__(self, runner: Callable[..., PreprocessSummary] = preprocess_datasets) -> None:
        self.runner = runner
        self.events: queue.Queue[PreprocessProgress | WorkerFinished] = queue.Queue()

    def start(self, datasets: Iterable[Dataset], output: Path, *, street_ids: Iterable[str] | None = None) -> None:
        def run() -> None:
            try:
                summary = self.runner(tuple(datasets), output, street_ids=street_ids, progress=self.events.put)
            except Exception as error:  # Index/dataset failures belong in the concise final UI message.
                self.events.put(WorkerFinished(None, error))
            else:
                self.events.put(WorkerFinished(summary, None))

        threading.Thread(target=run, daemon=True).start()

    def drain(self) -> list[PreprocessProgress | WorkerFinished]:
        events: list[PreprocessProgress | WorkerFinished] = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                return events


class PreprocessUiApp:
    def __init__(self, root: tk.Tk, *, dataset_root: Path | None = None) -> None:
        self.root = root
        self.root.title("Mug Previewer Preprocessing")
        self.root.minsize(590, 410)
        self.worker: PreprocessWorker | None = None
        self.dataset_root_var = tk.StringVar(value=str(dataset_root) if dataset_root else "")
        self.output_var = tk.StringVar(value=str(default_output_path(dataset_root)) if dataset_root else "")
        self.scope_var = tk.StringVar(value="All datasets")
        self.progress_var = tk.StringVar(value="Ready")
        self.counts_var = tk.StringVar(value="Processed: 0 | Reused/skipped: 0")
        self._build()
        self._update_browser_button()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._path_row(frame, 0, "Dataset root", self.dataset_root_var, self._choose_dataset_root)
        self._path_row(frame, 1, "Output folder", self.output_var, self._choose_output)
        ttk.Label(frame, text="Scope").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.scope_box = ttk.Combobox(frame, textvariable=self.scope_var, state="readonly")
        self.scope_box.grid(row=3, column=0, sticky="ew")
        self.scope_box["values"] = ("All datasets",)
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 8))
        self.start_button = ttk.Button(actions, text="Start Preprocessing", command=self._start)
        self.start_button.grid(row=0, column=0, padx=(0, 7))
        ttk.Button(actions, text="Open Output Folder", command=self._open_output).grid(row=0, column=1, padx=(0, 7))
        self.browser_button = ttk.Button(actions, text="Launch Mug Browser", command=self._launch_browser)
        self.browser_button.grid(row=0, column=2)
        ttk.Label(frame, textvariable=self.progress_var, wraplength=550, justify="left").grid(row=5, column=0, sticky="w", pady=(12, 4))
        ttk.Label(frame, textvariable=self.counts_var, wraplength=550, justify="left").grid(row=6, column=0, sticky="w")

    @staticmethod
    def _path_row(parent: ttk.Frame, row: int, label: str, value: tk.StringVar, command: Callable[[], None]) -> None:
        ttk.Label(parent, text=label).grid(row=row * 2, column=0, sticky="w")
        line = ttk.Frame(parent)
        line.grid(row=row * 2 + 1, column=0, sticky="ew", pady=(0, 8))
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=value).grid(row=0, column=0, sticky="ew", padx=(0, 7))
        ttk.Button(line, text="Choose Folder...", command=command).grid(row=0, column=1)

    def _choose_dataset_root(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Choose dataset root")
        if selected:
            self.dataset_root_var.set(selected)
            self.output_var.set(str(default_output_path(selected)))
            self._load_scope_choices()
            self._update_browser_button()

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Choose preprocessing output folder")
        if selected:
            self.output_var.set(selected)
            self._update_browser_button()

    def _load_scope_choices(self) -> None:
        root = Path(self.dataset_root_var.get())
        try:
            choices = [item.dataset.display_name for item in discover_datasets(root)]
        except Exception:
            choices = []
        self.scope_box["values"] = tuple(["All datasets", *choices])
        self.scope_var.set("All datasets")

    def _start(self) -> None:
        dataset_root = Path(self.dataset_root_var.get())
        output = Path(self.output_var.get())
        if not dataset_root.is_dir():
            messagebox.showerror("Preprocessing", f"Dataset root does not exist: {dataset_root}", parent=self.root)
            return
        found = discover_datasets(dataset_root)
        datasets = [item.dataset for item in found]
        if self.scope_var.get() != "All datasets":
            datasets = [dataset for dataset in datasets if dataset.display_name == self.scope_var.get()]
        if not datasets:
            messagebox.showerror("Preprocessing", "No usable datasets were selected.", parent=self.root)
            return
        self.start_button.configure(state="disabled")
        self.progress_var.set("Starting preprocessing...")
        self.worker = PreprocessWorker()
        self.worker.start(datasets, output)
        self.root.after(75, self._poll_worker)

    def _poll_worker(self) -> None:
        if self.worker is None:
            return
        finished = False
        for event in self.worker.drain():
            if isinstance(event, PreprocessProgress):
                self._apply_progress(event)
            else:
                finished = True
                self._finish(event)
        if not finished:
            self.root.after(75, self._poll_worker)

    def _apply_progress(self, progress: PreprocessProgress) -> None:
        self.progress_var.set(f"{progress.processed_or_reused} / {progress.total}: {progress.dataset_name} - {progress.street_id} {progress.street_name}")
        self.counts_var.set(_counts_text(progress.summary))

    def _finish(self, finished: WorkerFinished) -> None:
        self.start_button.configure(state="normal")
        self.worker = None
        if finished.error is not None:
            self.progress_var.set(f"Preprocessing failed: {finished.error}")
            messagebox.showerror("Preprocessing", str(finished.error), parent=self.root)
            return
        assert finished.summary is not None
        self.counts_var.set(_counts_text(finished.summary))
        self.progress_var.set("Preprocessing finished.")
        self._update_browser_button()
        messagebox.showinfo("Preprocessing complete", _counts_text(finished.summary), parent=self.root)

    def _update_browser_button(self) -> None:
        output = Path(self.output_var.get()) if self.output_var.get() else None
        ready = output is not None and (output / INDEX_FILENAME).is_file()
        self.browser_button.configure(state="normal" if ready else "disabled")

    def _open_output(self) -> None:
        output = Path(self.output_var.get())
        output.mkdir(parents=True, exist_ok=True)
        os.startfile(output)  # type: ignore[attr-defined]

    def _launch_browser(self) -> None:
        from .app import launch

        dataset_root, output = Path(self.dataset_root_var.get()), Path(self.output_var.get())
        self.root.destroy()
        launch(dataset_root=dataset_root, preprocessed=output)


def _counts_text(summary: PreprocessSummary) -> str:
    return (
        f"Processed: {summary.processed} | Reused/skipped: {summary.reused} | "
        f"AUTO_APPROVED: {summary.auto_approved} | MANUAL_REVIEW: {summary.manual_review} | "
        f"MANUAL_APPROVED: {summary.manual_approved} | UNRENDERABLE_INPUT: {summary.unrenderable_input} | "
        f"Unexpected errors: {summary.unexpected_errors}"
    )


def launch(*, dataset_root: Path | None = None) -> int:
    root = tk.Tk()
    PreprocessUiApp(root, dataset_root=dataset_root)
    root.mainloop()
    return 0
