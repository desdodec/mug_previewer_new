"""Focused Tk artwork panel; domain modules own files, QA and editor processes."""
from pathlib import Path
import hashlib
import tkinter as tk
from tkinter import filedialog, ttk

from ..manual_svg_workspace import ManualSvgWorkspace, find_inkscape_executable, launch_inkscape, open_artwork_folder
from ..review_index import REVIEW_STATUSES, current_review_state, save_review_record, svg_sha256
from ..preprocess import resolve_authoritative_face_svg, validate_manual_svg
from ..rendering.svg_raster import rasterize_face_svg
from .state import load_preprocessed_catalogue


class ArtworkPanelMixin:
    def _build_artwork_panel(self):
        panel = ttk.Frame(self.workflow_card)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        self.artwork_var = tk.StringVar(value="Select a street")
        ttk.Label(panel, textvariable=self.artwork_var, justify="left", wraplength=320).grid(row=0, column=0, columnspan=2, sticky="w", pady=8)
        actions = (
            ("edit", "Edit in Inkscape", self._edit_artwork),
            ("working", "Preview Edit", lambda: self._preview_artwork("working")),
            ("repair", "Repair Edit", self._repair_artwork),
            ("corrected", "Preview Corrected", lambda: self._preview_artwork("corrected")),
            ("approve", "Approve Corrected", self._approve_artwork),
            ("approved", "Preview Current SVG for QA", lambda: self._preview_artwork("authoritative")),
            ("folder", "Open Artwork Folder", self._open_artwork_folder),
            ("refresh", "Refresh Artwork", self._refresh_selected_artwork),
        )
        self.artwork_buttons = {}
        for i, (key, label, command) in enumerate(actions):
            button = ttk.Button(panel, text=label, command=command, state="disabled")
            button.grid(row=1 + i // 2, column=i % 2, sticky="ew", padx=2, pady=3)
            self.artwork_buttons[key] = button
        self.inkscape_button = ttk.Button(panel, text="Choose Inkscape executable", command=self._choose_inkscape, state="disabled")
        self.inkscape_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=3)
        self.next_manual_button = ttk.Button(panel, text="Next Manual Review", command=self._next_manual_review, state="disabled")
        self.next_manual_button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        self.qa_display_var = tk.StringVar(value="Review: not reviewed")
        ttk.Label(panel, textvariable=self.qa_display_var, wraplength=320).grid(row=7, column=0, columnspan=2, sticky="w", pady=8)
        self.qa_status_var = tk.StringVar(value="pass")
        self.qa_note_var = tk.StringVar()
        ttk.Label(panel, text="Status").grid(row=8, column=0, sticky="w")
        ttk.Combobox(panel, textvariable=self.qa_status_var, values=REVIEW_STATUSES, state="readonly", width=16).grid(row=8, column=1, sticky="ew")
        ttk.Label(panel, text="Note").grid(row=9, column=0, sticky="w")
        ttk.Entry(panel, textvariable=self.qa_note_var).grid(row=9, column=1, sticky="ew")
        self.review_target_var = tk.StringVar(value="Review target: selected artwork")
        ttk.Label(panel, textvariable=self.review_target_var, wraplength=320).grid(row=10, column=0, columnspan=2, sticky="w", pady=8)
        self.save_review_button = ttk.Button(panel, text="Save Review", command=self._save_artwork_review, state="disabled")
        self.save_review_button.grid(row=11, column=0, columnspan=2, sticky="ew")

    def _selected_artwork_record(self):
        data, street = self.state.selected_dataset, self.state.selected_street
        if data is None or street is None or self.preprocessed_catalogue is None:
            return None
        return self.preprocessed_catalogue.find(data, street)

    def _workspace(self):
        return ManualSvgWorkspace.from_record(self._selected_artwork_record(), self.preprocessed_catalogue.records.values())

    def _formal_review_svg(self):
        record = self._selected_artwork_record()
        if record is None:
            return None
        if record.state and record.state.value == "MANUAL_APPROVED":
            return record.approved_svg_path
        return record.svg_path or record.generated_svg_path

    def _qa_export_error(self):
        record = self._selected_artwork_record()
        if record is None:
            return "Select prepared artwork before exporting."
        try:
            resolution = resolve_authoritative_face_svg(self.preprocessed_catalogue.root, self.state.selected_dataset, self.state.selected_street)
            if not resolution.production_approved:
                return 'Production approval required'
            if resolution.path is None:
                return 'Authoritative SVG missing'
            validate_manual_svg(resolution.path.read_bytes())
            review = current_review_state(self.preprocessed_catalogue.root, record.dataset_id,
                                          record.street_id, resolution.path)
            return review.label if review.export_blocked else None
        except (OSError, ValueError) as error:
            return f"QA ledger unavailable: {error}"

    def _refresh_artwork(self, *, reset_review=False):
        if "artwork_var" not in self.__dict__:
            return
        record = self._selected_artwork_record()
        workspace = self._workspace()
        labels = [("Generated SVG", workspace.generated_svg), ("Working edit", workspace.working_svg),
                  ("Corrected edit", workspace.corrected_svg), ("Approved SVG", workspace.approved_svg)]
        state = record.state.value if record and record.state else "not prepared"
        authority = "Approved SVG" if state == "MANUAL_APPROVED" else "Generated SVG"
        formal = self._formal_review_svg()
        if not formal or not formal.is_file():
            authority = "unavailable"
        text = f"Production: {state}\nAuthoritative artwork: {authority}\n\n" + "\n".join(f"{label}: {'present' if path and path.is_file() else 'missing'}" for label, path in labels)
        current = workspace.correction_current
        if workspace.corrected_svg and workspace.corrected_svg.exists() and not current:
            text += "\nCorrection needs repair or is invalid."
        if state == "MANUAL_REVIEW":
            next_action = "Approve Corrected" if current else ("Repair Edit" if workspace.has_working else "Edit in Inkscape")
        elif state == "UNRENDERABLE_INPUT" or authority == "unavailable":
            next_action = "Check source artwork in its folder"
        else:
            next_action = "Preview Current SVG for QA, or export if ready"
        self.artwork_var.set(text + f"\n\nNext: {next_action}")
        manual = workspace.can_edit
        if "inkscape_button" in self.__dict__:
            self.inkscape_button.configure(state="normal" if manual else "disabled")
            streets = self.state.filtered_streets
            selected = self.state.selected_street
            start = streets.index(selected) + 1 if selected in streets else 0
            pending = any((candidate := self.preprocessed_catalogue.find(self.state.selected_dataset, street))
                          and candidate.state and candidate.state.value == "MANUAL_REVIEW"
                          for street in streets[start:]) if self.state.selected_dataset else False
            self.next_manual_button.configure(state="normal" if pending else "disabled")
        enabled = {"edit": manual, "working": manual and workspace.has_working,
                   "repair": manual and workspace.has_working, "corrected": manual and current,
                   "approve": manual and current, "approved": bool(self._formal_review_svg() and self._formal_review_svg().is_file()),
                   "folder": bool(workspace.generated_svg and workspace.generated_svg.parent.is_dir()),
                   "refresh": record is not None}
        for key, button in self.artwork_buttons.items():
            button.configure(state="normal" if enabled[key] else "disabled")
        self.artwork_buttons["edit"].configure(text="Edit Again in Inkscape" if state == "MANUAL_APPROVED" else "Edit in Inkscape")
        try:
            review = current_review_state(self.preprocessed_catalogue.root, record.dataset_id, record.street_id,
                                          self._formal_review_svg()) if record else None
            self.qa_display_var.set(review.label.replace("production export allowed", "current exact-SVG pass") if review else "QA: select a street")
            if reset_review:
                self.qa_status_var.set(review.record.status if review and review.record else "pass")
                self.qa_note_var.set(review.record.note if review and review.record else "")
        except (OSError, ValueError) as error:
            self.qa_display_var.set(f"QA ledger unavailable: {error}")
        target = self.__dict__.get("_review_target")
        self.save_review_button.configure(state="normal" if target and target.is_file() else "disabled")
        self.review_target_var.set(f"Save Review applies to: {self._preview_role}" if target else "Preview Current SVG for QA before saving a review.")

    def _reset_artwork_preview(self, record=None):
        self._review_target = None
        formal = self._formal_review_svg()
        try:
            self._preview_hash = svg_sha256(formal) if formal and formal.is_file() else None
        except OSError:
            self._preview_hash = None
        self._preview_role = "Approved Preview" if record and record.state and record.state.value == "MANUAL_APPROVED" else "Generated Preview"
        if "front_card" in self.__dict__:
            self.front_card.configure(text=self._preview_role)
        self._refresh_artwork(reset_review=True)

    def _choose_inkscape(self):
        selected = filedialog.askopenfilename(parent=self.root, title="Choose Inkscape executable",
                                             filetypes=[("Inkscape executable", "inkscape.exe"), ("All files", "*")])
        if selected:
            executable = find_inkscape_executable(Path(selected))
            if executable is None:
                self._show_error("The selected Inkscape executable does not exist.")
                return None
            self._inkscape_executable = executable
            return executable
        return None

    def _edit_artwork(self):
        try:
            workspace = self._workspace()
            workspace.create_or_get_working_edit()
            executable = find_inkscape_executable(self.__dict__.get("_inkscape_executable"))
            if executable is None:
                self.status_var.set("Inkscape was not found. Choose the Inkscape executable to continue.")
                executable = self._choose_inkscape()
                if executable is None:
                    return
            launch_inkscape(workspace, executable)
            self.status_var.set("Editing working copy in Inkscape. Save there, then Preview Edit or Repair Edit here.")
        except Exception as error:
            self._show_error(f"Could not open working edit: {error}")
        finally:
            self._refresh_artwork()

    def _preview_artwork(self, role):
        try:
            workspace = self._workspace()
            if role in {"working", "corrected"} and not workspace.can_edit:
                raise ValueError("Manual previews are unavailable for this street.")
            if role == "corrected" and not workspace.correction_current:
                raise ValueError("Repair the latest working edit before previewing the correction.")
            path = {"working": workspace.working_svg, "corrected": workspace.corrected_svg,
                    "approved": workspace.approved_svg, "authoritative": self._formal_review_svg()}[role]
            if path is None:
                raise ValueError("Requested artwork is unavailable.")
            self._invalidate_active_render_request()
            self._mug_front_image = None
            self.state.current_rear_preview = None
            payload = path.read_bytes()
            image = rasterize_face_svg(payload)
            self._preview_hash = hashlib.sha256(payload).hexdigest()
            self._review_target = path
            self._preview_role = {"working": "Working Edit Preview", "corrected": "Corrected SVG Preview", "approved": "Approved Preview", "authoritative": "Current SVG QA Preview"}[role]
            self.state.current_front_preview = image
            self.state.current_wrap = None
            self.front_card.configure(text=self._preview_role)
            self.status_var.set(self._preview_role)
            self._refresh_preview_images()
        except Exception as error:
            self._show_error(f"Could not preview artwork: {error}")
        finally:
            self._refresh_artwork()

    def _repair_artwork(self):
        try:
            self._workspace().repair()
            self._preview_artwork("corrected")
        except Exception as error:
            self._show_error(f"Could not repair edit: {error}")
        finally:
            self._refresh_artwork()

    def _approve_artwork(self):
        try:
            self._workspace().approve(self.state.selected_dataset, self.state.selected_street,
                                      self.preprocessed_catalogue.root)
        except Exception as error:
            self._refresh_selected_artwork()
            self._show_error(f"Could not approve correction: {error}")
            return
        self._refresh_selected_artwork()
        self.status_var.set("Corrected SVG approved. Production now uses this exact artwork.")

    def _refresh_selected_artwork(self):
        try:
            self._invalidate_active_render_request()
            self.preprocessed_catalogue = load_preprocessed_catalogue(self.preprocessed_catalogue.root)
            self._reload_workflow()
            if "batch_panel" in self.__dict__:
                self.batch_panel.invalidate()
            if self.state.selected_dataset and self.state.selected_street:
                self._show_preprocessed_selection(self.state.selected_dataset, self.state.selected_street)
        except Exception as error:
            self._set_export_buttons_state("disabled")
            self._show_error(f"Could not refresh artwork: {error}")

    def _open_artwork_folder(self):
        try:
            open_artwork_folder(self._workspace())
        except Exception as error:
            self._show_error(str(error))

    def _save_artwork_review(self):
        try:
            record = self._selected_artwork_record()
            target = self.__dict__.get("_review_target")
            if record is None or target is None:
                raise ValueError("Preview Current SVG for QA before saving a review; cached PNGs cannot establish exact SVG approval.")
            preview_hash = self.__dict__.get("_preview_hash")
            if preview_hash and preview_hash != svg_sha256(target):
                raise ValueError("Artwork changed since preview. Preview it again before saving the review.")
            save_review_record(self.preprocessed_catalogue.root, record.dataset_id, record.street_id,
                               self.qa_status_var.get(), target, self.qa_note_var.get(), expected_sha256=preview_hash)
            self._refresh_artwork()
            self._set_export_buttons_state("normal")
            self._reload_workflow()
            if "batch_panel" in self.__dict__:
                self.batch_panel.invalidate()
            self.status_var.set(f"Review saved against {self._preview_role}.")
        except Exception as error:
            self._show_error(f"Could not save review: {error}")

    def _next_manual_review(self):
        try:
            self.preprocessed_catalogue = load_preprocessed_catalogue(self.preprocessed_catalogue.root)
            streets = self.state.filtered_streets
            current = self.state.selected_street
            start = streets.index(current) + 1 if current in streets else 0
            for index in range(start, len(streets)):
                record = self.preprocessed_catalogue.find(self.state.selected_dataset, streets[index])
                if record and record.state and record.state.value == "MANUAL_REVIEW":
                    self.street_list.selection_clear(0, tk.END)
                    self.street_list.selection_set(index)
                    self.street_list.see(index)
                    self._select_street()
                    return
            self.status_var.set("No manual-review streets remaining after the current street.")
        except Exception as error:
            self._show_error(f"Could not find next manual review: {error}")
