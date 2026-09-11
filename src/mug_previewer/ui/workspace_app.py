"""Unified Mug Workspace additions for resumable GUI face generation."""
from __future__ import annotations

import argparse
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Sequence

from ..datasets.models import Dataset
from ..preprocess import PreprocessProgress, PreprocessSummary
from .app import MugPreviewerApp
from .prepared_linking import prepared_dataset_options
from .preprocess_ui import PreprocessWorker, WorkerFinished
from .state import (
    DatasetOption,
    PreprocessedCatalogue,
    UIDataError,
    dataset_options,
    load_preprocessed_catalogue,
)


@dataclass(frozen=True)
class FaceGenerationState:
    prepared: int
    total: int
    button_text: str
    enabled: bool


def face_generation_state(
    catalogue: PreprocessedCatalogue,
    dataset: Dataset,
    *,
    busy: bool = False,
) -> FaceGenerationState:
    """Describe the resumable preprocessing action for one selected dataset."""
    current_street_ids = {street.id for street in dataset.streets}
    prepared = sum(
        1
        for dataset_id, street_id in catalogue.records
        if dataset_id == dataset.id and street_id in current_street_ids
    )
    total = len(dataset.streets)
    if busy:
        return FaceGenerationState(prepared, total, "Generating Faces...", False)
    if prepared == 0:
        return FaceGenerationState(prepared, total, "Generate Faces", total > 0)
    if prepared < total:
        return FaceGenerationState(prepared, total, "Generate Missing Faces", True)
    return FaceGenerationState(prepared, total, "Faces Generated", False)


def preprocess_summary_text(summary: PreprocessSummary) -> str:
    return (
        f"Processed: {summary.processed} | Reused: {summary.reused}\n"
        f"AUTO_APPROVED: {summary.auto_approved} | MANUAL_REVIEW: {summary.manual_review} | "
        f"MANUAL_APPROVED: {summary.manual_approved} | UNRENDERABLE: {summary.unrenderable_input}"
    )


class MugWorkspaceApp(MugPreviewerApp):
    """Main prepared-artwork workspace with in-app resumable preprocessing."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        dataset_root: Path | None = None,
        preprocessed: Path | None = None,
    ) -> None:
        self._face_preprocess_worker: PreprocessWorker | None = None
        self._face_generation_dataset: Dataset | None = None
        self.source_dataset_by_label: dict[str, Dataset] = {}
        super().__init__(root, dataset_root=dataset_root, preprocessed=preprocessed)

    def _build_widgets(self) -> None:
        super()._build_widgets()
        if not self._is_preprocessed_mode():
            return
        self.face_generation_var = tk.StringVar(value="Choose a source dataset to create face artwork.")
        panel = ttk.LabelFrame(self.workflow_card, text="Face generation", padding=6)
        panel.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="Source dataset").grid(row=0, column=0, sticky="w", pady=(0, 3))
        self.source_dataset_var = tk.StringVar()
        self.source_dataset_box = ttk.Combobox(
            panel,
            state="readonly",
            textvariable=self.source_dataset_var,
            width=36,
        )
        self.source_dataset_box.grid(row=1, column=0, sticky="ew")
        self.source_dataset_box.bind("<<ComboboxSelected>>", self._select_source_dataset)
        ttk.Label(panel, textvariable=self.face_generation_var, wraplength=320, justify="left").grid(
            row=2, column=0, sticky="ew", pady=(6, 5)
        )
        self.generate_faces_button = ttk.Button(
            panel,
            text="Generate Faces",
            command=self._start_generate_faces,
            state="disabled",
        )
        self.generate_faces_button.grid(row=3, column=0, sticky="ew")

    def _refresh_batch_dataset_options(self) -> None:
        """Populate batch export only after prepared workspace discovery has completed."""
        panel = self.__dict__.get("batch_panel")
        if panel is not None:
            panel.refresh_dataset_options()

    def refresh_datasets(self) -> None:
        """Keep the workspace selector prepared-only; source discovery stays in Create Faces."""
        try:
            source_options = dataset_options(self.state.dataset_root)
        except UIDataError as error:
            self._show_error(str(error))
            return

        previous_source_id = None
        selected_source = self.source_dataset_by_label.get(
            self.source_dataset_var.get() if "source_dataset_var" in self.__dict__ else ""
        )
        if selected_source is not None:
            previous_source_id = selected_source.id

        self.source_dataset_by_label = {item.label: item.dataset for item in source_options}
        if "source_dataset_box" in self.__dict__:
            self.source_dataset_box["values"] = list(self.source_dataset_by_label)
            if previous_source_id is not None:
                label = next(
                    (label for label, dataset in self.source_dataset_by_label.items()
                     if dataset.id == previous_source_id),
                    "",
                )
                self.source_dataset_var.set(label)
            elif self.source_dataset_var.get() not in self.source_dataset_by_label:
                self.source_dataset_var.set("")

        catalogue = self.preprocessed_catalogue
        if catalogue is None:
            self.state.datasets = source_options
        else:
            self.state.datasets = prepared_dataset_options(source_options, catalogue)

        current_id = self.state.selected_dataset.id if self.state.selected_dataset is not None else None
        self.dataset_by_label = {item.label: item.dataset for item in self.state.datasets}
        self.dataset_box["values"] = list(self.dataset_by_label)
        current_label = next(
            (label for label, dataset in self.dataset_by_label.items() if dataset.id == current_id),
            "",
        )
        if current_label:
            self.dataset_var.set(current_label)
        else:
            self.dataset_var.set("")
            self.state.selected_dataset = None
            self.state.selected_street = None

        # BatchExportPanel is constructed before this after-idle discovery runs.
        # Refresh it now so its prepared-face-set dropdown does not stay empty.
        self._refresh_batch_dataset_options()

        if catalogue is None:
            self.status_var.set(f"{len(self.state.datasets)} datasets found. Select a dataset.")
        elif self.state.datasets:
            self.status_var.set(
                f"{len(self.state.datasets)} prepared face sets found. Select a prepared face set."
            )
        else:
            self.status_var.set(
                "No prepared face sets could be linked to source map data. "
                "Check the source parent folder or create a new source run."
            )
        self._refresh_face_generation_state()

    def _select_dataset(self, _event: object | None = None) -> None:
        super()._select_dataset(_event)
        data = self.state.selected_dataset
        if data is not None and "source_dataset_var" in self.__dict__:
            label = next(
                (label for label, source in self.source_dataset_by_label.items() if source.id == data.id),
                None,
            )
            if label is not None:
                self.source_dataset_var.set(label)
        self._refresh_batch_dataset_options()
        self._refresh_face_generation_state()

    def _select_source_dataset(self, _event: object | None = None) -> None:
        self._refresh_face_generation_state()

    def _selected_source_dataset(self) -> Dataset | None:
        if "source_dataset_var" not in self.__dict__:
            return None
        return self.source_dataset_by_label.get(self.source_dataset_var.get())

    def _refresh_selected_artwork(self) -> None:
        super()._refresh_selected_artwork()
        self._refresh_face_generation_state()

    def _refresh_face_generation_state(self) -> None:
        if "generate_faces_button" not in self.__dict__:
            return
        data = self._selected_source_dataset()
        catalogue = self.preprocessed_catalogue
        if data is None or catalogue is None:
            self.face_generation_var.set("Choose a source dataset to create face artwork.")
            self.generate_faces_button.configure(state="disabled", text="Generate Faces")
            return
        state = face_generation_state(
            catalogue,
            data,
            busy=self._face_preprocess_worker is not None,
        )
        self.face_generation_var.set(f"Prepared face records: {state.prepared} / {state.total}")
        self.generate_faces_button.configure(
            text=state.button_text,
            state="normal" if state.enabled else "disabled",
        )

    def _start_generate_faces(self) -> None:
        if self._face_preprocess_worker is not None:
            return
        data = self._selected_source_dataset()
        catalogue = self.preprocessed_catalogue
        if data is None or catalogue is None:
            self.status_var.set("Choose a source dataset before generating faces.")
            return
        if "batch_panel" in self.__dict__ and self.batch_panel.busy:
            self.status_var.set("Finish or cancel the active batch export before generating faces.")
            return

        self._face_generation_dataset = data
        self._face_preprocess_worker = PreprocessWorker()
        self.dataset_box.configure(state="disabled")
        self.source_dataset_box.configure(state="disabled")
        self.street_list.configure(state="disabled")
        self.generate_faces_button.configure(state="disabled", text="Generating Faces...")
        self.face_generation_var.set(f"Starting face generation for {data.display_name}...")
        self.status_var.set(f"Generating prepared face artwork for {data.display_name}...")
        self._set_export_buttons_state("disabled")
        if "batch_panel" in self.__dict__:
            self.batch_panel.invalidate()
        # PreprocessWorker deliberately uses preprocess_datasets with force=False,
        # so existing valid records/manual approvals are reused rather than regenerated.
        self._face_preprocess_worker.start((data,), catalogue.root)
        self.root.after(75, self._poll_face_preprocess)

    def _poll_face_preprocess(self) -> None:
        worker = self._face_preprocess_worker
        if worker is None or self._shutting_down:
            return
        finished: WorkerFinished | None = None
        for event in worker.drain():
            if isinstance(event, PreprocessProgress):
                self.face_generation_var.set(
                    f"{event.processed_or_reused} / {event.total}: "
                    f"{event.street_id} {event.street_name}\n{preprocess_summary_text(event.summary)}"
                )
                self.status_var.set(
                    f"Generating faces: {event.processed_or_reused} / {event.total} - "
                    f"{event.street_name}"
                )
            else:
                finished = event
        if finished is None:
            self.root.after(75, self._poll_face_preprocess)
            return
        self._finish_face_preprocess(finished)

    def _finish_face_preprocess(self, finished: WorkerFinished) -> None:
        generated_dataset = self._face_generation_dataset
        self._face_generation_dataset = None
        self._face_preprocess_worker = None
        self.dataset_box.configure(state="readonly")
        self.source_dataset_box.configure(state="readonly")
        self.street_list.configure(state="normal")
        if finished.error is not None:
            self._refresh_face_generation_state()
            self._show_error(f"Face generation failed: {finished.error}")
            return

        assert finished.summary is not None
        try:
            root = self.preprocessed_catalogue.root
            self.preprocessed_catalogue = load_preprocessed_catalogue(root)
            if "batch_panel" in self.__dict__:
                self.batch_panel.invalidate()
            self.refresh_datasets()
            if generated_dataset is not None:
                prepared_label = next(
                    (label for label, dataset in self.dataset_by_label.items()
                     if dataset.id == generated_dataset.id),
                    None,
                )
                if prepared_label is not None:
                    self.dataset_var.set(prepared_label)
                    self._select_dataset()
        except Exception as error:
            self._refresh_face_generation_state()
            self._show_error(f"Faces were generated but the workspace could not refresh: {error}")
            return

        summary = finished.summary
        self.face_generation_var.set(preprocess_summary_text(summary))
        self.status_var.set(
            f"Face generation complete: {summary.processed} processed, {summary.reused} reused; "
            f"{summary.auto_approved} auto approved, {summary.manual_review} manual review, "
            f"{summary.unrenderable_input} unrenderable."
        )
        self._refresh_face_generation_state()


def launch(*, dataset_root: Path | None = None, preprocessed: Path | None = None) -> int:
    root = tk.Tk()
    try:
        MugWorkspaceApp(root, dataset_root=dataset_root, preprocessed=preprocessed)
    except UIDataError as error:
        messagebox.showerror("Mug Workspace", str(error), parent=root)
        root.destroy()
        return 2
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mug_previewer.ui")
    parser.add_argument("--preprocessed", type=Path, help="Prepared artwork output directory.")
    parser.add_argument("--dataset-root", type=Path, help="Workflow-v6 dataset root.")
    args = parser.parse_args(argv)
    return launch(dataset_root=args.dataset_root, preprocessed=args.preprocessed)
