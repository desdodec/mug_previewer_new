"""Standalone print-style calibration lab for front and rear mug artwork.

The helper deliberately exposes more controls than the production UI.  Its job
is to find robust physical-print settings, save them as a small JSON profile,
and leave production defaults untouched until those settings are adopted.
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from dataclasses import asdict, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from ..datasets.discovery import discover_datasets
from ..datasets.models import Dataset, StreetRecord
from ..rendering.context_map import (
    ContextRenderError,
    ContextRenderOptions,
    render_context_map_result,
)
from ..rendering.face import (
    FRONT_GROUP_SCALE,
    FRONT_GROUP_Y_OFFSET,
    FRONT_TITLE_LOCALITY_GAP_DELTA_PX,
    FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX,
    STREET_STROKE_MULTIPLIER,
    FaceRenderError,
    FaceRenderOptions,
    _render_face_standard,
)


PROFILE_VERSION = 2


class FrontStyleLab(ttk.Frame):
    """Interactive laboratory for choosing print-robust front and rear styling."""

    def __init__(self, root: tk.Tk, *, dataset_root: Path) -> None:
        super().__init__(root, padding=10)
        self.root = root
        self.dataset_root = Path(dataset_root)
        self.datasets: dict[str, Dataset] = {}
        self.streets: dict[str, StreetRecord] = {}
        self.front_preview_photo: ImageTk.PhotoImage | None = None
        self.rear_preview_photo: ImageTk.PhotoImage | None = None
        self._front_refresh_after: str | None = None
        self._rear_refresh_after: str | None = None
        self._dataset_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        root.title("Mug Previewer — Print Style Calibration Lab")
        root.geometry("1320x860")
        root.minsize(1120, 740)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.dataset_var = tk.StringVar()
        self.street_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Loading datasets...")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

        self.front_tab = ttk.Frame(self.notebook, padding=8)
        self.rear_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.front_tab, text="Front face")
        self.notebook.add(self.rear_tab, text="Rear context")

        self._build_front_tab()
        self._build_rear_tab()
        self.root.after(50, self._start_dataset_load)

    # ------------------------------------------------------------------
    # Shared source selectors
    # ------------------------------------------------------------------
    def _build_source_controls(self, parent: ttk.Frame, *, rear: bool) -> tuple[ttk.Combobox, ttk.Combobox]:
        source = ttk.LabelFrame(parent, text="Artwork", padding=8)
        source.grid(row=0, column=0, sticky="ew")
        source.columnconfigure(0, weight=1)

        ttk.Label(source, text="Dataset").grid(row=0, column=0, sticky="w")
        dataset_box = ttk.Combobox(
            source,
            state="readonly",
            textvariable=self.dataset_var,
            width=38,
        )
        dataset_box.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        dataset_box.bind("<<ComboboxSelected>>", self._select_dataset)

        ttk.Label(source, text="Street").grid(row=2, column=0, sticky="w")
        street_box = ttk.Combobox(
            source,
            state="readonly",
            textvariable=self.street_var,
            width=38,
        )
        street_box.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        street_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._schedule_rear_preview() if rear else self._schedule_front_preview(),
        )
        return dataset_box, street_box

    # ------------------------------------------------------------------
    # Front tab
    # ------------------------------------------------------------------
    def _build_front_tab(self) -> None:
        self.front_tab.columnconfigure(1, weight=1)
        self.front_tab.rowconfigure(0, weight=1)

        controls = ttk.Frame(self.front_tab, padding=(0, 0, 12, 0))
        controls.grid(row=0, column=0, sticky="ns")
        controls.columnconfigure(0, weight=1)

        self.front_dataset_box, self.front_street_box = self._build_source_controls(controls, rear=False)

        style = ttk.LabelFrame(controls, text="Front print style", padding=8)
        style.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        style.columnconfigure(0, weight=1)

        self.title_scale = tk.DoubleVar(value=1.0)
        self.locality_scale = tk.DoubleVar(value=1.0)
        self.text_gap = tk.DoubleVar(value=FRONT_TITLE_LOCALITY_GAP_DELTA_PX)
        self.text_offset = tk.DoubleVar(value=FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX)
        self.facial_linework = tk.DoubleVar(value=1.0)
        self.support_stroke = tk.DoubleVar(value=1.0)
        self.street_stroke = tk.DoubleVar(value=STREET_STROKE_MULTIPLIER)
        self.vertical_spread = tk.DoubleVar(value=1.0)
        self.group_scale = tk.DoubleVar(value=FRONT_GROUP_SCALE)
        self.group_y = tk.DoubleVar(value=FRONT_GROUP_Y_OFFSET)

        sliders = (
            ("Street-name text ×", self.title_scale, 0.80, 1.45, 0.01),
            ("Area/locality text ×", self.locality_scale, 0.80, 1.60, 0.01),
            ("Title → area gap (px)", self.text_gap, -8.0, 36.0, 1.0),
            ("Text block vertical offset", self.text_offset, -45.0, 20.0, 1.0),
            ("All facial linework ×", self.facial_linework, 0.75, 3.00, 0.01),
            ("Ear/nose extra weight ×", self.support_stroke, 0.75, 2.75, 0.01),
            ("Street facial feature ×", self.street_stroke, 0.75, 4.00, 0.01),
            ("Global vertical row spread ×", self.vertical_spread, 0.85, 1.35, 0.01),
            ("Whole composition scale ×", self.group_scale, 0.90, 1.35, 0.01),
            ("Whole composition Y offset", self.group_y, 20.0, 95.0, 1.0),
        )
        for row, (label, variable, low, high, resolution) in enumerate(sliders):
            self._add_slider(
                style,
                row,
                label,
                variable,
                low,
                high,
                resolution,
                callback=self._schedule_front_preview,
            )

        actions = ttk.LabelFrame(controls, text="Calibration", padding=8)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="Reset production", command=self._reset_front).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="Load profile", command=self._load_profile).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(actions, text="Save profile", command=self._save_profile).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Export front PNG", command=self._export_front_png).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(actions, text="Export 12-variant matrix", command=self._export_matrix).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        ttk.Label(controls, textvariable=self.status_var, wraplength=330, justify="left").grid(
            row=3, column=0, sticky="ew", pady=(10, 0)
        )

        preview = ttk.LabelFrame(self.front_tab, text="Live front preview", padding=10)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.front_preview_label = ttk.Label(preview, anchor="center")
        self.front_preview_label.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            preview,
            text="Uses the production front renderer; calibration values do not alter production defaults.",
            wraplength=760,
            justify="center",
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

    # ------------------------------------------------------------------
    # Rear tab
    # ------------------------------------------------------------------
    def _build_rear_tab(self) -> None:
        self.rear_tab.columnconfigure(1, weight=1)
        self.rear_tab.rowconfigure(0, weight=1)

        controls = ttk.Frame(self.rear_tab, padding=(0, 0, 12, 0))
        controls.grid(row=0, column=0, sticky="ns")
        controls.columnconfigure(0, weight=1)

        self.rear_dataset_box, self.rear_street_box = self._build_source_controls(controls, rear=True)

        style = ttk.LabelFrame(controls, text="Rear attribution text", padding=8)
        style.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        style.columnconfigure(0, weight=1)

        self.rear_text_scale = tk.DoubleVar(value=1.0)
        self.rear_line_spacing_scale = tk.DoubleVar(value=1.0)

        self._add_slider(
            style,
            0,
            "Text size ×",
            self.rear_text_scale,
            0.75,
            2.00,
            0.01,
            callback=self._schedule_rear_preview,
        )
        self._add_slider(
            style,
            1,
            "Vertical line spacing ×",
            self.rear_line_spacing_scale,
            0.70,
            2.00,
            0.01,
            callback=self._schedule_rear_preview,
        )

        actions = ttk.LabelFrame(controls, text="Calibration", padding=8)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="Reset production", command=self._reset_rear).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="Load profile", command=self._load_profile).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(actions, text="Save profile", command=self._save_profile).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Export rear PNG", command=self._export_rear_png).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))

        ttk.Label(controls, textvariable=self.status_var, wraplength=330, justify="left").grid(
            row=3, column=0, sticky="ew", pady=(10, 0)
        )

        preview = ttk.LabelFrame(self.rear_tab, text="Live rear preview", padding=10)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.rear_preview_label = ttk.Label(preview, anchor="center")
        self.rear_preview_label.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            preview,
            text="Only the two attribution lines are calibrated here; map framing and highlight geometry are unchanged.",
            wraplength=760,
            justify="center",
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

    # ------------------------------------------------------------------
    # Slider helpers
    # ------------------------------------------------------------------
    def _add_slider(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.DoubleVar,
        low: float,
        high: float,
        resolution: float,
        *,
        callback,
    ) -> None:
        line = ttk.Frame(parent)
        line.grid(row=row, column=0, sticky="ew", pady=2)
        line.columnconfigure(0, weight=1)
        ttk.Label(line, text=label).grid(row=0, column=0, sticky="w")
        value_label = ttk.Label(line, width=7, anchor="e")
        value_label.grid(row=0, column=1, sticky="e")
        scale = tk.Scale(
            line,
            from_=low,
            to=high,
            resolution=resolution,
            orient="horizontal",
            variable=variable,
            showvalue=False,
            length=330,
            command=lambda _value, v=variable, out=value_label, cb=callback: self._slider_changed(v, out, cb),
        )
        scale.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._update_value_label(variable, value_label)

    def _slider_changed(self, variable: tk.DoubleVar, label: ttk.Label, callback) -> None:
        self._update_value_label(variable, label)
        callback()

    @staticmethod
    def _update_value_label(variable: tk.DoubleVar, label: ttk.Label) -> None:
        value = variable.get()
        if abs(value) >= 10:
            label.configure(text=f"{value:.1f}")
        else:
            label.configure(text=f"{value:.2f}")

    # ------------------------------------------------------------------
    # Dataset discovery and selection
    # ------------------------------------------------------------------
    def _start_dataset_load(self) -> None:
        def worker() -> None:
            try:
                found = discover_datasets(self.dataset_root)
            except Exception as error:
                self._dataset_queue.put(("error", error))
            else:
                self._dataset_queue.put(("ok", found))

        threading.Thread(target=worker, name="print-style-dataset-load", daemon=True).start()
        self.root.after(75, self._poll_dataset_load)

    def _poll_dataset_load(self) -> None:
        try:
            status, payload = self._dataset_queue.get_nowait()
        except queue.Empty:
            self.root.after(75, self._poll_dataset_load)
            return

        if status == "error":
            messagebox.showerror("Dataset error", str(payload), parent=self.root)
            self.status_var.set("Dataset discovery failed.")
            return

        found = payload
        self.datasets = {
            f"{item.dataset.display_name} [{item.dataset.id}]": item.dataset
            for item in found
        }
        values = list(self.datasets)
        self.front_dataset_box["values"] = values
        self.rear_dataset_box["values"] = values
        if self.datasets:
            self.dataset_var.set(values[0])
            self._select_dataset()
        else:
            self.status_var.set(f"No datasets found under {self.dataset_root}")

    def _select_dataset(self, _event: object | None = None) -> None:
        dataset = self.datasets.get(self.dataset_var.get())
        if dataset is None:
            return
        self.streets = {
            f"{street.id} — {street.display_name}": street
            for street in dataset.streets
        }
        values = list(self.streets)
        self.front_street_box["values"] = values
        self.rear_street_box["values"] = values
        if self.streets:
            self.street_var.set(values[0])
            self._schedule_active_preview()

    def _selected(self) -> tuple[Dataset, StreetRecord] | None:
        dataset = self.datasets.get(self.dataset_var.get())
        street = self.streets.get(self.street_var.get())
        if dataset is None or street is None:
            return None
        return dataset, street

    def _tab_changed(self, _event: object | None = None) -> None:
        self._schedule_active_preview()

    def _schedule_active_preview(self) -> None:
        if self.notebook.select() == str(self.rear_tab):
            self._schedule_rear_preview()
        else:
            self._schedule_front_preview()

    # ------------------------------------------------------------------
    # Renderer options
    # ------------------------------------------------------------------
    def _face_options(self) -> FaceRenderOptions:
        selected = self._selected()
        area = "" if selected is None else selected[0].display_name
        return FaceRenderOptions(
            area=area,
            group_scale=self.group_scale.get(),
            group_y_offset=self.group_y.get(),
            title_locality_gap_delta=self.text_gap.get(),
            typography_block_y_offset=self.text_offset.get(),
            title_font_scale=self.title_scale.get(),
            locality_font_scale=self.locality_scale.get(),
            facial_linework_multiplier=self.facial_linework.get(),
            supporting_stroke_multiplier=self.support_stroke.get(),
            street_feature_stroke_multiplier=self.street_stroke.get(),
            vertical_spread=self.vertical_spread.get(),
        )

    def _rear_options(self) -> ContextRenderOptions:
        return ContextRenderOptions(
            attribution_font_scale=self.rear_text_scale.get(),
            attribution_line_spacing_scale=self.rear_line_spacing_scale.get(),
        )

    # ------------------------------------------------------------------
    # Live previews
    # ------------------------------------------------------------------
    def _schedule_front_preview(self) -> None:
        if self._front_refresh_after is not None:
            self.root.after_cancel(self._front_refresh_after)
        self._front_refresh_after = self.root.after(140, self._render_front_preview)

    def _render_front_preview(self) -> None:
        self._front_refresh_after = None
        selected = self._selected()
        if selected is None:
            return
        dataset, street = selected
        self.status_var.set(f"Rendering front: {street.display_name}...")
        self.root.update_idletasks()
        try:
            image = _render_face_standard(street, self._face_options())
        except FaceRenderError as error:
            self.status_var.set(str(error))
            return
        except Exception as error:
            self.status_var.set(f"Front preview error: {error}")
            return

        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(image, mask=image.getchannel("A"))
        display = canvas.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        self.front_preview_photo = ImageTk.PhotoImage(display)
        self.front_preview_label.configure(image=self.front_preview_photo)
        self.status_var.set(f"{dataset.display_name} — {street.display_name}")

    def _schedule_rear_preview(self) -> None:
        if self._rear_refresh_after is not None:
            self.root.after_cancel(self._rear_refresh_after)
        self._rear_refresh_after = self.root.after(140, self._render_rear_preview)

    def _render_rear_preview(self) -> None:
        self._rear_refresh_after = None
        selected = self._selected()
        if selected is None:
            return
        dataset, street = selected
        self.status_var.set(f"Rendering rear: {street.display_name}...")
        self.root.update_idletasks()
        try:
            image = render_context_map_result(dataset, street, self._rear_options()).image
        except ContextRenderError as error:
            self.status_var.set(str(error))
            return
        except Exception as error:
            self.status_var.set(f"Rear preview error: {error}")
            return

        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(image, mask=image.getchannel("A"))
        display = canvas.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        self.rear_preview_photo = ImageTk.PhotoImage(display)
        self.rear_preview_label.configure(image=self.rear_preview_photo)
        self.status_var.set(f"{dataset.display_name} — {street.display_name}")

    # ------------------------------------------------------------------
    # Reset/profile actions
    # ------------------------------------------------------------------
    def _reset_front(self) -> None:
        self.title_scale.set(1.0)
        self.locality_scale.set(1.0)
        self.text_gap.set(FRONT_TITLE_LOCALITY_GAP_DELTA_PX)
        self.text_offset.set(FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX)
        self.facial_linework.set(1.0)
        self.support_stroke.set(1.0)
        self.street_stroke.set(STREET_STROKE_MULTIPLIER)
        self.vertical_spread.set(1.0)
        self.group_scale.set(FRONT_GROUP_SCALE)
        self.group_y.set(FRONT_GROUP_Y_OFFSET)
        self._schedule_front_preview()

    def _reset_rear(self) -> None:
        self.rear_text_scale.set(1.0)
        self.rear_line_spacing_scale.set(1.0)
        self._schedule_rear_preview()

    def _profile_payload(self) -> dict[str, object]:
        face_options = self._face_options()
        rear_options = self._rear_options()
        return {
            "profile_version": PROFILE_VERSION,
            "profile_kind": "mug_previewer_production_style",
            "purpose": "mug-print-calibration",
            "face_render_options": {
                key: value
                for key, value in asdict(face_options).items()
                if key not in {"area", "manual_override"}
            },
            "rear_render_options": {
                "attribution_font_scale": rear_options.attribution_font_scale,
                "attribution_line_spacing_scale": rear_options.attribution_line_spacing_scale,
            },
        }

    def _save_profile(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save production style profile",
            defaultextension=".json",
            filetypes=(("JSON profile", "*.json"),),
            initialfile="mug_production_style.json",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(self._profile_payload(), indent=2), encoding="utf-8")
        self.status_var.set(f"Saved calibration profile: {path}")

    def _load_profile(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load production style profile",
            filetypes=(("JSON profile", "*.json"),),
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            values = payload["face_render_options"]
            self.title_scale.set(float(values["title_font_scale"]))
            self.locality_scale.set(float(values["locality_font_scale"]))
            self.text_gap.set(float(values["title_locality_gap_delta"]))
            self.text_offset.set(float(values["typography_block_y_offset"]))
            self.facial_linework.set(float(values.get("facial_linework_multiplier", 1.0)))
            self.support_stroke.set(float(values["supporting_stroke_multiplier"]))
            self.street_stroke.set(float(values["street_feature_stroke_multiplier"]))
            self.vertical_spread.set(float(values["vertical_spread"]))
            self.group_scale.set(float(values["group_scale"]))
            self.group_y.set(float(values["group_y_offset"]))

            rear_values = payload.get("rear_render_options", {})
            self.rear_text_scale.set(float(rear_values.get("attribution_font_scale", 1.0)))
            self.rear_line_spacing_scale.set(float(rear_values.get("attribution_line_spacing_scale", 1.0)))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            messagebox.showerror("Profile error", f"Could not load profile:\n{error}", parent=self.root)
            return
        self._schedule_active_preview()
        self.status_var.set(f"Loaded calibration profile: {path}")

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------
    def _export_front_png(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        _dataset, street = selected
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export calibrated front PNG",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
            initialfile=f"{street.id}_{street.display_name}_front_calibration.png",
        )
        if not path:
            return
        try:
            _render_face_standard(street, self._face_options()).save(path)
        except Exception as error:
            messagebox.showerror("Export error", str(error), parent=self.root)
            return
        self.status_var.set(f"Exported: {path}")

    def _export_rear_png(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        dataset, street = selected
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export calibrated rear PNG",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
            initialfile=f"{street.id}_{street.display_name}_rear_calibration.png",
        )
        if not path:
            return
        try:
            render_context_map_result(dataset, street, self._rear_options()).image.save(path)
        except Exception as error:
            messagebox.showerror("Export error", str(error), parent=self.root)
            return
        self.status_var.set(f"Exported: {path}")

    def _export_matrix(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        _dataset, street = selected
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export calibration matrix",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
            initialfile=f"{street.id}_{street.display_name}_calibration_matrix.png",
        )
        if not path:
            return

        base = self._face_options()
        stroke_factors = (0.85, 1.00, 1.15, 1.30)
        text_factors = (0.90, 1.00, 1.10)
        cell_w, cell_h = 560, 560
        sheet = Image.new("RGB", (cell_w * len(stroke_factors), cell_h * len(text_factors)), "white")
        draw = ImageDraw.Draw(sheet)

        try:
            for row, text_factor in enumerate(text_factors):
                for col, stroke_factor in enumerate(stroke_factors):
                    options = replace(
                        base,
                        title_font_scale=base.title_font_scale * text_factor,
                        locality_font_scale=base.locality_font_scale * text_factor,
                        facial_linework_multiplier=base.facial_linework_multiplier * stroke_factor,
                        supporting_stroke_multiplier=base.supporting_stroke_multiplier,
                        street_feature_stroke_multiplier=base.street_feature_stroke_multiplier * stroke_factor,
                    )
                    face = _render_face_standard(street, options)
                    white = Image.new("RGB", face.size, "white")
                    white.paste(face, mask=face.getchannel("A"))
                    x = col * cell_w + (cell_w - face.width) // 2
                    y = row * cell_h + 48
                    sheet.paste(white, (x, y))
                    draw.text(
                        (col * cell_w + 12, row * cell_h + 12),
                        f"T{text_factor:.2f}  S{stroke_factor:.2f}",
                        fill="black",
                    )
        except Exception as error:
            messagebox.showerror("Matrix error", str(error), parent=self.root)
            return

        sheet.save(path)
        self.status_var.set(f"Exported 12-variant calibration matrix: {path}")


def launch(*, dataset_root: Path) -> int:
    root = tk.Tk()
    FrontStyleLab(root, dataset_root=dataset_root)
    root.mainloop()
    return 0
