"""Standalone front-artwork print calibration lab.

This deliberately exposes more controls than the production UI.  Its job is to
help choose physically robust typography, line weights and vertical spacing on
real printed mugs, then save the chosen values as a small JSON profile.
"""
from __future__ import annotations

import json
import tkinter as tk
from dataclasses import asdict, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from ..datasets.discovery import discover_datasets
from ..datasets.models import Dataset, StreetRecord
from ..rendering.face import (
    FRONT_GROUP_SCALE,
    FRONT_GROUP_Y_OFFSET,
    FRONT_TITLE_LOCALITY_GAP_DELTA_PX,
    FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX,
    STREET_STROKE_MULTIPLIER,
    FaceRenderError,
    FaceRenderOptions,
    render_face,
)


PROFILE_VERSION = 1


class FrontStyleLab(ttk.Frame):
    """Interactive laboratory for choosing print-robust front artwork styling."""

    def __init__(self, root: tk.Tk, *, dataset_root: Path) -> None:
        super().__init__(root, padding=10)
        self.root = root
        self.dataset_root = Path(dataset_root)
        self.datasets: dict[str, Dataset] = {}
        self.streets: dict[str, StreetRecord] = {}
        self.preview_photo: ImageTk.PhotoImage | None = None
        self._refresh_after: str | None = None

        root.title("Mug Previewer — Front Style Calibration Lab")
        root.geometry("1320x840")
        root.minsize(1120, 720)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.grid(sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_controls()
        self._build_preview()
        self._load_datasets()

    def _build_controls(self) -> None:
        controls = ttk.Frame(self, padding=(0, 0, 12, 0))
        controls.grid(row=0, column=0, sticky="ns")
        controls.columnconfigure(0, weight=1)

        source = ttk.LabelFrame(controls, text="Artwork", padding=8)
        source.grid(row=0, column=0, sticky="ew")
        source.columnconfigure(0, weight=1)

        ttk.Label(source, text="Dataset").grid(row=0, column=0, sticky="w")
        self.dataset_var = tk.StringVar()
        self.dataset_box = ttk.Combobox(source, state="readonly", textvariable=self.dataset_var, width=38)
        self.dataset_box.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.dataset_box.bind("<<ComboboxSelected>>", self._select_dataset)

        ttk.Label(source, text="Street").grid(row=2, column=0, sticky="w")
        self.street_var = tk.StringVar()
        self.street_box = ttk.Combobox(source, state="readonly", textvariable=self.street_var, width=38)
        self.street_box.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self.street_box.bind("<<ComboboxSelected>>", lambda _event: self._schedule_preview())

        style = ttk.LabelFrame(controls, text="Print style", padding=8)
        style.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        style.columnconfigure(0, weight=1)

        self.title_scale = tk.DoubleVar(value=1.0)
        self.locality_scale = tk.DoubleVar(value=1.0)
        self.text_gap = tk.DoubleVar(value=FRONT_TITLE_LOCALITY_GAP_DELTA_PX)
        self.text_offset = tk.DoubleVar(value=FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX)
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
            ("Facial support strokes ×", self.support_stroke, 0.75, 2.75, 0.01),
            ("Street facial feature ×", self.street_stroke, 0.75, 4.00, 0.01),
            ("Global vertical row spread ×", self.vertical_spread, 0.85, 1.35, 0.01),
            ("Whole composition scale ×", self.group_scale, 0.90, 1.35, 0.01),
            ("Whole composition Y offset", self.group_y, 20.0, 95.0, 1.0),
        )
        for row, (label, variable, low, high, resolution) in enumerate(sliders):
            self._add_slider(style, row, label, variable, low, high, resolution)

        actions = ttk.LabelFrame(controls, text="Calibration", padding=8)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(actions, text="Reset production", command=self._reset).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="Load profile", command=self._load_profile).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(actions, text="Save profile", command=self._save_profile).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(actions, text="Export front PNG", command=self._export_png).grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(6, 0))
        ttk.Button(actions, text="Export 12-variant matrix", command=self._export_matrix).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        self.status_var = tk.StringVar(value="Choose a dataset and street.")
        ttk.Label(controls, textvariable=self.status_var, wraplength=330, justify="left").grid(
            row=3, column=0, sticky="ew", pady=(10, 0)
        )

    def _add_slider(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.DoubleVar,
        low: float,
        high: float,
        resolution: float,
    ) -> None:
        line = ttk.Frame(parent)
        line.grid(row=row, column=0, sticky="ew", pady=2)
        line.columnconfigure(0, weight=1)
        ttk.Label(line, text=label).grid(row=0, column=0, sticky="w")
        value = ttk.Label(line, width=7, anchor="e")
        value.grid(row=0, column=1, sticky="e")
        scale = tk.Scale(
            line,
            from_=low,
            to=high,
            resolution=resolution,
            orient="horizontal",
            variable=variable,
            showvalue=False,
            length=330,
            command=lambda _value, v=variable, out=value: self._slider_changed(v, out),
        )
        scale.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._update_value_label(variable, value)

    def _slider_changed(self, variable: tk.DoubleVar, label: ttk.Label) -> None:
        self._update_value_label(variable, label)
        self._schedule_preview()

    @staticmethod
    def _update_value_label(variable: tk.DoubleVar, label: ttk.Label) -> None:
        value = variable.get()
        if abs(value) >= 10:
            label.configure(text=f"{value:.1f}")
        else:
            label.configure(text=f"{value:.2f}")

    def _build_preview(self) -> None:
        preview = ttk.LabelFrame(self, text="Live production-renderer preview", padding=10)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)

        self.preview_label = ttk.Label(preview, anchor="center")
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            preview,
            text=(
                "This uses the same face renderer as production. The lab changes options only; "
                "production defaults remain unchanged until you deliberately adopt a saved profile."
            ),
            wraplength=760,
            justify="center",
        ).grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def _load_datasets(self) -> None:
        try:
            found = discover_datasets(self.dataset_root)
        except Exception as error:
            messagebox.showerror("Dataset error", str(error), parent=self.root)
            return
        self.datasets = {
            f"{item.dataset.display_name} [{item.dataset.id}]": item.dataset
            for item in found
        }
        self.dataset_box["values"] = list(self.datasets)
        if self.datasets:
            first = next(iter(self.datasets))
            self.dataset_var.set(first)
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
        self.street_box["values"] = list(self.streets)
        if self.streets:
            first = next(iter(self.streets))
            self.street_var.set(first)
            self._schedule_preview()

    def _selected(self) -> tuple[Dataset, StreetRecord] | None:
        dataset = self.datasets.get(self.dataset_var.get())
        street = self.streets.get(self.street_var.get())
        if dataset is None or street is None:
            return None
        return dataset, street

    def _options(self) -> FaceRenderOptions:
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
            supporting_stroke_multiplier=self.support_stroke.get(),
            street_feature_stroke_multiplier=self.street_stroke.get(),
            vertical_spread=self.vertical_spread.get(),
        )

    def _schedule_preview(self) -> None:
        if self._refresh_after is not None:
            self.root.after_cancel(self._refresh_after)
        self._refresh_after = self.root.after(120, self._render_preview)

    def _render_preview(self) -> None:
        self._refresh_after = None
        selected = self._selected()
        if selected is None:
            return
        dataset, street = selected
        try:
            image = render_face(street, self._options())
        except FaceRenderError as error:
            self.status_var.set(str(error))
            return
        except Exception as error:
            self.status_var.set(f"Preview error: {error}")
            return

        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(image, mask=image.getchannel("A"))
        display = canvas.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(display)
        self.preview_label.configure(image=self.preview_photo)
        self.status_var.set(f"{dataset.display_name} — {street.display_name}")

    def _reset(self) -> None:
        self.title_scale.set(1.0)
        self.locality_scale.set(1.0)
        self.text_gap.set(FRONT_TITLE_LOCALITY_GAP_DELTA_PX)
        self.text_offset.set(FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX)
        self.support_stroke.set(1.0)
        self.street_stroke.set(STREET_STROKE_MULTIPLIER)
        self.vertical_spread.set(1.0)
        self.group_scale.set(FRONT_GROUP_SCALE)
        self.group_y.set(FRONT_GROUP_Y_OFFSET)
        self._schedule_preview()

    def _profile_payload(self) -> dict[str, object]:
        options = self._options()
        return {
            "profile_version": PROFILE_VERSION,
            "purpose": "front-print-calibration",
            "face_render_options": {
                key: value
                for key, value in asdict(options).items()
                if key not in {"area", "manual_override"}
            },
        }

    def _save_profile(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save front style calibration profile",
            defaultextension=".json",
            filetypes=(("JSON profile", "*.json"),),
            initialfile="front_print_style.json",
        )
        if not path:
            return
        Path(path).write_text(json.dumps(self._profile_payload(), indent=2), encoding="utf-8")
        self.status_var.set(f"Saved calibration profile: {path}")

    def _load_profile(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load front style calibration profile",
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
            self.support_stroke.set(float(values["supporting_stroke_multiplier"]))
            self.street_stroke.set(float(values["street_feature_stroke_multiplier"]))
            self.vertical_spread.set(float(values["vertical_spread"]))
            self.group_scale.set(float(values["group_scale"]))
            self.group_y.set(float(values["group_y_offset"]))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            messagebox.showerror("Profile error", f"Could not load profile:\n{error}", parent=self.root)
            return
        self._schedule_preview()
        self.status_var.set(f"Loaded calibration profile: {path}")

    def _export_png(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        dataset, street = selected
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
            render_face(street, self._options()).save(path)
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

        base = self._options()
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
                        supporting_stroke_multiplier=base.supporting_stroke_multiplier * stroke_factor,
                        street_feature_stroke_multiplier=base.street_feature_stroke_multiplier * stroke_factor,
                    )
                    face = render_face(street, options)
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
