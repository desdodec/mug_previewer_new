"""Focused Tk artwork panel; domain modules own files, QA and editor processes."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from ..manual_svg_workspace import ManualSvgWorkspace, find_inkscape_executable, launch_inkscape
from ..review_index import current_review_state, svg_sha256
from ..preprocess import resolve_authoritative_face_svg, validate_manual_svg
from .state import load_preprocessed_catalogue


class ArtworkPanelMixin:
    def _build_artwork_panel(self):
        panel = ttk.Frame(self.workflow_card)
        panel.grid(row=0, column=0, sticky="new")
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        self.artwork_var = tk.StringVar(value="Select a street")
        ttk.Label(panel, textvariable=self.artwork_var, justify="left", wraplength=320).grid(row=0, column=0, columnspan=2, sticky="w", pady=8)
        self.artwork_buttons = {"edit": ttk.Button(panel, text="Edit in Inkscape", command=self._edit_artwork)}
        self.artwork_buttons['edit'].grid(row=1, column=0, sticky='ew')
        self.exclude_var = tk.BooleanVar()
        ttk.Checkbutton(panel, text='Exclude', variable=self.exclude_var, command=self._exclude_artwork).grid(row=1, column=1)

    def _exclude_artwork(self):
        from ..review_index import set_excluded
        record = self._selected_artwork_record()
        if record:
            set_excluded(self.preprocessed_catalogue.root, record.dataset_id, record.street_id, self.exclude_var.get())
            self._refresh_selected_artwork()

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
        self.artwork_var.set('Save in Inkscape to update this face automatically.' if record else 'Select a face')
        self.artwork_buttons['edit'].configure(state='normal' if workspace.can_edit else 'disabled')
        review = current_review_state(self.preprocessed_catalogue.root, record.dataset_id, record.street_id,
                                      self._formal_review_svg()) if record else None
        self.exclude_var.set(bool(review and review.export_blocked))

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
            self.face_grid.watch_edit(workspace, self.state.selected_dataset, self.state.selected_street)
            launch_inkscape(workspace, executable)
            self.status_var.set("Save in Inkscape to update the face automatically.")
        except Exception as error:
            self._show_error(f"Could not open working edit: {error}")
        finally:
            self._refresh_artwork()

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
