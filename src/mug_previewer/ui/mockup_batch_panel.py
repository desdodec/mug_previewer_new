"""Batch controls for exporting reusable studio mug mockup photos."""

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from ..manual_svg_workspace import open_local_path
from ..mockup_batch import (
    STUDIO_MOCKUP_STYLE_ID,
    build_mockup_batch_plan,
    execute_mockup_batch,
)


MOCKUP_STYLES = {
    "Studio white mug — front + rear": STUDIO_MOCKUP_STYLE_ID,
}


def result_summary(result):
    s = result.summary
    heading = "Mockup batch cancelled" if result.cancelled else "Mockup batch finished"
    return (
        f"{heading}: {s['exported']} pairs exported, {s['failed']} failed\n"
        f"Included: {s['ready']} | Excluded: {s['excluded']}\n"
        f"Unrenderable: {s['unrenderable']} | Asset errors: {s['asset_errors']}\n"
        f"Existing pairs: {s['skipped_existing']} | Cancelled: {s['cancelled']}"
    )


class MockupBatchPanel(ttk.LabelFrame):
    def __init__(self, parent, app):
        super().__init__(parent, text="Export studio mockup photos", padding=6)
        self.app = app
        self.plan = None
        self.busy = False
        self.events = queue.SimpleQueue()
        self.cancel_event = threading.Event()
        self.generation = 0
        self.report_path = None
        self.dataset = tk.StringVar()
        self.dataset_by_label = {}
        self.style = tk.StringVar(value=next(iter(MOCKUP_STYLES)))
        self.destination = tk.StringVar()
        self.policy = tk.StringVar(value="Skip existing")
        self.summary = tk.StringVar(
            value="Choose a folder to batch-render front and rear studio mug photos from included prepared faces."
        )
        self.progress = tk.StringVar()
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text="Prepared face set").grid(row=0, column=0, sticky="w")
        self.dataset_box = ttk.Combobox(self, textvariable=self.dataset, state="readonly")
        self.dataset_box.grid(row=1, column=0, sticky="ew")
        self.dataset_box.bind("<<ComboboxSelected>>", self.select_export_dataset)

        ttk.Label(self, text="Mockup style").grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.style_box = ttk.Combobox(
            self, textvariable=self.style, values=list(MOCKUP_STYLES), state="readonly"
        )
        self.style_box.grid(row=3, column=0, sticky="ew")
        self.style_box.bind("<<ComboboxSelected>>", lambda _e: self.invalidate())

        self.choose = ttk.Button(
            self, text="1. Choose mockup export folder", command=self.choose_folder
        )
        self.choose.grid(row=4, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(self, textvariable=self.destination, wraplength=320).grid(
            row=5, column=0, sticky="w"
        )

        self.policy_box = ttk.Combobox(
            self,
            textvariable=self.policy,
            values=["Skip existing", "Replace existing"],
            state="readonly",
        )
        self.policy_box.grid(row=6, column=0, sticky="ew", pady=(7, 0))
        self.policy_box.bind("<<ComboboxSelected>>", lambda _e: self.invalidate())

        ttk.Label(self, textvariable=self.summary, wraplength=320).grid(
            row=7, column=0, sticky="w", pady=(7, 0)
        )
        self.refresh = ttk.Button(
            self, text="Recheck mockup readiness", command=self.refresh_plan, state="disabled"
        )
        self.refresh.grid(row=8, column=0, sticky="ew")
        self.start = ttk.Button(
            self, text="2. Export ALL included mockup pairs", command=self.start_batch, state="disabled"
        )
        self.start.grid(row=9, column=0, sticky="ew", pady=(5, 0))
        self.cancel = ttk.Button(
            self, text="Cancel Mockup Export", command=self.cancel_event.set, state="disabled"
        )
        self.cancel.grid(row=10, column=0, sticky="ew")
        ttk.Label(self, textvariable=self.progress, wraplength=320).grid(
            row=11, column=0, sticky="w"
        )

        links = ttk.Frame(self)
        links.grid(row=12, column=0, sticky="ew")
        self.folder_button = ttk.Button(
            links, text="Open Mockup Folder", command=self.open_folder, state="disabled"
        )
        self.folder_button.pack(side="left")
        self.report_button = ttk.Button(
            links, text="View Mockup Report", command=self.open_report, state="disabled"
        )
        self.report_button.pack(side="left")

        ttk.Label(
            self,
            text=(
                "Outputs are preview photos only. Production/provider PNGs are untouched. "
                "Each included face gets a native-resolution front and rear PNG."
            ),
            wraplength=320,
        ).grid(row=13, column=0, sticky="w", pady=(8, 0))

        self.refresh_dataset_options()
        self.bind("<Visibility>", lambda _event: self.refresh_dataset_options())
        self.after(50, self.drain)

    def refresh_dataset_options(self):
        options = list(getattr(self.app.state, "datasets", ()) or ())
        updated = {item.label: item.dataset for item in options}
        old_signature = tuple((label, data.id) for label, data in self.dataset_by_label.items())
        old_label = self.dataset.get()
        self.dataset_by_label = updated
        self.dataset_box["values"] = list(updated)
        current = getattr(self.app.state, "selected_dataset", None)
        current_id = current.id if current is not None else None
        label = next((name for name, data in updated.items() if data.id == current_id), None)
        if label is not None:
            self.dataset.set(label)
        elif self.dataset.get() not in updated:
            self.dataset.set("")
        new_signature = tuple((name, data.id) for name, data in updated.items())
        if new_signature != old_signature or self.dataset.get() != old_label:
            self.invalidate()

    def _selected_dataset(self):
        label = self.dataset.get()
        if label in self.dataset_by_label:
            return self.dataset_by_label[label]
        if label:
            return None
        return getattr(self.app.state, "selected_dataset", None)

    def select_export_dataset(self, _event=None):
        data = self._selected_dataset()
        if data is None:
            self.invalidate()
            return
        current = getattr(self.app.state, "selected_dataset", None)
        if current is None or current.id != data.id:
            main_mapping = getattr(self.app, "dataset_by_label", {})
            main_label = next(
                (label for label, item in main_mapping.items() if item.id == data.id),
                None,
            )
            if main_label is not None and hasattr(self.app, "dataset_var"):
                self.app.dataset_var.set(main_label)
                self.app._select_dataset()
            else:
                self.app.state.selected_dataset = data
        self.invalidate()

    def invalidate(self, *, rebuild=True):
        self.plan = None
        if self.busy:
            self._plan_invalidated = True
            return
        self.generation += 1
        self.start.configure(state="disabled", text="2. Export ALL included mockup pairs")
        ready = self._selected_dataset() is not None and bool(self.destination.get())
        self.summary.set(
            "Preparing mockup export readiness..."
            if ready
            else "Choose a folder to batch-render front and rear studio mug photos."
        )
        self.refresh.configure(state="normal" if ready else "disabled")
        self.folder_button.configure(
            state="normal" if self.destination.get() or self.report_path else "disabled"
        )
        if ready and rebuild:
            generation = self.generation
            self.after(
                150,
                lambda: self.refresh_plan()
                if generation == self.generation and not self.busy
                else None,
            )

    def choose_folder(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(
            parent=self.app.root, title="Choose folder for exported mockup photos"
        )
        if selected:
            self.destination.set(selected)
            self.invalidate()
            if self._selected_dataset() is not None:
                self.refresh_plan()

    def set_busy(self, busy, *, exporting=False):
        self.busy = busy
        self.choose.configure(state="disabled" if busy else "normal")
        data = self._selected_dataset()
        self.refresh.configure(
            state="normal" if not busy and data is not None and self.destination.get() else "disabled"
        )
        for widget in (self.style_box, self.policy_box, self.dataset_box):
            widget.configure(state="disabled" if busy else "readonly")
        self.start.configure(state="disabled")
        self.cancel.configure(state="normal" if exporting else "disabled")
        self.app.dataset_box.configure(state="disabled" if busy else "readonly")

    def refresh_plan(self):
        if self.busy:
            return
        data = self._selected_dataset()
        if data is None or not self.destination.get():
            self.invalidate()
            self.summary.set("Choose a prepared face set and mockup export folder first.")
            return
        self.invalidate(rebuild=False)
        self._plan_invalidated = False
        self.set_busy(True)
        self.summary.set(f"Checking included faces in {data.display_name}...")
        args = (
            self.app.preprocessed_catalogue.root,
            data,
            Path(self.destination.get()),
        )
        options = {
            "replace_existing": self.policy.get() == "Replace existing",
            "design_options": self.app.state.design_options,
            "style_id": MOCKUP_STYLES[self.style.get()],
        }
        threading.Thread(
            target=self.plan_worker,
            args=(self.generation, args, options),
            daemon=True,
        ).start()

    def plan_worker(self, generation, args, options):
        try:
            self.events.put(
                ("plan", generation, build_mockup_batch_plan(*args, **options))
            )
        except Exception as error:
            self.events.put(("error", generation, str(error)))

    def start_batch(self):
        data = self._selected_dataset()
        if self.busy or self.plan is None or not self.plan.summary.ready or not self.destination.get():
            return
        if data is None or self.plan.dataset.id != data.id:
            self.invalidate()
            return
        if self.app.state.design_options != self.plan.design_options:
            self.invalidate()
            return
        self.cancel_event.clear()
        self.set_busy(True, exporting=True)
        self.summary.set(
            f"Mockup export is running: {self.plan.summary.ready} front/rear pairs."
        )
        self.progress.set(f"Exporting 0 / {self.plan.summary.ready}")
        threading.Thread(
            target=self.export_worker,
            args=(self.generation, self.plan),
            daemon=False,
        ).start()

    def export_worker(self, generation, plan):
        try:
            result = execute_mockup_batch(
                plan,
                cancel_event=self.cancel_event,
                on_progress=lambda p: self.events.put(("progress", generation, p)),
            )
            self.events.put(("result", generation, result))
        except Exception as error:
            self.events.put(("error", generation, str(error)))

    def drain(self):
        if self.app._shutting_down:
            self.cancel_event.set()
            return
        while True:
            try:
                kind, generation, value = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            if kind == "progress":
                self.progress.set(
                    f"Exporting {value.current} / {value.total}\n"
                    f"{value.result.item.source.street_id} - "
                    f"{value.result.item.source.street_name}: {value.result.result}"
                )
                continue

            self.set_busy(False)
            if kind == "plan":
                if (
                    getattr(self, "_plan_invalidated", False)
                    or value.design_options != self.app.state.design_options
                ):
                    self.invalidate()
                    continue
                self.plan = value
                s = value.summary
                self.summary.set(
                    f"{value.dataset.display_name}: {s.total} prepared\n"
                    f"Ready for mockups: {s.ready} | Excluded: {s.excluded}\n"
                    f"Unrenderable: {s.unrenderable} | Asset errors: {s.asset_errors}\n"
                    f"Existing complete pairs: {s.existing_pairs}\n"
                    f"Output: front/ and rear/ native-resolution PNGs."
                )
                self.start.configure(
                    text=f"2. Export ALL {s.ready} front + rear mockup pairs",
                    state="normal" if s.ready else "disabled",
                )
            elif kind == "result":
                self.report_path = value.report_path
                self.report_button.configure(state="normal")
                self.summary.set(result_summary(value))
                self.progress.set("Mockup report saved.")
                self.plan = None
                self.after(150, self.invalidate)
            else:
                self.plan = None
                self.summary.set(f"Mockup batch cannot start or finish: {value}")
        self.after(50, self.drain)

    def open_folder(self):
        path = (
            self.report_path.parent
            if self.report_path
            else (
                Path(self.destination.get())
                / "mockups"
                / sanitize_dataset_id(self._selected_dataset())
                if self.destination.get() and self._selected_dataset() is not None
                else None
            )
        )
        if path is not None and not path.exists():
            path = path.parent
        self._open(path)

    def open_report(self):
        self._open(self.report_path)

    def _open(self, path):
        try:
            if path is None:
                raise ValueError("Choose a mockup export folder first")
            open_local_path(path)
        except Exception as error:
            self.app._show_error(str(error))


def sanitize_dataset_id(dataset) -> str:
    from ..batch_export import sanitize_filename
    return sanitize_filename(dataset.id)
