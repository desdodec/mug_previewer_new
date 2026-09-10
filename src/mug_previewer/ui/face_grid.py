"""Virtual face cards backed exclusively by prepared PNG files."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import math
import queue
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

from ..manual_svg_workspace import (ManualSvgWorkspace, EditSaveMonitor,
                                   launch_inkscape, find_inkscape_executable, revert_generated)
from ..review_index import load_exclusions, set_excluded
from .state import load_preprocessed_catalogue


@lru_cache(maxsize=64)
def cached_thumbnail(path, modified, size):
    with Image.open(path) as source:
        image = source.convert('RGBA')
        image.thumbnail((size, size // 2))
        return image


class FaceGrid(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.streets = []
        self.cards = {}
        self.monitors = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='face-edit')
        self.pending = None
        self.edit_events = queue.SimpleQueue()
        self.columns = tk.IntVar(value=2)
        self.size = tk.IntVar(value=320)
        bar = ttk.Frame(self)
        bar.pack(fill='x')
        ttk.Label(bar, text='Columns').pack(side='left')
        box = ttk.Combobox(bar, textvariable=self.columns, values=(1, 2, 3, 4), state='readonly', width=3)
        box.pack(side='left')
        box.bind('<<ComboboxSelected>>', lambda e: self.redraw())
        ttk.Label(bar, text='Card size').pack(side='left', padx=8)
        size = ttk.Combobox(bar, textvariable=self.size, values=(240, 320, 400, 480), state='readonly', width=5)
        size.pack(side='left')
        size.bind('<<ComboboxSelected>>', lambda e: self.redraw())
        self.canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient='vertical', command=self.scroll)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind('<Configure>', lambda e: self.redraw())
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.after(600, self.poll)

    def set_streets(self, streets):
        same_dataset = getattr(self, '_dataset_id', None) == self.app.state.selected_dataset.id
        self._dataset_id = self.app.state.selected_dataset.id
        self.streets = list(streets)
        if not same_dataset:
            self.canvas.yview_moveto(0)
        self.redraw()

    def scroll(self, *args):
        self.canvas.yview(*args)
        self.draw_visible()

    def wheel(self, event):
        self.scroll('scroll', -int(event.delta / 120), 'units')
        return 'break'

    def redraw(self):
        for frame, window in self.cards.values():
            self.canvas.delete(window)
            frame.destroy()
        self.cards.clear()
        self.draw_visible()

    def draw_visible(self):
        columns = self.columns.get()
        width = max(120, self.canvas.winfo_width() // columns)
        thumb = min(self.size.get(), width - 20)
        height = thumb // 2 + 110
        self.canvas.configure(scrollregion=(0, 0, width * columns, math.ceil(len(self.streets) / columns) * height))
        first = max(0, int(self.canvas.canvasy(0) // height))
        last = first + math.ceil(max(1, self.canvas.winfo_height()) / height) + 1
        visible = set(range(first * columns, min(len(self.streets), last * columns)))
        for index in set(self.cards) - visible:
            frame, window = self.cards.pop(index)
            self.canvas.delete(window)
            frame.destroy()
        data = self.app.state.selected_dataset
        catalogue = self.app.preprocessed_catalogue
        try:
            exclusions = load_exclusions(catalogue.root)
        except (OSError, ValueError):
            exclusions = {}
        for index in sorted(visible - self.cards.keys()):
            street = self.streets[index]
            record = catalogue.find(data, street)
            card = ttk.LabelFrame(self.canvas, padding=6, relief='groove')
            window = self.canvas.create_window((index % columns) * width, (index // columns) * height,
                                               window=card, anchor='nw', width=width, height=height)
            self.cards[index] = card, window
            label = ttk.Label(card, anchor='center')
            label.pack(fill='x')
            try:
                path = record.preview_path
                photo = ImageTk.PhotoImage(cached_thumbnail(str(path), path.stat().st_mtime_ns, thumb))
                label.configure(image=photo)
                label.image = photo
            except (AttributeError, OSError, ValueError):
                label.configure(text='Preview unavailable')
            ttk.Label(card, text=f'{street.id} \u2014 {street.display_name}', wraplength=width-16).pack()
            if record and record.state and record.state.value == 'MANUAL_REVIEW':
                ttk.Label(card, text='Needs Attention').pack()
            actions = ttk.Frame(card)
            actions.pack()
            workspace = ManualSvgWorkspace.from_record(record, catalogue.records.values())
            ttk.Button(actions, text='Edit in Inkscape', command=lambda s=street: self.edit(s),
                       state='normal' if workspace.can_edit else 'disabled').pack(side='left')
            item = self.app.workflow_items.get(street.id)
            excluded = tk.BooleanVar(value=exclusions.get((data.id, street.id), bool(item and item.review_status not in (None, 'pass'))))
            ttk.Checkbutton(actions, text='Exclude', variable=excluded,
                            command=lambda s=street, v=excluded: self.exclude(s, v.get())).pack(side='left')
            card.excluded = excluded
            menu = tk.Menu(card, tearoff=False)
            menu.add_command(label='Revert to generated original', command=lambda s=street: self.revert(s),
                             state='normal' if record and record.state and record.state.value == 'MANUAL_APPROVED' else 'disabled')
            def bind(widget, popup=menu):
                widget.bind('<Button-1>', lambda e, s=street: self.select(s))
                widget.bind('<MouseWheel>', self.wheel)
                widget.bind('<Button-3>', lambda e: popup.tk_popup(e.x_root, e.y_root))
                for child in widget.winfo_children():
                    bind(child, popup)
            bind(card)
        self.highlight_selection()

    def select(self, street):
        index = self.app.state.filtered_streets.index(street)
        self.app.street_list.selection_clear(0, tk.END)
        self.app.street_list.selection_set(index)
        self.app.street_list.activate(index)
        self.app.street_list.see(index)
        self.app._select_street()

    def highlight_selection(self):
        selected = getattr(self.app.state, 'selected_street', None)
        for index, (card, _) in self.cards.items():
            active = self.streets[index] == selected
            card.configure(text='Current face' if active else '',
                           relief='solid' if active else 'groove', borderwidth=3 if active else 1)

    def sync_selection(self):
        selected = self.app.state.selected_street
        if selected in self.streets:
            columns = self.columns.get()
            width = max(120, self.canvas.winfo_width() // columns)
            height = min(self.size.get(), width - 20) // 2 + 110
            top = (self.streets.index(selected) // columns) * height
            view_top = self.canvas.canvasy(0)
            if top < view_top or top + height > view_top + self.canvas.winfo_height():
                total = max(1, math.ceil(len(self.streets) / columns) * height)
                self.canvas.yview_moveto(top / total)
            self.draw_visible()
        self.highlight_selection()

    def exclude(self, street, value):
        try:
            set_excluded(self.app.preprocessed_catalogue.root, self.app.state.selected_dataset.id, street.id, value)
            self.app._reload_workflow()
            self.app.batch_panel.invalidate()
        except Exception as error:
            self.app._show_error(str(error))

    def edit(self, street):
        self.select(street)
        try:
            data = self.app.state.selected_dataset
            catalogue = self.app.preprocessed_catalogue
            workspace = ManualSvgWorkspace.from_record(catalogue.find(data, street), catalogue.records.values())
            workspace.create_or_get_working_edit()
            executable = find_inkscape_executable(self.app.__dict__.get('_inkscape_executable')) or self.app._choose_inkscape()
            if executable:
                self.watch_edit(workspace, data, street)
                launch_inkscape(workspace, executable)
                self.app.status_var.set('Save in Inkscape to update this face automatically.')
        except Exception as error:
            self.app._show_error(str(error))

    def watch_edit(self, workspace, data, street):
        key = data.id, street.id
        if key not in self.monitors:
            self.monitors[key] = EditSaveMonitor(workspace, data, street, self.app.preprocessed_catalogue.root,
                                                  on_detected=lambda: self.edit_events.put(key))

    @staticmethod
    def check_saves(monitors):
        changes, errors = [], []
        for key, monitor in monitors:
            try:
                if monitor.poll():
                    changes.append(key)
            except Exception as error:
                errors.append(f'{key[1]}: {error}')
        return changes, errors

    def poll(self):
        if self.app._shutting_down:
            self.executor.shutdown(wait=False)
            return
        while not self.edit_events.empty():
            self.edit_events.get_nowait()
            self.app.status_var.set('Edit detected \u2014 updating face...')
        if self.pending is not None and self.pending.done():
            changes, errors = self.pending.result()
            self.pending = None
            if changes:
                self.app._refresh_selected_artwork()
                self.redraw()
                self.app.status_var.set('Face updated from Inkscape.')
            if errors:
                self.app.status_var.set('Save rejected \u2014 last-known-good SVG restored; previous preview kept.')
                self.app._show_error('\n'.join(errors))
        if self.pending is None and self.monitors:
            self.pending = self.executor.submit(self.check_saves, tuple(self.monitors.items()))
        self.after(600, self.poll)

    def revert(self, street):
        try:
            if self.pending is not None and not self.pending.done():
                self.app.status_var.set('Save check in progress; try reverting again shortly.')
                return
            data = self.app.state.selected_dataset
            revert_generated(data, street, self.app.preprocessed_catalogue.root)
            monitor = self.monitors.get((data.id, street.id))
            if monitor:
                monitor.accepted = monitor.workspace.working_svg.read_bytes()
                monitor.pending = None
            self.app._refresh_selected_artwork()
            self.redraw()
        except Exception as error:
            self.app._show_error(str(error))
