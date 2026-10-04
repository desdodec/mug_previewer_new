"""Extended calibration lab with provider-visible rear artwork sizing."""
from __future__ import annotations

import json
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from ..rendering.context_map import (
    ContextRenderError,
    REAR_STREET_HIGHLIGHT_SCALE,
    render_context_map_result,
)
from ..rendering.face import (
    FRONT_GROUP_SCALE,
    FRONT_GROUP_Y_OFFSET,
    FRONT_TITLE_LOCALITY_GAP_DELTA_PX,
    FRONT_TYPOGRAPHY_BLOCK_Y_OFFSET_PX,
    STREET_STROKE_MULTIPLIER,
)
from .front_style_lab import FrontStyleLab


PROFILE_VERSION = 4
PREVIEW_MAX_REAR_ARTWORK_SCALE = 1.30


class EnhancedFrontStyleLab(FrontStyleLab):
    """Add a print-scale control while preserving the existing lab behavior."""

    def __init__(self, root: tk.Tk, *, dataset_root: Path) -> None:
        self.rear_artwork_scale = tk.DoubleVar(master=root, value=1.0)
        super().__init__(root, dataset_root=dataset_root)

    def _build_rear_tab(self) -> None:
        super()._build_rear_tab()
        style = None
        for child in self.rear_tab.winfo_children():
            for candidate in child.winfo_children():
                if isinstance(candidate, ttk.LabelFrame) and candidate.cget("text") == "Rear attribution text":
                    style = candidate
                    break
            if style is not None:
                break
        if style is None:
            return
        style.configure(text="Rear print style")
        self._add_slider(
            style,
            6,
            "Rear artwork size on mug ×",
            self.rear_artwork_scale,
            0.90,
            1.30,
            0.01,
            callback=self._schedule_rear_preview,
        )

    def _rear_options(self):
        return replace(
            super()._rear_options(),
            artwork_scale=self.rear_artwork_scale.get(),
        )

    def _render_rear_preview(self) -> None:
        """Show the provider-visible rear scale without lowering render quality."""
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

        white = Image.new("RGB", image.size, "white")
        white.paste(image, mask=image.getchannel("A"))
        scale = self.rear_artwork_scale.get()
        display_size = (
            max(1, round(image.width * 2 * scale)),
            max(1, round(image.height * 2 * scale)),
        )
        display = white.resize(display_size, Image.Resampling.LANCZOS)
        canvas_size = (
            round(image.width * 2 * PREVIEW_MAX_REAR_ARTWORK_SCALE),
            round(image.height * 2 * PREVIEW_MAX_REAR_ARTWORK_SCALE),
        )
        canvas = Image.new("RGB", canvas_size, "white")
        origin = (
            (canvas.width - display.width) // 2,
            (canvas.height - display.height) // 2,
        )
        canvas.paste(display, origin)
        self.rear_preview_photo = ImageTk.PhotoImage(canvas)
        self.rear_preview_label.configure(image=self.rear_preview_photo)
        self.status_var.set(
            f"{dataset.display_name} — {street.display_name} — rear artwork {scale:.2f}×"
        )

    def _reset_rear(self) -> None:
        self.rear_artwork_scale.set(1.0)
        super()._reset_rear()

    def _profile_payload(self) -> dict[str, object]:
        payload = super()._profile_payload()
        payload["profile_version"] = PROFILE_VERSION
        rear = payload.get("rear_render_options")
        if isinstance(rear, dict):
            rear["artwork_scale"] = self.rear_artwork_scale.get()
        return payload

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
            highlight_scale = float(
                rear_values.get("highlight_stroke_scale", REAR_STREET_HIGHLIGHT_SCALE)
            )
            self.rear_highlight_weight.set(highlight_scale / REAR_STREET_HIGHLIGHT_SCALE)
            self.rear_artwork_scale.set(float(rear_values.get("artwork_scale", 1.0)))
            legacy_scale = float(rear_values.get("attribution_font_scale", 1.0))
            self.rear_line1_text_scale.set(
                float(rear_values.get("attribution_line1_font_scale", legacy_scale))
            )
            self.rear_line2_text_scale.set(
                float(rear_values.get("attribution_line2_font_scale", legacy_scale))
            )
            self.rear_line_spacing_scale.set(
                float(rear_values.get("attribution_line_spacing_scale", 1.0))
            )
            self.rear_line1_y_offset.set(
                float(rear_values.get("attribution_line1_y_offset", 0.0))
            )
            self.rear_line2_y_offset.set(
                float(rear_values.get("attribution_line2_y_offset", 0.0))
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            messagebox.showerror(
                "Profile error",
                f"Could not load profile:\n{error}",
                parent=self.root,
            )
            return
        self._schedule_active_preview()
        self.status_var.set(f"Loaded calibration profile: {path}")


def launch(*, dataset_root: Path) -> int:
    root = tk.Tk()
    EnhancedFrontStyleLab(root, dataset_root=dataset_root)
    root.mainloop()
    return 0
