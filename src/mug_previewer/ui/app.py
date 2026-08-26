"""Small Tkinter shell for selecting streets and viewing production mockups."""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from ..datasets.models import Dataset, StreetRecord
from .state import (
    AppState,
    DatasetOption,
    PreviewPair,
    UIDataError,
    dataset_options,
    display_image,
    filter_streets,
    render_preview_pair,
    resolve_dataset_root,
)

LOGGER = logging.getLogger(__name__)


class MugPreviewerApp(ttk.Frame):
    """Widget layer that delegates data, rendering, and image operations to state."""

    def __init__(self, root: tk.Tk, *, dataset_root: Path | None = None) -> None:
        super().__init__(root, padding=14)
        self.root = root
        self.state = AppState(dataset_root=resolve_dataset_root(dataset_root))
        self.dataset_by_label: dict[str, Dataset] = {}
        self._front_photo: ImageTk.PhotoImage | None = None
        self._rear_photo: ImageTk.PhotoImage | None = None
        self._resize_pending: str | None = None
        self._build_widgets()
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.after_idle(self.refresh_datasets)

    def _build_widgets(self) -> None:
        self.root.title("Mug Previewer")
        self.root.minsize(1050, 650)
        self.columnconfigure(0, weight=0, minsize=285)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)

        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        ttk.Label(controls, text="Mug Previewer", font=("TkDefaultFont", 15, "bold")).grid(row=0, column=0, sticky="w")
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
        self.render_button = ttk.Button(controls, text="Render Preview", command=self._start_render, state="disabled")
        self.render_button.grid(row=6, column=0, sticky="ew")
        self.framing_var = tk.StringVar(value="Rear framing: —")
        ttk.Label(controls, textvariable=self.framing_var).grid(row=7, column=0, sticky="w", pady=(13, 0))

        self.front_card = self._preview_card("Front")
        self.front_card.grid(row=0, column=1, sticky="nsew", padx=(0, 7))
        self.rear_card = self._preview_card("Rear")
        self.rear_card.grid(row=0, column=2, sticky="nsew", padx=(7, 0))
        self.status_var = tk.StringVar(value="Loading datasets…")
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))

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
        self.state.selected_dataset = data
        self.state.selected_street = None
        self.state.current_wrap = self.state.current_front_preview = self.state.current_rear_preview = None
        self.state.framing_mode = None
        self.framing_var.set("Rear framing: —")
        self._clear_previews()
        self._apply_filter()
        self.status_var.set(f"{data.display_name}: {len(data.streets)} streets available.")

    def _filter_changed(self, *_args: object) -> None:
        self._apply_filter()

    def _apply_filter(self) -> None:
        data = self.state.selected_dataset
        if data is None:
            return
        self.state.street_filter = self.search_var.get()
        self.state.filtered_streets = filter_streets(data.streets, self.state.street_filter)
        self.street_list.delete(0, tk.END)
        for street in self.state.filtered_streets:
            self.street_list.insert(tk.END, f"{street.id} — {street.display_name}")
        self.state.selected_street = None
        self.render_button.configure(state="disabled")

    def _select_street(self, _event: object | None = None) -> None:
        selection = self.street_list.curselection()
        if not selection:
            return
        self.state.selected_street = self.state.filtered_streets[selection[0]]
        street = self.state.selected_street
        self.status_var.set(f"Selected: {street.id} — {street.display_name}")
        self.render_button.configure(state="normal")

    def _start_render(self) -> None:
        data, street = self.state.selected_dataset, self.state.selected_street
        if data is None or street is None:
            self._show_error("Select a dataset and street before rendering.")
            return
        self.render_button.configure(state="disabled")
        self.status_var.set(f"Rendering {street.id} — {street.display_name}…")
        self.state.render_status = "Rendering"
        threading.Thread(target=self._render_worker, args=(data, street), daemon=True).start()

    def _render_worker(self, data: Dataset, street: StreetRecord) -> None:
        try:
            pair = render_preview_pair(data, street)
        except Exception as error:
            LOGGER.exception("Preview rendering failed")
            self.root.after(0, lambda: self._render_failed(str(error)))
            return
        self.root.after(0, lambda: self._render_finished(pair, data, street))

    def _render_finished(self, pair: PreviewPair, data: Dataset, street: StreetRecord) -> None:
        self.state.current_wrap = pair.wrap
        self.state.current_front_preview = pair.front
        self.state.current_rear_preview = pair.rear
        self.state.framing_mode = pair.framing_mode
        self.state.render_status = "Ready"
        self.framing_var.set(f"Rear framing: {pair.framing_mode}")
        self.status_var.set(f"Rendered {data.display_name} — {street.id} {street.display_name}")
        self._refresh_preview_images()
        self.render_button.configure(state="normal")

    def _render_failed(self, detail: str) -> None:
        self.state.render_status = "Error"
        self.render_button.configure(state="normal" if self.state.selected_street else "disabled")
        self._show_error(f"Could not render the selected street. {detail}")

    def _preview_resized(self, _event: object) -> None:
        if self._resize_pending is not None:
            self.root.after_cancel(self._resize_pending)
        self._resize_pending = self.root.after(100, self._refresh_preview_images)

    def _refresh_preview_images(self) -> None:
        self._resize_pending = None
        self._front_photo = self._set_preview(self.front_label, self.state.current_front_preview)
        self._rear_photo = self._set_preview(self.rear_label, self.state.current_rear_preview)

    @staticmethod
    def _set_preview(label: ttk.Label, image: Image.Image | None) -> ImageTk.PhotoImage | None:
        if image is None:
            label.configure(image="", text="Render a street to view this mug")
            return None
        width, height = max(80, label.winfo_width() - 12), max(80, label.winfo_height() - 12)
        photo = ImageTk.PhotoImage(display_image(image, (width, height)))
        label.configure(image=photo, text="")
        return photo

    def _clear_previews(self) -> None:
        self._front_photo = self._rear_photo = None
        self.front_label.configure(image="", text="Render a street to view this mug")
        self.rear_label.configure(image="", text="Render a street to view this mug")

    def _show_error(self, detail: str) -> None:
        self.state.error = detail
        self.status_var.set(detail)
        messagebox.showerror("Mug Previewer", detail, parent=self.root)


def launch(*, dataset_root: Path | None = None) -> int:
    """Create the native desktop shell and begin the Tk event loop."""
    root = tk.Tk()
    MugPreviewerApp(root, dataset_root=dataset_root)
    root.mainloop()
    return 0
