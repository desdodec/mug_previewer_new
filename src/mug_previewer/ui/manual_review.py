"""Testable constrained desktop workflow for production manual review."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from importlib.resources import files
from pathlib import Path
from typing import Callable, Iterable, Literal

from PIL import Image

from ..datasets.models import Dataset, StreetRecord
from ..diagnostics.front_candidates import Candidate, ProductionTriageStatus, render_production_masks, select_production_placement
from ..diagnostics.manual_review import ManualReviewItem, make_manual_review_item
from ..manual import (
    ALLOWED_ORIENTATIONS, ALLOWED_SCALES, ALLOWED_Y_OFFSETS,
    DEFAULT_MANUAL_OVERRIDE_PATH, ManualOverrideError, ManualPlacementOverride,
    clear_manual_override, load_manual_overrides, save_manual_override,
)
from ..rendering import face
from ..rendering.face import FaceRenderOptions, render_face

ReviewFilter = Literal["pending", "resolved", "all"]
FaceRenderer = Callable[[StreetRecord, FaceRenderOptions], Image.Image]
REASON_TEXT = {
    "standard_nose_overlap": "Mouth overlaps nose",
    "standard_low_nose_clearance": "Mouth is too close to nose",
    "standard_mouth_role_outlier": "Street sits outside the preferred mouth area",
    "unresolved_geometry": "No automatic placement was confident enough",
    "adaptation_low_confidence": "Automatic alternative remained visually uncertain",
}

@dataclass(frozen=True)
class ReviewRecord:
    dataset: Dataset
    street: StreetRecord
    item: ManualReviewItem
    @property
    def key(self) -> tuple[str, str]: return self.dataset.id, self.street.id
    @property
    def reason_text(self) -> str:
        return "; ".join(REASON_TEXT.get(code, code.replace("_", " ").capitalize()) for code in self.item.reason_codes)

@dataclass(frozen=True)
class _QueueCandidate:
    orientation_deg: int = 0
    scale: float = 1.0
    y_offset: int = 0


@dataclass(frozen=True)
class _QueueSource:
    dataset: Dataset
    street: StreetRecord
    triage_status: ProductionTriageStatus | None = None
    reason_codes: tuple[str, ...] = ()


def _production_scope_sources(datasets: tuple[Dataset, ...]) -> tuple[_QueueSource, ...]:
    """Use the accepted production scope when every stable key is available."""
    rows = json.loads(files("mug_previewer.ui").joinpath("manual_review_scope.json").read_text(encoding="utf-8"))
    by_display = {dataset.display_name: dataset for dataset in datasets}
    sources: list[_QueueSource] = []
    for row in rows:
        dataset = by_display.get(row["dataset"])
        if dataset is None:
            return ()
        street = next((item for item in dataset.streets if item.id == row["street_id"]), None)
        if street is None:
            return ()
        sources.append(_QueueSource(
            dataset, street, ProductionTriageStatus(row["triage_status"]),
            tuple(filter(None, row["reason_codes"].split(";"))),
        ))
    return tuple(sources)


class ManualReviewController:
    """Queue/edit state; triage loads incrementally and remains session-cached."""

    def __init__(
        self, datasets: Iterable[Dataset], *, override_path: Path | str = DEFAULT_MANUAL_OVERRIDE_PATH,
        face_renderer: FaceRenderer = render_face, eager: bool = True,
    ) -> None:
        self.datasets, self.override_path, self.face_renderer = tuple(datasets), Path(override_path), face_renderer
        self.filter: ReviewFilter = "pending"
        self.records: tuple[ReviewRecord, ...] = ()
        self.visible_records: tuple[ReviewRecord, ...] = ()
        self.current_key: tuple[str, str] | None = None
        self.loaded_transform: tuple[int, float, int] = (0, 1.0, 0)
        self.transform = self.loaded_transform
        scoped = _production_scope_sources(self.datasets)
        self._sources = scoped or tuple(
            _QueueSource(dataset, street)
            for dataset, street in sorted(
                ((dataset, street) for dataset in self.datasets for street in dataset.streets),
                key=lambda pair: (pair[0].id, pair[1].id, pair[1].display_name),
            )
        )
        self._source_index = 0
        self._triage_cache: dict[tuple[str, str], object] = {}
        self._override_store = load_manual_overrides(self.override_path)
        self._unrenderable_count = 0
        if eager:
            self.load_all()

    @property
    def current(self) -> ReviewRecord | None:
        return next((record for record in self.visible_records if record.key == self.current_key), None)

    @property
    def pending_count(self) -> int:
        return sum(record.item.manual_override is None or not record.item.manual_override.approved for record in self.records)

    @property
    def resolved_count(self) -> int:
        return len(self.records) - self.pending_count

    @property
    def unrenderable_count(self) -> int:
        return self._unrenderable_count

    @property
    def loading(self) -> bool:
        return self._source_index < len(self._sources)

    @property
    def load_progress(self) -> tuple[int, int]:
        return self._source_index, len(self._sources)

    @property
    def progress(self) -> tuple[int, int]:
        return (self.visible_records.index(self.current) + 1, len(self.visible_records)) if self.current else (0, len(self.visible_records))

    def load_next(self) -> bool:
        """Evaluate exactly one source street and return whether loading remains."""
        if not self.loading:
            return False
        source = self._sources[self._source_index]
        dataset, street = source.dataset, source.street
        key = dataset.id, street.id
        if source.triage_status is None:
            decision = self._triage_cache.get(key)
            if decision is None:
                decision = select_production_placement(street, area=dataset.display_name)
                self._triage_cache[key] = decision
            item = make_manual_review_item(dataset.id, street, decision, self._override_store.get(*key))
        elif source.triage_status is ProductionTriageStatus.MANUAL_REVIEW:
            candidate = _QueueCandidate()
            item = ManualReviewItem(
                dataset.id, street.id, street.display_name, source.triage_status, None,
                candidate, candidate, source.reason_codes, manual_override=self._override_store.get(*key),
            )
            decision = None
        else:
            item = None
            decision = source
        self._source_index += 1
        if item is not None:
            self.records = tuple(sorted((*self.records, ReviewRecord(dataset, street, item)), key=lambda record: (record.dataset.id, record.street.id, record.street.display_name)))
        elif source.triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT or (
            decision is not None and decision.triage_status is ProductionTriageStatus.UNRENDERABLE_INPUT
        ):
            self._unrenderable_count += 1
        self._apply_filter(self.current_key)
        return self.loading

    def load_all(self) -> None:
        while self.loading:
            self.load_next()

    def refresh(self) -> None:
        """Refresh only saved-resolution metadata; never rerun production triage."""
        self._override_store = load_manual_overrides(self.override_path)
        self.records = tuple(
            replace(record, item=replace(record.item, manual_override=self._override_store.get(*record.key)))
            for record in self.records
        )
        self._apply_filter(self.current_key)

    def set_filter(self, value: ReviewFilter) -> None:
        if value not in ("pending", "resolved", "all"):
            raise ValueError("Review filter must be pending, resolved, or all.")
        self.filter = value
        self._apply_filter(self.current_key)

    def select(self, key: tuple[str, str]) -> ReviewRecord:
        record = next((item for item in self.visible_records if item.key == key), None)
        if record is None:
            raise ValueError("Selected street is not available in the current review queue.")
        self.current_key = key
        override, candidate = record.item.manual_override, record.item.candidate
        self.loaded_transform = (
            (int(override.orientation_deg), float(override.scale), int(override.y_offset))
            if override and override.approved else (candidate.orientation_deg, candidate.scale, candidate.y_offset)
        )
        self.transform = self.loaded_transform
        return record

    def select_index(self, index: int) -> ReviewRecord | None:
        if not self.visible_records:
            self.current_key = None
            return None
        return self.select(self.visible_records[max(0, min(index, len(self.visible_records) - 1))].key)

    def previous(self) -> ReviewRecord | None:
        current, _ = self.progress
        return self.select_index(current - 2) if current else None

    def next(self) -> ReviewRecord | None:
        current, _ = self.progress
        return self.select_index(current) if current else None

    def next_pending(self) -> ReviewRecord | None:
        pending = [record for record in self.records if record.item.manual_override is None or not record.item.manual_override.approved]
        if not pending:
            return None
        target = pending[0] if self.current is None else next((record for record in pending if record.key > self.current.key), pending[0])
        if target not in self.visible_records:
            self.set_filter("pending")
        return self.select(target.key)

    def set_transform(self, orientation: int, scale: float, y_offset: int) -> None:
        if orientation not in ALLOWED_ORIENTATIONS or scale not in ALLOWED_SCALES or y_offset not in ALLOWED_Y_OFFSETS:
            raise ValueError("The selected placement is outside approved manual transform bounds.")
        self.transform = orientation, scale, y_offset

    def use_standard(self) -> None:
        self.transform = (0, 1.0, 0)

    def use_best_candidate(self) -> None:
        if self.current:
            candidate = self.current.item.candidate
            self.transform = candidate.orientation_deg, candidate.scale, candidate.y_offset

    def reset_edit(self) -> None:
        self.transform = self.loaded_transform

    def render_previews(self) -> tuple[Image.Image, Image.Image]:
        """Render only the selected street through the production face seam."""
        record = self._require_current()
        if isinstance(record.item.standard_candidate, _QueueCandidate):
            decision = select_production_placement(record.street, area=record.dataset.display_name)
            self._triage_cache[record.key] = decision
            item = make_manual_review_item(record.dataset.id, record.street, decision, self._override_store.get(*record.key))
            if item is None:
                raise ManualOverrideError("This queued street is no longer eligible for manual review.")
            self.records = tuple(replace(value, item=item) if value.key == record.key else value for value in self.records)
            self.select(record.key)
            record = self._require_current()
        orientation, scale, y_offset = self.transform
        temporary = ManualPlacementOverride.approved_transform(
            record.dataset.id, record.street.id, record.street.display_name,
            orientation_deg=orientation, scale=scale, y_offset=y_offset,
        )
        options = FaceRenderOptions(area=record.dataset.display_name)
        if self.face_renderer is not render_face:
            standard = self.face_renderer(record.street, options)
            return standard, self.face_renderer(
                record.street, FaceRenderOptions(area=record.dataset.display_name, manual_override=temporary),
            )
        # Reuse the production renderer's own standard panel and constrained-mask
        # repaint seam.  This avoids rerunning placement triage for each panel.
        standard = face._render_face_standard(record.street, options)
        if temporary.transform == (0, 1.0, 0):
            return standard, standard.copy()
        masks = render_production_masks(record.street, area=record.dataset.display_name, base=standard)
        current = face._render_transformed_street(
            standard, masks, Candidate(orientation, scale, 0, y_offset),
        )
        return standard, current

    def approve_standard(self) -> ReviewRecord | None:
        record = self._require_current()
        return self._save_and_advance(
            ManualPlacementOverride.approved_standard(record.dataset.id, record.street.id, record.street.display_name),
        )

    def approve_current_edit(self) -> ReviewRecord | None:
        record = self._require_current()
        orientation, scale, y_offset = self.transform
        return self._save_and_advance(ManualPlacementOverride.approved_transform(
            record.dataset.id, record.street.id, record.street.display_name,
            orientation_deg=orientation, scale=scale, y_offset=y_offset,
        ))

    def clear_saved_decision(self) -> ReviewRecord:
        record = self._require_current()
        if record.item.manual_override is None or not record.item.manual_override.approved:
            raise ManualOverrideError("This street has no saved manual decision to clear.")
        clear_manual_override(record.dataset.id, record.street.id, self.override_path)
        self._replace_override(record.key, None)
        restored = next(item for item in self.records if item.key == record.key)
        if self.filter == "pending":
            self.select(restored.key)
        return restored

    def _save_and_advance(self, decision: ManualPlacementOverride) -> ReviewRecord | None:
        old_index = self.visible_records.index(self._require_current())
        save_manual_override(decision, self.override_path)
        self._replace_override(decision.key, decision)
        return self.select_index(old_index)

    def _replace_override(self, key: tuple[str, str], override: ManualPlacementOverride | None) -> None:
        self.records = tuple(
            replace(record, item=replace(record.item, manual_override=override)) if record.key == key else record
            for record in self.records
        )
        self._apply_filter(self.current_key)

    def _apply_filter(self, preferred_key: tuple[str, str] | None) -> None:
        pending = lambda record: record.item.manual_override is None or not record.item.manual_override.approved
        shown = (
            [record for record in self.records if pending(record)] if self.filter == "pending"
            else [record for record in self.records if not pending(record)] if self.filter == "resolved"
            else list(self.records)
        )
        self.visible_records = tuple(shown)
        if preferred_key and any(record.key == preferred_key for record in self.visible_records):
            if preferred_key != self.current_key:
                self.select(preferred_key)
        elif self.visible_records:
            self.select(self.visible_records[0].key)
        else:
            self.current_key = None

    def _require_current(self) -> ReviewRecord:
        if self.current is None:
            raise ValueError("Select a manual-review street first.")
        return self.current


# Tk is imported below so controller-only tests do not require a display.
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import ImageTk

class ManualReviewWindow(tk.Toplevel):
    """Tk review window that remains responsive while triage loads incrementally."""

    def __init__(
        self, parent: tk.Tk, controller: ManualReviewController, *,
        initial_key: tuple[str, str] | None = None, on_resolution_changed: Callable[[tuple[str, str]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller, self._standard_photo, self._current_photo = controller, None, None
        self._initial_key, self._on_resolution_changed = initial_key, on_resolution_changed
        self._updating = False
        self.title("Mug Previewer - Manual Review")
        self.minsize(1050, 700)
        self.columnconfigure(1, weight=1); self.rowconfigure(1, weight=1)
        self.summary_var, self.identity_var = tk.StringVar(), tk.StringVar()
        self.reason_var, self.progress_var = tk.StringVar(), tk.StringVar()
        header = ttk.Frame(self, padding=12); header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text="Manual Review", font=("TkDefaultFont", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.summary_var).grid(row=0, column=1, sticky="e"); header.columnconfigure(1, weight=1)
        left = ttk.Frame(self, padding=(12, 0, 8, 12)); left.grid(row=1, column=0, sticky="ns")
        self.filter_var = tk.StringVar(value="pending")
        ttk.Label(left, text="Queue").pack(anchor="w")
        ttk.Combobox(left, textvariable=self.filter_var, values=("pending", "resolved", "all"), state="readonly", width=16).pack(fill="x", pady=(3, 7))
        self.filter_var.trace_add("write", lambda *_: self._change_filter())
        self.queue = tk.Listbox(left, width=38, height=26, exportselection=False); self.queue.pack(fill="y", expand=True)
        self.queue.bind("<<ListboxSelect>>", self._queue_selected)
        main = ttk.Frame(self, padding=(8, 0, 12, 12)); main.grid(row=1, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1); main.columnconfigure(1, weight=1); main.rowconfigure(2, weight=1)
        ttk.Label(main, textvariable=self.identity_var, font=("TkDefaultFont", 13, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(main, textvariable=self.reason_var, wraplength=700).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        self.standard_label = self._preview(main, "STANDARD", 0)
        self.current_label = self._preview(main, "CURRENT EDIT", 1)
        controls = ttk.LabelFrame(main, text="Allowed street-mouth placement", padding=8); controls.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.orientation_var, self.scale_var, self.y_var = tk.IntVar(), tk.StringVar(), tk.IntVar()
        for variable in (self.orientation_var, self.scale_var, self.y_var):
            variable.trace_add("write", lambda *_: self._controls_changed())
        ttk.Label(controls, text="Orientation").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(controls, text="0 deg", value=0, variable=self.orientation_var).grid(row=0, column=1)
        ttk.Radiobutton(controls, text="180 deg", value=180, variable=self.orientation_var).grid(row=0, column=2)
        ttk.Label(controls, text="Scale").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Combobox(controls, textvariable=self.scale_var, values=tuple(f"{value:.2f}" for value in ALLOWED_SCALES), state="readonly", width=8).grid(row=1, column=1, sticky="w", pady=(5, 0))
        ttk.Label(controls, text="Vertical").grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Combobox(controls, textvariable=self.y_var, values=ALLOWED_Y_OFFSETS, state="readonly", width=8).grid(row=2, column=1, sticky="w", pady=(5, 0))
        buttons = ttk.Frame(controls); buttons.grid(row=3, column=0, columnspan=4, sticky="w", pady=(9, 0))
        for text, command in (("Use Standard", self._use_standard), ("Use Best Candidate", self._use_best), ("Reset Edit", self._reset), ("Approve Standard", self._approve_standard), ("Approve Current Edit", self._approve_edit), ("Leave Pending", self._next), ("Clear Saved Decision", self._clear)):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 5))
        bottom = ttk.Frame(main); bottom.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bottom, text="Previous", command=self._previous).pack(side="left")
        ttk.Label(bottom, textvariable=self.progress_var).pack(side="left", padx=18)
        ttk.Button(bottom, text="Next Pending", command=self._next_pending).pack(side="right")
        ttk.Button(bottom, text="Next", command=self._next).pack(side="right", padx=(0, 5))
        self._refresh(render=False)
        self.after_idle(self._load_next)

    def _preview(self, parent: ttk.Frame, title: str, column: int) -> ttk.Label:
        card = ttk.LabelFrame(parent, text=title, padding=5)
        card.grid(row=2, column=column, sticky="nsew", padx=(0, 4) if column == 0 else (4, 0))
        card.rowconfigure(0, weight=1); card.columnconfigure(0, weight=1)
        label = ttk.Label(card, anchor="center"); label.grid(sticky="nsew")
        return label

    def _load_next(self) -> None:
        previous_key = self.controller.current_key
        more = self.controller.load_next()
        if self._initial_key and any(record.key == self._initial_key for record in self.controller.records):
            self.controller.set_filter("pending")
            self.controller.select(self._initial_key)
            self._initial_key = None
        self._refresh(render=self.controller.current_key != previous_key)
        if more and self.winfo_exists():
            self.after(1, self._load_next)

    def _refresh(self, *, render: bool = True) -> None:
        controller = self.controller
        processed, total = controller.load_progress
        loading = f"Loading manual-review queue: processed {processed} of {total}" if controller.loading else "Manual-review queue loaded"
        self.summary_var.set(f"{loading}   Pending: {controller.pending_count}   Resolved: {controller.resolved_count}   Unrenderable: {controller.unrenderable_count}")
        self.queue.delete(0, tk.END)
        for record in controller.visible_records:
            self.queue.insert(tk.END, f"{record.dataset.display_name} | {record.street.id} | {record.street.display_name} - {record.reason_text}")
        record = controller.current
        if record is None:
            self.identity_var.set("Loading manual-review queue..." if controller.loading else "Manual review complete. No streets currently require review.")
            self.reason_var.set(""); self.progress_var.set(f"Processed {processed} of {total}")
            self.standard_label.configure(image="", text=""); self.current_label.configure(image="", text="")
            return
        index, total_visible = controller.progress
        self.queue.selection_set(index - 1)
        self.identity_var.set(f"{record.dataset.display_name} / {record.street.id} / {record.street.display_name}")
        status = "Pending Review" if record.item.manual_override is None else ("Approved Standard" if record.item.manual_override.status.value == "approved_standard" else "Approved Manual Edit")
        self.reason_var.set(f"{status}. Reason: {record.reason_text}")
        self.progress_var.set(f"Review {index} of {total_visible}; processed {processed} of {total}")
        self._updating = True
        self.orientation_var.set(controller.transform[0]); self.scale_var.set(f"{controller.transform[1]:.2f}"); self.y_var.set(controller.transform[2])
        self._updating = False
        if render:
            self._render()

    def _render(self) -> None:
        try:
            standard, current = self.controller.render_previews()
        except Exception as error:
            self.reason_var.set(f"{self.reason_var.get()}  Preview error: {error}")
            return
        self._standard_photo = ImageTk.PhotoImage(standard.resize((495, 462)))
        self._current_photo = ImageTk.PhotoImage(current.resize((495, 462)))
        self.standard_label.configure(image=self._standard_photo, text="")
        self.current_label.configure(image=self._current_photo, text="")

    def _change_filter(self) -> None:
        self.controller.set_filter(self.filter_var.get()); self._refresh(render=False)

    def _queue_selected(self, _event: object) -> None:
        if self.queue.curselection():
            self.controller.select_index(self.queue.curselection()[0]); self._refresh()

    def _controls_changed(self) -> None:
        if self._updating or not self.controller.current or not self.scale_var.get():
            return
        try:
            self.controller.set_transform(self.orientation_var.get(), float(self.scale_var.get()), self.y_var.get())
            self._render()
        except (ValueError, tk.TclError):
            return

    def _use_standard(self) -> None: self.controller.use_standard(); self._refresh()
    def _use_best(self) -> None: self.controller.use_best_candidate(); self._refresh()
    def _reset(self) -> None: self.controller.reset_edit(); self._refresh()
    def _previous(self) -> None: self.controller.previous(); self._refresh()
    def _next(self) -> None: self.controller.next(); self._refresh()
    def _next_pending(self) -> None: self.controller.next_pending(); self._refresh()
    def _approve_standard(self) -> None: self._save(self.controller.approve_standard, "Saved approved STANDARD.")
    def _approve_edit(self) -> None: self._save(self.controller.approve_current_edit, f"Saved manual placement: {self.controller.transform[0]} deg / {self.controller.transform[1]:.2f} / Y{self.controller.transform[2]:+d}.")

    def _save(self, action: Callable[[], object], message: str) -> None:
        changed_key = self.controller.current_key
        try:
            action()
        except Exception as error:
            messagebox.showerror("Manual Review", f"Could not save manual decision. The street remains pending.\n\n{error}", parent=self)
            return
        self._refresh(); self.reason_var.set(message)
        if self._on_resolution_changed is not None and changed_key is not None:
            self._on_resolution_changed(changed_key)

    def _clear(self) -> None:
        if not self.controller.current or self.controller.current.item.manual_override is None:
            return
        if not messagebox.askyesno("Clear saved decision", "Return this street to pending manual review?", parent=self):
            return
        changed_key = self.controller.current_key
        try:
            self.controller.clear_saved_decision()
        except Exception as error:
            messagebox.showerror("Manual Review", str(error), parent=self)
            return
        self._refresh()
        if self._on_resolution_changed is not None and changed_key is not None:
            self._on_resolution_changed(changed_key)
