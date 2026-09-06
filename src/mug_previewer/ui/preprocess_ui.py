"""Small Tk front end for the resumable preprocessing workflow."""
from __future__ import annotations

import os
import queue
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable

from ..datasets.discovery import DatasetCandidate, discover_datasets
from ..datasets.models import Dataset
from ..preprocess import INDEX_FILENAME, PreprocessProgress, PreprocessSummary, preprocess_datasets


class PreprocessLocationError(ValueError):
    """A concise, user-actionable input or output location problem."""


def validate_preprocess_locations(
    input_root: Path | str | None,
    output_root: Path | str | None,
    *,
    discover: Callable[[Path | str], list] = discover_datasets,
) -> tuple[Path, Path, list]:
    """Validate explicit locations and return the datasets from the input root."""
    if input_root is None or not str(input_root).strip():
        raise PreprocessLocationError("Choose an Input Dataset Folder.")
    if output_root is None or not str(output_root).strip():
        raise PreprocessLocationError("Choose an Output Folder.")
    input_path, output_path = Path(input_root), Path(output_root)
    if not input_path.is_dir():
        raise PreprocessLocationError(f"Input Dataset Folder does not exist: {input_path}")
    input_resolved, output_resolved = input_path.resolve(), output_path.resolve()
    if _paths_overlap(input_resolved, output_resolved):
        raise PreprocessLocationError("Input Dataset Folder and Output Folder must be separate locations.")
    if output_path.exists() and not output_path.is_dir():
        raise PreprocessLocationError(f"Output Folder is not a folder: {output_path}")
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise PreprocessLocationError(f"Output Folder cannot be created: {output_path} ({error})") from error
    if not output_path.is_dir():
        raise PreprocessLocationError(f"Output Folder is not a folder: {output_path}")
    try:
        with tempfile.NamedTemporaryFile(dir=output_path, prefix=".mug-previewer-write-test-", delete=True):
            pass
    except OSError as error:
        raise PreprocessLocationError(f"Output Folder is not writable: {output_path} ({error})") from error
    datasets = discover(input_path)
    if not datasets:
        raise PreprocessLocationError(f"No valid workflow datasets were found in: {input_path}")
    return input_path, output_path, datasets


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        right.relative_to(left)
        return True
    except ValueError:
        try:
            left.relative_to(right)
            return True
        except ValueError:
            return False


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
    def __init__(
        self,
        root: tk.Tk,
        *,
        dataset_root: Path | None = None,
        output_root: Path | None = None,
    ) -> None:
        self.root = root
        self.root.title("Mug Previewer Preprocessing")
        self.root.minsize(640, 460)
        self.worker: PreprocessWorker | None = None
        self.dataset_root_var = tk.StringVar(value=str(dataset_root) if dataset_root else "")
        self.output_var = tk.StringVar(value=str(output_root) if output_root else "")
        self.scope_var = tk.StringVar(value="All datasets")
        self.progress_var = tk.StringVar(value="Ready")
        self.counts_var = tk.StringVar(value="Processed: 0 | Reused/skipped: 0")
        self._build()
        if dataset_root is not None:
            self._load_scope_choices()
        self._refresh_location_state()
        self._update_browser_button()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._path_section(
            frame,
            0,
            "Input Dataset Folder",
            self.dataset_root_var,
            self._choose_dataset_root,
            "Choose Input Folder...",
        )
        self._path_section(
            frame,
            1,
            "Output Folder",
            self.output_var,
            self._choose_output,
            "Choose Output Folder...",
        )

        scope = ttk.Frame(frame)
        scope.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        scope.columnconfigure(0, weight=1)
        ttk.Label(scope, text="Scope").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.scope_box = ttk.Combobox(scope, textvariable=self.scope_var, state="readonly")
        self.scope_box.grid(row=1, column=0, sticky="ew")
        self.scope_box["values"] = ("All datasets",)

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="w", pady=(0, 18))
        self.start_button = ttk.Button(actions, text="Start Preprocessing", command=self._start, state="disabled")
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Button(actions, text="Open Output Folder", command=self._open_output).grid(row=0, column=1, padx=(0, 8))
        self.browser_button = ttk.Button(actions, text="Launch Mug Browser", command=self._launch_browser)
        self.browser_button.grid(row=0, column=2)

        status = ttk.Frame(frame)
        status.grid(row=4, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Separator(status, orient="horizontal").grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(status, textvariable=self.progress_var, wraplength=600, justify="left").grid(row=1, column=0, sticky="w", pady=(0, 4))
        ttk.Label(status, textvariable=self.counts_var, wraplength=600, justify="left").grid(row=2, column=0, sticky="w")

    @staticmethod
    def _path_section(
        parent: ttk.Frame, row: int, label: str, value: tk.StringVar,
        command: Callable[[], None], button_label: str,
    ) -> None:
        section = ttk.Frame(parent)
        section.grid(row=row, column=0, sticky="ew", pady=(0, 18))
        section.columnconfigure(0, weight=1)
        ttk.Label(section, text=label).grid(row=0, column=0, sticky="w", pady=(0, 4))
        line = ttk.Frame(section)
        line.grid(row=1, column=0, sticky="ew")
        line.columnconfigure(0, weight=1)
        ttk.Entry(line, textvariable=value, state="readonly").grid(row=0, column=0, sticky="ew", padx=(0, 7))
        ttk.Button(line, text=button_label, command=command).grid(row=0, column=1)

    def _choose_dataset_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose Input Dataset Folder",
            initialdir=self.dataset_root_var.get(),
        )
        if selected:
            self.dataset_root_var.set(selected)
            self._load_scope_choices()
            self._refresh_location_state()
            self._update_browser_button()

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose Output Folder",
            initialdir=self.output_var.get(),
        )
        if selected:
            self.output_var.set(selected)
            self._refresh_location_state()
            self._update_browser_button()

    def _load_scope_choices(self) -> None:
        root = self.dataset_root_var.get()
        try:
            datasets = discover_datasets(root)
        except Exception:
            datasets = ()
        self._set_scope_choices(datasets)

    def _set_scope_choices(self, datasets: Iterable[DatasetCandidate]) -> None:
        candidates = tuple(datasets)
        names = tuple(item.dataset.display_name for item in candidates)
        selected_path = getattr(candidates[0].dataset, "path", None) if len(candidates) == 1 else None
        is_direct_dataset = (
            bool(selected_path)
            and Path(self.dataset_root_var.get()).resolve() == Path(selected_path).resolve()
        )
        values = names if is_direct_dataset else ("All datasets", *names)
        self.scope_box["values"] = values
        self.scope_var.set(names[0] if is_direct_dataset else "All datasets")

    def _refresh_location_state(self) -> None:
        try:
            _input_root, _output_root, datasets = validate_preprocess_locations(
                self.dataset_root_var.get(), self.output_var.get(),
            )
        except PreprocessLocationError as error:
            self.start_button.configure(state="disabled")
            if self.dataset_root_var.get() or self.output_var.get():
                self.progress_var.set(str(error))
            return
        self._set_scope_choices(datasets)
        self.start_button.configure(state="normal")
        self.progress_var.set("Input and output folders are ready.")

    def _start(self) -> None:
        try:
            dataset_root, output, found = validate_preprocess_locations(
                self.dataset_root_var.get(), self.output_var.get(),
            )
        except PreprocessLocationError as error:
            self.start_button.configure(state="disabled")
            self.progress_var.set(str(error))
            messagebox.showerror("Preprocessing", str(error), parent=self.root)
            return
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
        try:
            _input_root, output, _datasets = validate_preprocess_locations(
                self.dataset_root_var.get(), self.output_var.get(),
            )
        except PreprocessLocationError:
            ready = False
        else:
            ready = (output / INDEX_FILENAME).is_file()
        self.browser_button.configure(state="normal" if ready else "disabled")

    def _open_output(self) -> None:
        if not self.output_var.get():
            messagebox.showerror("Preprocessing", "Choose an Output Folder first.", parent=self.root)
            return
        output = Path(self.output_var.get())
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            messagebox.showerror("Preprocessing", f"Cannot create Output Folder: {error}", parent=self.root)
            return
        os.startfile(output)  # type: ignore[attr-defined]

    def _launch_browser(self) -> None:
        from .app import launch

        try:
            dataset_root, output, _datasets = validate_preprocess_locations(
                self.dataset_root_var.get(), self.output_var.get(),
            )
        except PreprocessLocationError as error:
            messagebox.showerror("Preprocessing", str(error), parent=self.root)
            return
        if not (output / INDEX_FILENAME).is_file():
            messagebox.showerror("Preprocessing", f"No preprocessing index exists in: {output}", parent=self.root)
            return
        self.root.destroy()
        launch(dataset_root=dataset_root, preprocessed=output)


def _counts_text(summary: PreprocessSummary) -> str:
    return (
        f"Processed: {summary.processed} | Reused/skipped: {summary.reused} | "
        f"AUTO_APPROVED: {summary.auto_approved} | MANUAL_REVIEW: {summary.manual_review} | "
        f"MANUAL_APPROVED: {summary.manual_approved} | UNRENDERABLE_INPUT: {summary.unrenderable_input} | "
        f"Unexpected errors: {summary.unexpected_errors}"
    )


def launch(*, dataset_root: Path | None = None, output_root: Path | None = None) -> int:
    root = tk.Tk()
    PreprocessUiApp(root, dataset_root=dataset_root, output_root=output_root)
    root.mainloop()
    return 0
