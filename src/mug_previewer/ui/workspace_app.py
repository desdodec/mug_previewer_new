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
from .preprocess_ui import PreprocessWorker, WorkerFinished
from .state import PreprocessedCatalogue, UIDataError, load_preprocessed_catalogue


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
    prepared = sum(1 for dataset_id, _street_id in catalogue.records if dataset_id == dataset.id)
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
        super().__init__(root, dataset_root=dataset_root, preprocessed=preprocessed)

    def _build_widgets(self) -> None:
        super()._build_widgets()
        if not self._is_preprocessed_mode():
            return
        self.face_generation_var = tk.StringVar(value="Select a dataset to generate face artwork.")
        panel = ttk.LabelFrame(self.workflow_card, text="Face generation", padding=6)
        panel.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, textvariable=self.face_generation_var, wraplength=320, justify="left").grid(
            row=0, column=0, sticky="ew", pady=(0, 5)
        )
        self.generate_faces_button = ttk.Button(
            panel,
            text="Generate Faces",
            command=self._start_generate_faces,
            state="disabled",
        )
        self.generate_faces_button.grid(row=1, column=0, sticky="ew")

    def _select_dataset(self, _event: object | None = None) -> None:
        super()._select_dataset(_event)
        self._refresh_face_generation_state()

    def _refresh_selected_artwork(self) -> None:
        super()._refresh_selected_artwork()
        self._refresh_face_generation_state()

    def _refresh_face_generation_state(self) -> None:
        if "generate_faces_button" not in self.__dict__:
            return
        data = self.state.selected_dataset
        catalogue = self.preprocessed_catalogue
        if data is None or catalogue is None:
            self.face_generation_var.set("Select a dataset to generate face artwork.")
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
        data = self.state.selected_dataset
        catalogue = self.preprocessed_catalogue
        if data is None or catalogue is None:
            self.status_var.set("Select a dataset before generating faces.")
            return
        if "batch_panel" in self.__dict__ and self.batch_panel.busy:
            self.status_var.set("Finish or cancel the active batch export before generating faces.")
            return

        self._face_preprocess_worker = PreprocessWorker()
        self.dataset_box.configure(state="disabled")
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
        self._face_preprocess_worker = None
        self.dataset_box.configure(state="readonly")
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
            self._reload_workflow()
            self._apply_filter()
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
