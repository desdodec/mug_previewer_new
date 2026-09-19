"""Standalone desktop app for calibrating provider mug mockups."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Sequence
import argparse

from PIL import Image, ImageTk

from ..preview_v2 import (
    MugCalibrationProfile,
    PreviewV2View,
    get_calibration_profile,
    list_calibration_profiles,
)
from .session import (
    CalibrationFit,
    ImageBounds,
    ViewFit,
    candidate_profile_mapping,
    load_fit_session,
    render_target_overlay,
    save_candidate_profile,
    save_fit_session,
)
from .target import render_calibration_target, save_calibration_target


class MockupCanvas(ttk.Frame):
    """One mockup image with drag-to-select mug-body bounds."""

    def __init__(self, parent, *, title: str, on_bounds_changed) -> None:
        super().__init__(parent)
        self.on_bounds_changed = on_bounds_changed
        self.image: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.bounds: ImageBounds | None = None
        self._display_scale = 1.0
        self._display_offset = (0, 0)
        self._drag_start: tuple[int, int] | None = None
        self._selection_id = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text=title, font=("TkDefaultFont", 11, "bold")).grid(
            row=0, column=0, sticky="w", padx=4, pady=(2, 4)
        )
        self.canvas = tk.Canvas(self, background="#ececec", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._finish_drag)

    def set_image(self, image: Image.Image | None) -> None:
        self.image = image.convert("RGBA").copy() if image is not None else None
        self.redraw()

    def set_bounds(self, bounds: ImageBounds | None) -> None:
        self.bounds = bounds
        self.redraw()

    def redraw(self) -> None:
        self.canvas.delete("all")
        self.photo = None
        image = self.image
        if image is None:
            self.canvas.create_text(
                max(20, self.canvas.winfo_width() // 2),
                max(20, self.canvas.winfo_height() // 2),
                text="Load a provider mockup",
                fill="#666666",
            )
            return

        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        scale = min(cw / image.width, ch / image.height)
        dw = max(1, round(image.width * scale))
        dh = max(1, round(image.height * scale))
        display = image.resize((dw, dh), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(display)
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2
        self._display_scale = scale
        self._display_offset = (ox, oy)
        self.canvas.create_image(ox, oy, image=self.photo, anchor="nw")
        if self.bounds is not None:
            x1, y1 = self._image_to_canvas(self.bounds.x, self.bounds.y)
            x2, y2 = self._image_to_canvas(
                self.bounds.x + self.bounds.width,
                self.bounds.y + self.bounds.height,
            )
            self.canvas.create_rectangle(
                x1, y1, x2, y2,
                outline="#ff2d2d",
                width=3,
                dash=(8, 5),
            )

    def _start_drag(self, event) -> None:
        if self.image is None:
            return
        point = self._canvas_to_image(event.x, event.y, clamp=True)
        if point is None:
            return
        self._drag_start = point

    def _drag(self, event) -> None:
        if self.image is None or self._drag_start is None:
            return
        point = self._canvas_to_image(event.x, event.y, clamp=True)
        if point is None:
            return
        x1, y1 = self._image_to_canvas(*self._drag_start)
        x2, y2 = self._image_to_canvas(*point)
        if self._selection_id is not None:
            self.canvas.delete(self._selection_id)
        self._selection_id = self.canvas.create_rectangle(
            x1, y1, x2, y2, outline="#ff2d2d", width=3, dash=(8, 5)
        )

    def _finish_drag(self, event) -> None:
        if self.image is None or self._drag_start is None:
            return
        point = self._canvas_to_image(event.x, event.y, clamp=True)
        start = self._drag_start
        self._drag_start = None
        self._selection_id = None
        if point is None:
            self.redraw()
            return
        left = min(start[0], point[0])
        top = min(start[1], point[1])
        right = max(start[0], point[0])
        bottom = max(start[1], point[1])
        if right - left < 10 or bottom - top < 10:
            self.redraw()
            return
        self.bounds = ImageBounds(left, top, right - left, bottom - top)
        self.on_bounds_changed(self.bounds)
        self.redraw()

    def _canvas_to_image(self, x: int, y: int, *, clamp: bool) -> tuple[int, int] | None:
        image = self.image
        if image is None:
            return None
        ox, oy = self._display_offset
        scale = self._display_scale
        ix = round((x - ox) / scale)
        iy = round((y - oy) / scale)
        if clamp:
            ix = max(0, min(image.width, ix))
            iy = max(0, min(image.height, iy))
            return ix, iy
        if not (0 <= ix <= image.width and 0 <= iy <= image.height):
            return None
        return ix, iy

    def _image_to_canvas(self, x: int, y: int) -> tuple[int, int]:
        ox, oy = self._display_offset
        return (
            round(ox + x * self._display_scale),
            round(oy + y * self._display_scale),
        )


class MugCalibrationApp(ttk.Frame):
    """Separate app for fitting V2 geometry to provider-generated mockups."""

    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=10)
        self.root = root
        self.root.title("Mug Previewer Calibration Lab")
        self.root.minsize(1250, 760)
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.profiles = list_calibration_profiles()
        self.profile_by_label = {profile.display_label: profile for profile in self.profiles}
        default = get_calibration_profile("inkthreadable_11oz_white_v2")
        self.fit = CalibrationFit(profile_id=default.id)
        self.front_source: Image.Image | None = None
        self.rear_source: Image.Image | None = None
        self.target = render_calibration_target(default)
        self._build_controls(default)
        self._build_viewers()
        self._refresh_all()

    def _build_controls(self, default: MugCalibrationProfile) -> None:
        panel = ttk.Frame(self)
        panel.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        panel.columnconfigure(0, weight=1)

        ttk.Label(panel, text="Calibration Lab", font=("TkDefaultFont", 15, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )

        ttk.Label(panel, text="Provider / SKU calibration").grid(row=1, column=0, sticky="w")
        self.profile_var = tk.StringVar(value=default.display_label)
        self.profile_box = ttk.Combobox(
            panel,
            textvariable=self.profile_var,
            values=list(self.profile_by_label),
            state="readonly",
            width=42,
        )
        self.profile_box.grid(row=2, column=0, sticky="ew", pady=(2, 4))
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_changed)

        self.profile_detail_var = tk.StringVar()
        ttk.Label(
            panel,
            textvariable=self.profile_detail_var,
            wraplength=320,
            justify="left",
        ).grid(row=3, column=0, sticky="ew", pady=(0, 8))

        buttons = ttk.Frame(panel)
        buttons.grid(row=4, column=0, sticky="ew")
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="Save Target PNG", command=self._save_target).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(buttons, text="Load Front Mockup", command=lambda: self._load_mockup("front")).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(buttons, text="Load Rear Mockup", command=lambda: self._load_mockup("rear")).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )

        ttk.Separator(panel).grid(row=5, column=0, sticky="ew", pady=10)

        self.front_yaw = tk.DoubleVar(value=0.0)
        self.rear_yaw = tk.DoubleVar(value=0.0)
        self.artwork_offset = tk.DoubleVar(value=0.0)
        self.visible_arc = tk.DoubleVar(value=default.calibration.visible_angle_degrees)
        self.print_arc = tk.DoubleVar(value=default.calibration.wrap_span_degrees)
        self.front_scale = tk.DoubleVar(value=1.0)
        self.rear_scale = tk.DoubleVar(value=1.0)
        self.front_offset = tk.DoubleVar(value=0.0)
        self.rear_offset = tk.DoubleVar(value=0.0)
        self.opacity = tk.DoubleVar(value=0.55)

        row = 6
        row = self._add_slider(panel, row, "Front camera yaw", self.front_yaw, -45, 45, 0.5, "°")
        row = self._add_slider(panel, row, "Rear camera yaw", self.rear_yaw, -45, 45, 0.5, "°")
        row = self._add_slider(panel, row, "Shared artwork registration", self.artwork_offset, -20, 20, 0.25, "°")
        row = self._add_slider(panel, row, "Visible cylinder arc", self.visible_arc, 90, 175, 0.5, "°")
        row = self._add_slider(panel, row, "Print canvas arc", self.print_arc, 200, 350, 0.5, "°")
        row = self._add_slider(panel, row, "Front vertical scale", self.front_scale, 0.75, 1.20, 0.005, "×")
        row = self._add_slider(panel, row, "Rear vertical scale", self.rear_scale, 0.75, 1.20, 0.005, "×")
        row = self._add_slider(panel, row, "Front vertical offset", self.front_offset, -0.20, 0.20, 0.005, "")
        row = self._add_slider(panel, row, "Rear vertical offset", self.rear_offset, -0.20, 0.20, 0.005, "")
        row = self._add_slider(panel, row, "Overlay opacity", self.opacity, 0.10, 0.90, 0.05, "")

        ttk.Label(
            panel,
            text=(
                "Drag a rectangle around the cylindrical mug body in each mockup. "
                "Do not include the handle. Fit the coloured longitude lines and bullseye first; "
                "then use the shared artwork offset only if both views require the same angular shift."
            ),
            wraplength=320,
            justify="left",
        ).grid(row=row, column=0, sticky="ew", pady=(8, 10))
        row += 1

        io = ttk.Frame(panel)
        io.grid(row=row, column=0, sticky="ew")
        io.columnconfigure((0, 1), weight=1)
        ttk.Button(io, text="Save Session", command=self._save_session).grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(io, text="Load Session", command=self._load_session).grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(io, text="Export Candidate JSON", command=self._export_candidate).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )

        self.status_var = tk.StringVar(value="Generate the target, upload it to a provider, then load the returned mockups.")
        ttk.Label(
            panel,
            textvariable=self.status_var,
            wraplength=320,
            justify="left",
        ).grid(row=row + 1, column=0, sticky="ew", pady=(10, 0))

    def _build_viewers(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=1, sticky="nsew")
        self.front_view = MockupCanvas(
            notebook,
            title="Front mockup — drag around mug body",
            on_bounds_changed=lambda bounds: self._bounds_changed("front", bounds),
        )
        self.rear_view = MockupCanvas(
            notebook,
            title="Rear mockup — drag around mug body",
            on_bounds_changed=lambda bounds: self._bounds_changed("rear", bounds),
        )
        self.target_view = MockupCanvas(
            notebook,
            title="Calibration target artwork",
            on_bounds_changed=lambda _bounds: None,
        )
        notebook.add(self.front_view, text="Front Fit")
        notebook.add(self.rear_view, text="Rear Fit")
        notebook.add(self.target_view, text="Target")
        self.target_view.canvas.unbind("<ButtonPress-1>")
        self.target_view.canvas.unbind("<B1-Motion>")
        self.target_view.canvas.unbind("<ButtonRelease-1>")

    def _add_slider(
        self,
        parent,
        row: int,
        label: str,
        variable: tk.DoubleVar,
        minimum: float,
        maximum: float,
        resolution: float,
        suffix: str,
    ) -> int:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="ew", pady=2)
        frame.columnconfigure(0, weight=1)
        value_var = tk.StringVar()

        def update(_value=None) -> None:
            value = variable.get()
            decimals = 3 if resolution < 0.01 else 2 if resolution < 0.1 else 1
            value_var.set(f"{value:.{decimals}f}{suffix}")
            self._parameters_changed()

        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=value_var, width=10, anchor="e").grid(row=0, column=1, sticky="e")
        scale = tk.Scale(
            frame,
            from_=minimum,
            to=maximum,
            resolution=resolution,
            orient=tk.HORIZONTAL,
            variable=variable,
            command=update,
            showvalue=False,
            highlightthickness=0,
            length=300,
        )
        scale.grid(row=1, column=0, columnspan=2, sticky="ew")
        value_var.set(f"{variable.get():.2f}{suffix}")
        return row + 1

    def _selected_profile(self) -> MugCalibrationProfile:
        return self.profile_by_label[self.profile_var.get()]

    def _profile_changed(self, _event=None) -> None:
        profile = self._selected_profile()
        self.fit = CalibrationFit(
            profile_id=profile.id,
            front=self.fit.front,
            rear=self.fit.rear,
        )
        self.visible_arc.set(profile.calibration.visible_angle_degrees)
        self.print_arc.set(profile.calibration.wrap_span_degrees)
        self.artwork_offset.set(0.0)
        self.target = render_calibration_target(profile)
        self._refresh_all()
        self.status_var.set(
            f"Selected {profile.display_label}. Save a fresh target before calibrating this profile."
        )

    def _profile_detail(self, profile: MugCalibrationProfile) -> str:
        body = (
            f"{profile.body_height_mm:g} × {profile.body_diameter_mm:g} mm"
            if profile.body_height_mm is not None and profile.body_diameter_mm is not None
            else "body dimensions unavailable"
        )
        print_size = (
            f"{profile.print_width_mm:g} × {profile.print_height_mm:g} mm"
            if profile.print_width_mm is not None and profile.print_height_mm is not None
            else "print dimensions unavailable"
        )
        return (
            f"{profile.status.value.upper()} | body {body} | print {print_size} | "
            f"base print arc {profile.calibration.wrap_span_degrees:.1f}°"
        )

    def _load_mockup(self, which: str) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"Load {which} provider mockup",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            with Image.open(path) as image:
                loaded = image.convert("RGBA").copy()
        except OSError as error:
            messagebox.showerror("Calibration Lab", f"Could not load image: {error}", parent=self.root)
            return
        if which == "front":
            self.front_source = loaded
            self.fit = replace(self.fit, front=replace(self.fit.front, bounds=None))
        else:
            self.rear_source = loaded
            self.fit = replace(self.fit, rear=replace(self.fit.rear, bounds=None))
        self._refresh_all()
        self.status_var.set(f"Loaded {which} mockup: {Path(path).name}. Drag around the mug body.")

    def _bounds_changed(self, which: str, bounds: ImageBounds) -> None:
        if which == "front":
            self.fit = replace(self.fit, front=replace(self.fit.front, bounds=bounds))
        else:
            self.fit = replace(self.fit, rear=replace(self.fit.rear, bounds=bounds))
        self._refresh_all()

    def _parameters_changed(self) -> None:
        if not hasattr(self, "status_var"):
            return
        self.fit = replace(
            self.fit,
            artwork_offset_degrees=self.artwork_offset.get(),
            visible_angle_degrees=self.visible_arc.get(),
            print_arc_degrees=self.print_arc.get(),
            front=replace(
                self.fit.front,
                camera_yaw_degrees=self.front_yaw.get(),
                vertical_scale=self.front_scale.get(),
                vertical_offset_fraction=self.front_offset.get(),
            ),
            rear=replace(
                self.fit.rear,
                camera_yaw_degrees=self.rear_yaw.get(),
                vertical_scale=self.rear_scale.get(),
                vertical_offset_fraction=self.rear_offset.get(),
            ),
        )
        self._refresh_overlays()

    def _refresh_all(self) -> None:
        profile = self._selected_profile()
        self.profile_detail_var.set(self._profile_detail(profile))
        self.target_view.set_image(self.target)
        self.front_view.set_bounds(self.fit.front.bounds)
        self.rear_view.set_bounds(self.fit.rear.bounds)
        self._refresh_overlays()

    def _refresh_overlays(self) -> None:
        profile = self._selected_profile()
        try:
            front = (
                None
                if self.front_source is None
                else render_target_overlay(
                    self.front_source,
                    self.target,
                    profile=profile,
                    fit=self.fit,
                    view=PreviewV2View.FRONT,
                    opacity=self.opacity.get(),
                )
            )
            rear = (
                None
                if self.rear_source is None
                else render_target_overlay(
                    self.rear_source,
                    self.target,
                    profile=profile,
                    fit=self.fit,
                    view=PreviewV2View.REAR,
                    opacity=self.opacity.get(),
                )
            )
        except ValueError as error:
            self.status_var.set(str(error))
            return
        self.front_view.set_image(front)
        self.rear_view.set_image(rear)

    def _save_target(self) -> None:
        profile = self._selected_profile()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save calibration target",
            initialfile=f"{profile.id}_calibration_target.png",
            defaultextension=".png",
            filetypes=[("PNG files", "*.png")],
        )
        if not path:
            return
        save_calibration_target(path, profile)
        self.status_var.set(
            f"Saved target to {path}. Upload this exact PNG to the provider without cropping or resizing."
        )

    def _save_session(self) -> None:
        self._parameters_changed()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save calibration session",
            initialfile=f"{self._selected_profile().id}_fit_session.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if not path:
            return
        save_fit_session(path, self.fit)
        self.status_var.set(f"Saved calibration session: {path}")

    def _load_session(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load calibration session",
            filetypes=[("JSON files", "*.json")],
        )
        if not path:
            return
        try:
            fit = load_fit_session(path)
            profile = get_calibration_profile(fit.profile_id)
        except (OSError, ValueError) as error:
            messagebox.showerror("Calibration Lab", f"Could not load session: {error}", parent=self.root)
            return
        self.fit = fit
        self.profile_var.set(profile.display_label)
        self.target = render_calibration_target(profile)
        self.front_yaw.set(fit.front.camera_yaw_degrees)
        self.rear_yaw.set(fit.rear.camera_yaw_degrees)
        self.artwork_offset.set(fit.artwork_offset_degrees)
        self.visible_arc.set(fit.visible_angle_degrees or profile.calibration.visible_angle_degrees)
        self.print_arc.set(fit.print_arc_degrees or profile.calibration.wrap_span_degrees)
        self.front_scale.set(fit.front.vertical_scale)
        self.rear_scale.set(fit.rear.vertical_scale)
        self.front_offset.set(fit.front.vertical_offset_fraction)
        self.rear_offset.set(fit.rear.vertical_offset_fraction)
        self._refresh_all()
        self.status_var.set(
            f"Loaded fit session {Path(path).name}. Mockup images are intentionally not embedded; reload them if needed."
        )

    def _export_candidate(self) -> None:
        self._parameters_changed()
        if self.fit.front.bounds is None or self.fit.rear.bounds is None:
            messagebox.showerror(
                "Calibration Lab",
                "Set mug-body bounds for both front and rear mockups before exporting a candidate.",
                parent=self.root,
            )
            return
        profile = self._selected_profile()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export candidate calibration JSON",
            initialfile=f"{profile.id}_fitted_candidate.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
        )
        if not path:
            return
        candidate_id = f"{profile.id}_fitted"
        mapping = candidate_profile_mapping(
            profile,
            self.fit,
            calibration_id=candidate_id,
            source_description=(
                "Candidate geometry fitted in Mug Previewer Calibration Lab against "
                "provider-generated front and rear mockups. Review before promoting to a built-in profile."
            ),
        )
        save_candidate_profile(path, mapping)
        self.status_var.set(
            f"Exported provisional candidate: {path}. This does not modify the built-in calibration registry."
        )


def launch() -> int:
    root = tk.Tk()
    MugCalibrationApp(root)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mug-calibrator",
        description="Fit Mug Previewer V2 geometry to provider-generated mockups.",
    )
    parser.parse_args(argv)
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
