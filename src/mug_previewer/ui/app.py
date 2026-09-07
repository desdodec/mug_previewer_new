"""Small Tkinter shell for selecting streets and viewing production mockups."""
from __future__ import annotations

import logging
from collections.abc import Callable
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from ..preprocessed_export import export_preprocessed_provider_png
from ..design import DESIGN_WEIGHT_MAX, DESIGN_WEIGHT_MIN, DESIGN_WEIGHT_STEP, DesignOptions
from ..datasets.models import Dataset, StreetRecord
from .artwork_panel import ArtworkPanelMixin
from .batch_export_panel import BatchExportPanel
from .manual_review import ManualReviewController, ManualReviewWindow
from .production import ProductionStatus, production_status, production_summary, production_unrenderable_items
from .state import (
    AppState,
    PreprocessedCatalogue,
    PreprocessedRecord,
    DatasetOption,
    PreviewPair,
    UIDataError,
    dataset_options,
    display_image,
    export_provider_png,
    filter_streets,
    load_preprocessed_catalogue,
    load_preprocessed_preview,
    render_preview_pair,
    render_prepared_preview_pair,
    resolve_dataset_root,
)

from .workspace import WORKFLOW_FILTERS, filter_workflow, load_workflow, workflow_counts

LOGGER = logging.getLogger(__name__)


class MugPreviewerApp(ArtworkPanelMixin, ttk.Frame):
    """Widget layer that delegates data, rendering, and image operations to state."""

    def __init__(
        self, root: tk.Tk, *, dataset_root: Path | None = None, preprocessed: Path | None = None,
    ) -> None:
        super().__init__(root, padding=14)
        self.preprocessed_catalogue: PreprocessedCatalogue | None = (
            load_preprocessed_catalogue(preprocessed) if preprocessed is not None else None
        )
        self.root = root
        self.state = AppState(dataset_root=resolve_dataset_root(dataset_root))
        self.dataset_by_label: dict[str, Dataset] = {}
        self._front_photo: ImageTk.PhotoImage | None = None
        self._rear_photo: ImageTk.PhotoImage | None = None
        self._resize_pending: str | None = None
        self.current_production_status: ProductionStatus | None = None
        self._production_status_generation = 0
        self._production_status_cache: dict[tuple[str, str], ProductionStatus] = {}
        self._production_status_results: queue.SimpleQueue[tuple[int, Dataset, StreetRecord, ProductionStatus | None, Exception | None]] = queue.SimpleQueue()
        self._render_generation = 0
        self._render_results: queue.SimpleQueue[tuple[int, Dataset, StreetRecord, PreviewPair | None, Exception | None]] = queue.SimpleQueue()
        self._workflow_generation = 0
        self._workflow_results = queue.SimpleQueue()
        self._shutting_down = False
        self._build_widgets()
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.protocol("WM_DELETE_WINDOW", self._shutdown)
        root.after(25, self._drain_production_status_results)
        root.after(25, self._drain_render_results)
        root.after(25, self._drain_workflow_results)
        root.after_idle(self.refresh_datasets)

    def _build_widgets(self) -> None:
        self.root.title("Mug Workspace" if self._is_preprocessed_mode() else "Mug Previewer")
        self.root.minsize(1050, 650)
        self.columnconfigure(0, weight=0, minsize=285)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)

        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        ttk.Label(controls, text="Mug Workspace" if self._is_preprocessed_mode() else "Mug Previewer", font=("TkDefaultFont", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="Dataset").grid(row=1, column=0, sticky="w", pady=(18, 3))
        self.dataset_var = tk.StringVar()
        self.dataset_box = ttk.Combobox(controls, state="readonly", textvariable=self.dataset_var, width=32)
        self.dataset_box.grid(row=2, column=0, sticky="ew")
        self.dataset_box.bind("<<ComboboxSelected>>", self._select_dataset)
        ttk.Label(controls, text="Search streets").grid(row=3, column=0, sticky="w", pady=(16, 3))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._filter_changed)
        ttk.Entry(controls, textvariable=self.search_var).grid(row=4, column=0, sticky="ew")
        self.street_list = tk.Listbox(controls, height=22, exportselection=False, activestyle="none")
        self.street_list.grid(row=5, column=0, sticky="nsew", pady=(7, 10))
        self.street_list.bind("<<ListboxSelect>>", self._select_street)
        controls.rowconfigure(5, weight=1)
        controls.columnconfigure(0, weight=1)
        self.manual_review_button = ttk.Button(controls, text="Review Pending Streets", command=self._open_manual_review)
        self.manual_review_button.grid(row=11, column=0, sticky="ew", pady=(10, 0))
        self.review_street_button = ttk.Button(controls, text="Review Selected Street", command=self._review_selected_street, state="disabled")
        self.review_street_button.grid(row=12, column=0, sticky="ew", pady=(6, 0))
        self.summary_button = ttk.Button(controls, text="Production Summary", command=self._show_production_summary)
        self.summary_button.grid(row=13, column=0, sticky="ew", pady=(6, 0))
        self.production_var = tk.StringVar(value="Production status: select a street")
        ttk.Label(controls, textvariable=self.production_var, wraplength=270, justify="left").grid(row=14, column=0, sticky="ew", pady=(10, 0))
        self.render_button = ttk.Button(controls, text="Render Preview", command=self._start_render, state="disabled")
        design = ttk.LabelFrame(controls, text="Design", padding=8)
        design.grid(row=6, column=0, sticky="ew", pady=(2, 10))
        design.columnconfigure(0, weight=1)
        self.front_weight_var = tk.DoubleVar(value=self.state.design_options.front_feature_weight)
        self.rear_weight_var = tk.DoubleVar(value=self.state.design_options.rear_highlight_weight)
        self.front_weight_display = tk.StringVar()
        self.rear_weight_display = tk.StringVar()
        self._add_weight_control(design, 0, "Street feature weight", self.front_weight_var, self.front_weight_display)
        self._add_weight_control(design, 2, "Map highlight weight", self.rear_weight_var, self.rear_weight_display)
        ttk.Button(design, text="Reset design", command=self._reset_design).grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.export_button = ttk.Button(
            controls,
            text='Export Inkthreadable PNG',
            command=self._start_inkthreadable_export,
            state='disabled',
        )
        self.export_button.grid(row=9, column=0, sticky='ew', pady=(6, 0))
        self.printify_export_button = ttk.Button(
            controls,
            text="Export Printify PNG",
            command=self._start_printify_export,
            state="disabled",
        )
        self.printify_export_button.grid(row=10, column=0, sticky="ew", pady=(6, 0))
        self.render_button.grid(row=7, column=0, sticky="ew")
        self.framing_var = tk.StringVar(value="Rear framing: \u2014")
        if self._is_preprocessed_mode():
            self.render_button.grid_remove()
        ttk.Label(controls, textvariable=self.framing_var).grid(row=8, column=0, sticky="w", pady=(13, 0))

        self.front_card = self._preview_card("Front")
        self.front_card.grid(row=0, column=1, sticky="nsew", padx=(0, 7))
        self.rear_card = self._preview_card("Rear")
        self.rear_card.grid(row=0, column=2, sticky="nsew", padx=(7, 0))
        if self._is_preprocessed_mode():
            design.grid_remove()
            self.manual_review_button.grid_remove()
            self.review_street_button.grid_remove()
            self.summary_button.grid_remove()
            self.workflow_var = tk.StringVar(value='All')
            self.workflow_items = {}
            self.workflow_box = ttk.Combobox(controls, textvariable=self.workflow_var,
                                             values=WORKFLOW_FILTERS, state='readonly')
            self.workflow_box.grid(row=5, column=0, sticky='ew', pady=(8, 0))
            self.street_list.grid(row=6)
            controls.rowconfigure(5, weight=0)
            controls.rowconfigure(6, weight=1)
            self.workflow_box.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
            self.workflow_counts_var = tk.StringVar()
            ttk.Label(controls, textvariable=self.workflow_counts_var, wraplength=260).grid(row=15, column=0, sticky='ew')
            self.export_button.grid_remove()
            self.printify_export_button.grid_remove()
            self.render_button.configure(text='Preview Mug')
            self.render_button.grid()
            self._build_unified_workspace()
        self.status_var = tk.StringVar(value="Loading datasets\u2026")
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))

    def _build_unified_workspace(self):
        self.columnconfigure(2, weight=0, minsize=360)
        self.front_card.grid_remove()
        self.rear_card.grid_remove()
        self.preview_tabs = ttk.Notebook(self)
        self.preview_tabs.grid(row=0, column=1, sticky='nsew', padx=8)
        for title, attribute in (('Face', 'front'), ('Mug Front', 'mug_front'), ('Mug Rear', 'rear'), ('Full Wrap', 'wrap')):
            card = ttk.LabelFrame(self.preview_tabs, text=title, padding=8)
            card.columnconfigure(0, weight=1)
            card.rowconfigure(0, weight=1)
            label = ttk.Label(card, anchor='center', text='Choose Preview Mug to load this view')
            label.grid(sticky='nsew')
            label.bind('<Configure>', self._preview_resized)
            setattr(self, attribute + '_label', label)
            if attribute == 'front':
                self.front_card = card
            self.preview_tabs.add(card, text=title)
        self.workflow_tabs = ttk.Notebook(self)
        self.workflow_tabs.grid(row=0, column=2, sticky='nsew')
        self.workflow_card = ttk.Frame(self.workflow_tabs, padding=6)
        self.workflow_card.columnconfigure(0, weight=1)
        self.workflow_tabs.add(self.workflow_card, text='Workflow / Single export')
        self._build_artwork_panel()
        self.export_state_var = tk.StringVar(value='Export: BLOCKED - select a street')
        ttk.Label(self.workflow_card, textvariable=self.export_state_var, wraplength=330).grid(row=1, column=0, sticky='ew', pady=8)
        self.export_button = ttk.Button(self.workflow_card, text='Export Inkthreadable PNG', command=self._start_inkthreadable_export, state='disabled')
        self.export_button.grid(row=2, column=0, sticky='ew', pady=3)
        self.printify_export_button = ttk.Button(self.workflow_card, text='Export Printify PNG', command=self._start_printify_export, state='disabled')
        self.printify_export_button.grid(row=3, column=0, sticky='ew', pady=3)
        self.batch_panel = BatchExportPanel(self.workflow_tabs, self)
        self.workflow_tabs.add(self.batch_panel, text='Batch export')
        self._mug_front_image = None

    def _reload_workflow(self):
        if 'workflow_items' not in self.__dict__ or self.state.selected_dataset is None:
            return
        self._workflow_generation += 1
        self.workflow_items = {}
        self.workflow_counts_var.set('Checking prepared workflow...')
        self._set_export_buttons_state('disabled')
        threading.Thread(target=self._workflow_worker,
                         args=(self._workflow_generation, self.state.selected_dataset), daemon=True).start()

    def _workflow_worker(self, generation, dataset):
        try:
            items = load_workflow(self.preprocessed_catalogue.root, dataset)
            self._workflow_results.put((generation, items, None))
        except Exception as error:
            self._workflow_results.put((generation, {}, str(error)))

    def _drain_workflow_results(self):
        if self._shutting_down:
            return
        while True:
            try:
                generation, items, error = self._workflow_results.get_nowait()
            except queue.Empty:
                break
            if generation != self._workflow_generation:
                continue
            self.workflow_items = items
            counts = workflow_counts(items)
            self.workflow_counts_var.set(f'Workflow unavailable: {error}' if error else
                                         ' | '.join(f'{key}: {value}' for key, value in counts.items()))
            selected = self.state.selected_street
            self._apply_filter()
            if selected in self.state.filtered_streets:
                self.street_list.selection_set(self.state.filtered_streets.index(selected))
                self._select_street()
        self._schedule_main_thread_poll(self._drain_workflow_results)

    def _open_batch_window(self):
        if self._is_preprocessed_mode():
            self.workflow_tabs.select(self.batch_panel)

    def _add_weight_control(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: tk.DoubleVar,
        display: tk.StringVar,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        ttk.Label(parent, textvariable=display).grid(row=row, column=1, sticky="e")
        scale = tk.Scale(
            parent,
            from_=DESIGN_WEIGHT_MIN,
            to=DESIGN_WEIGHT_MAX,
            resolution=DESIGN_WEIGHT_STEP,
            orient=tk.HORIZONTAL,
            showvalue=False,
            variable=variable,
            command=self._design_changed,
            highlightthickness=0,
        )
        scale.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
        self._update_weight_displays()

    def _update_weight_displays(self) -> None:
        self.front_weight_display.set(f"{self.front_weight_var.get():.2f}\u00d7")
        self.rear_weight_display.set(f"{self.rear_weight_var.get():.2f}\u00d7")

    def _design_changed(self, _value: str | None = None) -> None:
        self.state.set_design_options(
            round(self.front_weight_var.get(), 2), round(self.rear_weight_var.get(), 2),
        )
        self._update_weight_displays()
        if self.state.current_wrap is not None:
            self.status_var.set("Design settings changed \u2014 render to update preview.")

    def _reset_design(self) -> None:
        self.state.reset_design_options()
        self.front_weight_var.set(self.state.design_options.front_feature_weight)
        self.rear_weight_var.set(self.state.design_options.rear_highlight_weight)
        self._update_weight_displays()
        if self.state.current_wrap is not None:
            self.status_var.set("Design reset \u2014 render to update preview.")

    def _preview_card(self, title: str) -> ttk.Frame:
        card = ttk.LabelFrame(self, text=title, padding=8)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        label = ttk.Label(card, anchor="center")
        label.grid(sticky="nsew")
        label.bind("<Configure>", self._preview_resized)
        setattr(self, f"{title.casefold()}_label", label)
        return card

    def refresh_datasets(self) -> None:
        try:
            self.state.datasets = dataset_options(self.state.dataset_root)
        except UIDataError as error:
            self._show_error(str(error))
            return
        self.dataset_by_label = {item.label: item.dataset for item in self.state.datasets}
        self.dataset_box["values"] = list(self.dataset_by_label)
        self.status_var.set(f"{len(self.state.datasets)} datasets found. Select a dataset.")

    def _select_dataset(self, _event: object | None = None) -> None:
        data = self.dataset_by_label.get(self.dataset_var.get())
        if data is None:
            return
        self._invalidate_active_production_status_request()
        self._invalidate_active_render_request()
        self.state.selected_dataset = data
        if "batch_panel" in self.__dict__:
            self.batch_panel.invalidate()
        self.state.selected_street = None
        self.state.current_wrap = self.state.current_front_preview = self.state.current_rear_preview = None
        self.state.framing_mode = None
        self.framing_var.set("Rear framing: \u2014")
        self._clear_previews()
        self.current_production_status = None
        self.production_var.set("Production status: select a street")
        self.review_street_button.configure(state="disabled")
        self._reload_workflow()
        self._apply_filter()
        self.status_var.set(f"{data.display_name}: {len(data.streets)} streets available.")

    def _filter_changed(self, *_args: object) -> None:
        self._apply_filter()

    def _apply_filter(self) -> None:
        data = self.state.selected_dataset
        if data is None:
            return
        self._invalidate_active_production_status_request()
        self._invalidate_active_render_request()
        self.state.street_filter = self.search_var.get()
        self.state.filtered_streets = filter_streets(data.streets, self.state.street_filter)
        if 'workflow_var' in self.__dict__:
            self.state.filtered_streets = filter_workflow(self.state.filtered_streets, self.workflow_items, self.workflow_var.get())
        self.street_list.delete(0, tk.END)
        for street in self.state.filtered_streets:
            self.street_list.insert(tk.END, f"{street.id} \u2014 {street.display_name}")
        self.state.selected_street = None
        self.current_production_status = None
        self.production_var.set("Production status: select a street")
        self.render_button.configure(state="disabled")
        self._set_export_buttons_state('disabled')
        self.review_street_button.configure(state="disabled")

        if self._is_preprocessed_mode():
            self._clear_previews()
            self._reset_artwork_preview()

    def _select_street(self, _event: object | None = None) -> None:
        selection = self.street_list.curselection()
        if not selection:
            return
        self._invalidate_active_render_request()
        self.state.selected_street = self.state.filtered_streets[selection[0]]
        if self._is_preprocessed_mode():
            self._show_preprocessed_selection(self.state.selected_dataset, self.state.selected_street)
            return
        self._request_production_status(self.state.selected_dataset, self.state.selected_street)

    def _is_preprocessed_mode(self) -> bool:
        return getattr(self, "preprocessed_catalogue", None) is not None

    def _show_preprocessed_selection(self, data: Dataset | None, street: StreetRecord) -> None:
        """Apply stored state and a cached PNG synchronously, never render or score."""
        catalogue = self.preprocessed_catalogue
        if data is None or catalogue is None:
            return
        record = catalogue.find(data, street)
        self._reset_artwork_preview(record)
        self._mug_front_image = None
        self.render_button.configure(state="disabled")
        self.review_street_button.configure(state="disabled")
        self.state.current_wrap = self.state.current_front_preview = self.state.current_rear_preview = None
        self.state.framing_mode = None
        self.framing_var.set("Rear framing: cached face preview")
        if record is None:
            self.current_production_status = None
            self.production_var.set("Preview not prepared: no index record for this street.")
            self.status_var.set(f"Preview not prepared: {street.id} {street.display_name}")
            self._set_export_buttons_state("disabled")
            self._clear_previews()
            return

        result = self._preprocessed_status(record)
        self.current_production_status = result
        self.render_button.configure(state="normal" if result.readiness != "unrenderable" else "disabled")
        detail = result.detail
        if record.editable_svg_path is not None and record.state is not None and record.state.value in {"MANUAL_REVIEW", "MANUAL_APPROVED"}:
            detail = f"{detail}\nEditable SVG: {record.editable_svg_path}"
        self.production_var.set(f"{result.title}: {detail}")
        self._set_export_buttons_state("normal" if result.export_allowed else "disabled")
        if result.readiness == "unrenderable":
            self.status_var.set(f"{result.title}: {street.id} {street.display_name}")
            self._clear_previews()
            return
        try:
            self.state.current_front_preview = load_preprocessed_preview(record)
        except UIDataError as error:
            self.current_production_status = None
            self.production_var.set(str(error))
            self.status_var.set(f"Preview not prepared: {street.id} {street.display_name}")
            self._set_export_buttons_state("disabled")
            self._clear_previews()
            return
        self.status_var.set(f"Cached preview: {data.display_name} - {street.id} {street.display_name}")
        self._refresh_preview_images()

    @staticmethod
    def _preprocessed_status(record: PreprocessedRecord) -> ProductionStatus:
        detail = record.reason_detail or "Stored preprocessing result."
        if record.state is not None and record.state.value == "AUTO_APPROVED":
            return ProductionStatus("ready", "Ready for Production", detail, True, False)
        if record.state is not None and record.state.value == "MANUAL_REVIEW":
            return ProductionStatus("manual_review", "Manual Review Required", detail, False, True)
        if record.state is not None and record.state.value == "MANUAL_APPROVED":
            return ProductionStatus("ready", "Manually Approved / Ready for Production", detail, True, False)
        if record.state is not None and record.state.value == "UNRENDERABLE_INPUT":
            return ProductionStatus("unrenderable", "Cannot Render", detail, False, False)
        return ProductionStatus("not_prepared", "Preview not prepared", detail, False, False)

    def _request_production_status(self, data: Dataset | None, street: StreetRecord) -> None:
        """Start one status request; all widget changes remain on the Tk thread."""
        if data is None:
            return
        self._production_status_generation += 1
        generation = self._production_status_generation
        self.status_var.set(f"Checking production status for {street.id}...")
        self.production_var.set("Checking production status...")
        self.current_production_status = None
        self.render_button.configure(state="disabled")
        self._set_export_buttons_state("disabled")
        self.review_street_button.configure(state="disabled")
        cached = self._production_status_cache.get(self._production_status_key(data, street))
        if cached is not None:
            self._production_status_finished(generation, data, street, cached)
            return
        threading.Thread(target=self._production_status_worker, args=(generation, data, street), daemon=True).start()

    def _production_status_worker(self, generation: int, data: Dataset, street: StreetRecord) -> None:
        """Compute off the UI thread and hand the result to the main-thread poller."""
        try:
            result = production_status(data, street)
        except Exception as error:
            LOGGER.exception("Production status check failed")
            self._production_status_results.put((generation, data, street, None, error))
            return
        self._production_status_results.put((generation, data, street, result, None))

    def _drain_production_status_results(self) -> None:
        """Apply completed worker results on Tk's main thread and keep polling."""
        if getattr(self, "_shutting_down", False):
            return
        while True:
            try:
                generation, data, street, result, error = self._production_status_results.get_nowait()
            except queue.Empty:
                break
            if error is not None:
                self._production_status_failed(generation, data, street, error)
            elif result is not None:
                self._production_status_cache[self._production_status_key(data, street)] = result
                self._production_status_finished(generation, data, street, result)
        self._schedule_main_thread_poll(self._drain_production_status_results)

    @staticmethod
    def _production_status_key(data: Dataset, street: StreetRecord) -> tuple[str, str]:
        return data.id, street.id

    def _invalidate_active_production_status_request(self) -> None:
        self._production_status_generation += 1

    def _production_status_resolution_changed(self, key: tuple[str, str]) -> None:
        self._production_status_cache.pop(key, None)
        if self.state.selected_dataset is not None and self.state.selected_street is not None:
            if self._production_status_key(self.state.selected_dataset, self.state.selected_street) == key:
                self._request_production_status(self.state.selected_dataset, self.state.selected_street)

    def _is_current_production_status_request(self, generation: int, data: Dataset, street: StreetRecord) -> bool:
        selected_data, selected_street = self.state.selected_dataset, self.state.selected_street
        return (
            generation == self._production_status_generation
            and selected_data is not None
            and selected_street is not None
            and self._production_status_key(selected_data, selected_street) == self._production_status_key(data, street)
        )

    def _production_status_finished(self, generation: int, data: Dataset, street: StreetRecord, result: ProductionStatus) -> None:
        if not self._is_current_production_status_request(generation, data, street):
            return
        self.current_production_status = result
        self.production_var.set(f"{result.title}: {result.detail}")
        self.status_var.set(f"{result.title}: {street.id} {street.display_name}")
        if result.export_allowed and not self._is_preprocessed_mode():
            self.status_var.set("Production export blocked: open prepared artwork with a current human QA pass.")
        self.render_button.configure(state="normal" if result.readiness != "unrenderable" else "disabled")
        self._set_export_buttons_state("normal" if result.export_allowed else "disabled")
        self.review_street_button.configure(state="normal" if result.review_required else "disabled")

    def _production_status_failed(self, generation: int, data: Dataset, street: StreetRecord, error: Exception) -> None:
        if not self._is_current_production_status_request(generation, data, street):
            return
        self.current_production_status = None
        self.production_var.set("Status Check Failed: Could not determine production status. Check the street input and try again.")
        self.status_var.set(f"Status Check Failed: {street.id} {street.display_name}")
        self.render_button.configure(state="disabled")
        self._set_export_buttons_state("disabled")
        self.review_street_button.configure(state="disabled")
        LOGGER.error("Production status check failed for %s/%s: %s", data.id, street.id, error)

    def _review_selected_street(self) -> None:
        data, street, result = self.state.selected_dataset, self.state.selected_street, self.current_production_status
        if data is None or street is None or result is None or not result.review_required:
            self._show_error("This street does not currently require manual review.")
            return
        self._open_manual_review(initial_key=(data.id, street.id))

    def _show_production_summary(self) -> None:
        if not self.state.datasets:
            self._show_error("Load workflow-v6 datasets before opening the production summary.")
            return
        datasets = [item.dataset for item in self.state.datasets]
        summary = production_summary(datasets)
        failures = production_unrenderable_items(datasets)
        message_lines = [
            f"Ready for production: {summary.ready}",
            f"Automatically ready: {summary.auto_standard + summary.auto_adapted}",
            f"Manually approved: {summary.manual_standard + summary.manual_override}",
            f"Still requiring review: {summary.pending_manual_review}",
            f"Unable to render: {summary.unrenderable}",
            "",
            "Use Review Pending Streets to resolve pending streets.",
        ]
        if failures:
            message_lines.extend(("", "Input that cannot be rendered:"))
            message_lines.extend(f"- {item.street_name}: {item.reason}" for item in failures)
        message = "\n".join(message_lines)
        messagebox.showinfo("Production Summary", message, parent=self.root)

    def _open_manual_review(self, initial_key: tuple[str, str] | None = None) -> None:
        if not self.state.datasets:
            self._show_error("Load workflow-v6 datasets before opening Manual Review.")
            return
        def refresh_main(key: tuple[str, str]) -> None:
            self._production_status_resolution_changed(key)
        ManualReviewWindow(self.root, ManualReviewController([item.dataset for item in self.state.datasets], eager=False), initial_key=initial_key, on_resolution_changed=refresh_main)

    def _start_render(self) -> None:
        data, street = self.state.selected_dataset, self.state.selected_street
        if data is None or street is None:
            self._show_error("Select a dataset and street before rendering.")
            return
        self._render_generation += 1
        generation = self._render_generation
        self.render_button.configure(state="disabled")
        self.status_var.set(f"Rendering {street.id} \u2014 {street.display_name}\u2026")
        self.state.render_status = "Rendering"
        threading.Thread(
            target=self._render_worker,
            args=(generation, data, street, self.state.design_options),
            daemon=True,
        ).start()

    def _render_worker(self, generation: int, data: Dataset, street: StreetRecord, design_options: DesignOptions) -> None:
        """Render off the UI thread and hand the result to the main-thread poller."""
        try:
            if self._is_preprocessed_mode():
                pair = render_prepared_preview_pair(self.preprocessed_catalogue.root, data, street, design_options=design_options)
            else:
                pair = render_preview_pair(data, street, design_options=design_options)
        except Exception as error:
            LOGGER.exception("Preview rendering failed")
            self._render_results.put((generation, data, street, None, error))
            return
        self._render_results.put((generation, data, street, pair, None))

    def _drain_render_results(self) -> None:
        """Apply completed preview results on Tk's main thread and keep polling."""
        if getattr(self, "_shutting_down", False):
            return
        while True:
            try:
                generation, data, street, pair, error = self._render_results.get_nowait()
            except queue.Empty:
                break
            if not self._is_current_render_request(generation, data, street):
                continue
            if error is not None:
                self._render_failed(str(error))
            elif pair is not None:
                self._render_finished(pair, data, street)
        self._schedule_main_thread_poll(self._drain_render_results)

    def _schedule_main_thread_poll(self, callback: Callable[[], None]) -> None:
        """Reschedule a poll only while the Tk application still exists."""
        if getattr(self, "_shutting_down", False):
            return
        try:
            self.root.after(25, callback)
        except tk.TclError:
            self._shutting_down = True
            if "batch_panel" in self.__dict__:
                self.batch_panel.cancel_event.set()

    def _shutdown(self) -> None:
        """Invalidate worker completions before the Tk interpreter is destroyed."""
        if self._shutting_down:
            return
        self._shutting_down = True
        if "batch_panel" in self.__dict__:
            self.batch_panel.cancel_event.set()
        self._invalidate_active_production_status_request()
        self._invalidate_active_render_request()
        if self._resize_pending is not None:
            try:
                self.root.after_cancel(self._resize_pending)
            except tk.TclError:
                pass
            self._resize_pending = None
        self.root.destroy()

    def _invalidate_active_render_request(self) -> None:
        self._render_generation += 1

    def _is_current_render_request(self, generation: int, data: Dataset, street: StreetRecord) -> bool:
        selected_data, selected_street = self.state.selected_dataset, self.state.selected_street
        return (
            generation == self._render_generation
            and selected_data is not None
            and selected_street is not None
            and selected_data.id == data.id
            and selected_street.id == street.id
        )

    def _render_finished(self, pair: PreviewPair, data: Dataset, street: StreetRecord) -> None:
        self.state.current_wrap = pair.wrap
        if self._is_preprocessed_mode():
            self._mug_front_image = pair.front
        else:
            self.state.current_front_preview = pair.front
        self.state.current_rear_preview = pair.rear
        self.state.framing_mode = pair.framing_mode
        self.state.render_status = "Ready"
        self.framing_var.set(f"Rear framing: {pair.framing_mode}")
        self.status_var.set(f"Rendered {data.display_name} \u2014 {street.id} {street.display_name}")
        self._refresh_preview_images()
        self.render_button.configure(state="normal")

    def _render_failed(self, detail: str) -> None:
        self.state.render_status = "Error"
        self.render_button.configure(state="normal" if self.state.selected_street else "disabled")
        self._show_error(f"Could not render the selected street. {detail}")


    def _start_inkthreadable_export(self) -> None:
        self._start_provider_export(
            profile_id="inkthreadable_11oz_white",
            provider_label="Inkthreadable",
            filename_suffix="inkthreadable",
        )

    def _start_printify_export(self) -> None:
        self._start_provider_export(
            profile_id="printify_generic_11oz_ceramic",
            provider_label="Printify",
            filename_suffix="printify",
        )

    def _start_provider_export(
        self,
        *,
        profile_id: str,
        provider_label: str,
        filename_suffix: str,
    ) -> None:
        data, street = self.state.selected_dataset, self.state.selected_street
        if data is None or street is None:
            self._show_error("Select a street before exporting.")
            return
        readiness = getattr(self, "current_production_status", "unknown")
        if readiness != "unknown" and (readiness is None or not readiness.export_allowed):
            self._show_error("Production export is unavailable until this street is ready for production.")
            return
        if not self._is_preprocessed_mode():
            self._show_error('Production export requires prepared artwork with a current human QA pass. Open preprocessed mode.')
            return
        if self._is_preprocessed_mode():
            error = self._qa_export_error()
            if error:
                self._show_error(f"Production export blocked: {error}")
                return
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"Export {provider_label} PNG",
            initialfile=self._provider_filename(data, street, filename_suffix),
            defaultextension=".png",
            filetypes=[("PNG files", "*.png")],
        )
        if not destination:
            return
        self._set_export_buttons_state("disabled")
        self.status_var.set(f"Exporting {street.id} - {street.display_name} for {provider_label}...")
        threading.Thread(
            target=self._export_worker,
            args=(data, street, self.state.design_options, Path(destination), profile_id, provider_label),
            daemon=True,
        ).start()

    def _export_worker(
        self,
        data: Dataset,
        street: StreetRecord,
        design_options: DesignOptions,
        destination: Path,
        profile_id: str,
        provider_label: str,
    ) -> None:
        try:
            if self._is_preprocessed_mode():
                saved = export_preprocessed_provider_png(
                    self.preprocessed_catalogue.root, data, street, destination,
                    profile_id=profile_id, design_options=design_options,
                )
            else:
                saved = export_provider_png(
                    data, street, destination,
                    profile_id=profile_id, design_options=design_options,
                )
        except Exception as error:
            LOGGER.exception("%s export failed", provider_label)
            self.root.after(0, lambda detail=str(error): self._export_failed(provider_label, detail))
            return
        self.root.after(0, lambda: self._export_finished(provider_label, saved))

    def _export_finished(self, provider_label: str, destination: Path) -> None:
        self._set_export_buttons_state("normal" if self.state.selected_street else "disabled")
        dimensions = "2362 x 1063" if provider_label == "Inkthreadable" else "2475 x 1155"
        self.status_var.set(f"Export complete: {provider_label} {dimensions} PNG saved to {destination}")

    def _export_failed(self, provider_label: str, detail: str) -> None:
        self._set_export_buttons_state("normal" if self.state.selected_street else "disabled")
        LOGGER.error("%s export error: %s", provider_label, detail)
        self._show_error(f"Could not export {provider_label}: {detail}")

    def _set_export_buttons_state(self, state: str) -> None:
        reason = 'select a street'
        if not self._is_preprocessed_mode():
            state = 'disabled'
        else:
            record = self._selected_artwork_record()
            if record is None:
                state = 'disabled'
            elif not self._preprocessed_status(record).export_allowed:
                reason = 'production approval required'
                state = 'disabled'
            else:
                reason = self._qa_export_error()
                if reason:
                    state = 'disabled'
        self.export_button.configure(state=state)
        self.printify_export_button.configure(state=state)
        if 'export_state_var' in self.__dict__:
            self.export_state_var.set('Export: READY' if state == 'normal' else
                                     f"Export: BLOCKED - {reason or 'export temporarily unavailable'}")

    @staticmethod
    def _provider_filename(dataset: Dataset, street: StreetRecord, suffix: str) -> str:
        def slug(value: str, fallback: str) -> str:
            normalized = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-")
            return normalized or fallback
        return f"{slug(dataset.display_name, 'dataset')}_{slug(street.id, 'street')}_{slug(street.display_name, 'street')}_{slug(suffix, 'provider')}.png"

    @staticmethod
    def _inkthreadable_filename(dataset: Dataset, street: StreetRecord) -> str:
        return MugPreviewerApp._provider_filename(dataset, street, "inkthreadable")
    def _preview_resized(self, _event: object) -> None:
        if getattr(self, "_shutting_down", False):
            return
        if self._resize_pending is not None:
            self.root.after_cancel(self._resize_pending)
        self._resize_pending = self.root.after(100, self._refresh_preview_images)

    def _refresh_preview_images(self) -> None:
        self._resize_pending = None
        self._front_photo = self._set_preview(self.front_label, self.state.current_front_preview)
        self._rear_photo = self._set_preview(self.rear_label, self.state.current_rear_preview)
        if 'mug_front_label' in self.__dict__:
            self._mug_front_photo = self._set_preview(self.mug_front_label, self._mug_front_image)
            self._wrap_photo = self._set_preview(self.wrap_label, self.state.current_wrap)

    def _set_preview(self, label: ttk.Label, image: Image.Image | None) -> ImageTk.PhotoImage | None:
        if image is None:
            text = "No cached preview for this view" if self._is_preprocessed_mode() else "Render a street to view this mug"
            label.configure(image="", text=text)
            return None
        width, height = max(80, label.winfo_width() - 12), max(80, label.winfo_height() - 12)
        photo = ImageTk.PhotoImage(display_image(image, (width, height)))
        label.configure(image=photo, text="")
        return photo

    def _clear_previews(self) -> None:
        self._front_photo = self._rear_photo = None
        text = "No cached preview" if self._is_preprocessed_mode() else "Render a street to view this mug"
        self.front_label.configure(image="", text=text)
        self.rear_label.configure(image="", text=text)
        if 'mug_front_label' in self.__dict__:
            self._mug_front_image = None
            self._mug_front_photo = self._wrap_photo = None
            self.mug_front_label.configure(image='', text=text)
            self.wrap_label.configure(image='', text=text)

    def _show_error(self, detail: str) -> None:
        self.state.error = detail
        self.status_var.set(detail)
        messagebox.showerror("Mug Previewer", detail, parent=self.root)


def launch(*, dataset_root: Path | None = None, preprocessed: Path | None = None) -> int:
    """Create the native desktop shell and begin the Tk event loop."""
    root = tk.Tk()
    try:
        MugPreviewerApp(root, dataset_root=dataset_root, preprocessed=preprocessed)
    except UIDataError as error:
        messagebox.showerror("Mug Previewer", str(error), parent=root)
        root.destroy()
        return 2
    root.mainloop()
    return 0
