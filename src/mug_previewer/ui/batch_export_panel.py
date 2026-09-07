"""Compact batch controls; workers communicate only through a queue."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from ..batch_export import build_batch_plan, execute_batch_export
from ..manual_svg_workspace import open_local_path

PROVIDERS = {'Inkthreadable': 'inkthreadable_11oz_white',
             'Printify': 'printify_generic_11oz_ceramic'}


def result_summary(result):
    s = result.summary
    heading = 'Batch cancelled' if result.cancelled else 'Batch finished'
    return (f"{heading}: {s['exported']} exported, {s['failed']} failed\n"
            f"{s['skipped_existing']} existing, {s['manual_review']} manual review, "
            f"{s.get('not_reviewed', 0)} not reviewed, {s['qa_blocked'] - s.get('not_reviewed', 0)} QA problems\n{s['excluded']} Do Not Use, "
            f"{s['unrenderable']} unrenderable, {s['asset_errors']} asset errors, "
            f"{s['cancelled']} cancelled")


class BatchExportPanel(ttk.LabelFrame):
    def __init__(self, parent, app):
        super().__init__(parent, text='Production batch', padding=6)
        self.app = app
        self.plan = None
        self.busy = False
        self.events = queue.SimpleQueue()
        self.cancel_event = threading.Event()
        self.generation = 0
        self.report_path = None
        self.provider = tk.StringVar(value='Inkthreadable')
        self.destination = tk.StringVar()
        self.policy = tk.StringVar(value='Skip existing')
        self.summary = tk.StringVar(value='Select a dataset and destination')
        self.progress = tk.StringVar()
        self.columnconfigure(0, weight=1)
        self.provider_box = ttk.Combobox(self, textvariable=self.provider,
                                        values=list(PROVIDERS), state='readonly')
        self.provider_box.grid(row=0, column=0, sticky='ew')
        self.provider_box.bind('<<ComboboxSelected>>', lambda e: self.invalidate())
        self.choose = ttk.Button(self, text='Choose destination folder', command=self.choose_folder)
        self.choose.grid(row=1, column=0, sticky='ew')
        ttk.Label(self, textvariable=self.destination, wraplength=265).grid(row=2, column=0, sticky='w')
        self.policy_box = ttk.Combobox(self, textvariable=self.policy,
                                      values=['Skip existing', 'Replace existing'], state='readonly')
        self.policy_box.grid(row=3, column=0, sticky='ew')
        self.policy_box.bind('<<ComboboxSelected>>', lambda e: self.invalidate())
        ttk.Label(self, textvariable=self.summary, wraplength=265).grid(row=4, column=0, sticky='w')
        self.refresh = ttk.Button(self, text='Refresh Batch Plan', command=self.refresh_plan)
        self.refresh.grid(row=5, column=0, sticky='ew')
        self.start = ttk.Button(self, text='Export 0 Production-Ready PNGs', command=self.start_batch, state='disabled')
        self.start.grid(row=6, column=0, sticky='ew')
        self.cancel = ttk.Button(self, text='Cancel Batch', command=self.cancel_event.set, state='disabled')
        self.cancel.grid(row=7, column=0, sticky='ew')
        ttk.Label(self, textvariable=self.progress, wraplength=265).grid(row=8, column=0, sticky='w')
        links = ttk.Frame(self)
        links.grid(row=9, column=0, sticky='ew')
        ttk.Button(links, text='Open Export Folder', command=self.open_folder).pack(side='left')
        self.report_button = ttk.Button(links, text='View Export Report', command=self.open_report, state='disabled')
        self.report_button.pack(side='left')
        self.after(50, self.drain)

    def invalidate(self):
        if self.busy:
            return
        self.generation += 1
        self.plan = None
        self.start.configure(state='disabled', text='Export 0 Production-Ready PNGs')
        self.summary.set('Refresh the plan for the selected dataset')

    def choose_folder(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.app.root, title='Choose batch production destination')
        if selected:
            self.destination.set(selected)
            self.invalidate()
            self.refresh_plan()

    def set_busy(self, busy, *, exporting=False):
        self.busy = busy
        for widget in (self.choose, self.refresh):
            widget.configure(state='disabled' if busy else 'normal')
        for widget in (self.provider_box, self.policy_box):
            widget.configure(state='disabled' if busy else 'readonly')
        self.start.configure(state='disabled')
        self.cancel.configure(state='normal' if exporting else 'disabled')
        # Keep dataset scope fixed while planning/exporting; other street browsing
        # remains cached and artwork mutations are guarded by domain rechecks.
        self.app.dataset_box.configure(state='disabled' if busy else 'readonly')

    def refresh_plan(self):
        if self.busy:
            return
        data = self.app.state.selected_dataset
        if data is None or not self.destination.get():
            self.invalidate()
            self.summary.set('Select a dataset and destination first')
            return
        self.invalidate()
        self.set_busy(True)
        self.summary.set('Checking prepared artwork and QA...')
        args = (self.app.preprocessed_catalogue.root, data, PROVIDERS[self.provider.get()],
                Path(self.destination.get()))
        options = dict(replace_existing=self.policy.get() == 'Replace existing',
                       design_options=self.app.state.design_options)
        threading.Thread(target=self.plan_worker, args=(self.generation, args, options), daemon=True).start()

    def plan_worker(self, generation, args, options):
        try:
            self.events.put(('plan', generation, build_batch_plan(*args, **options)))
        except Exception as error:
            self.events.put(('error', generation, str(error)))

    def start_batch(self):
        if self.busy or self.plan is None or not self.plan.summary.ready or not self.destination.get():
            return
        if self.app.state.selected_dataset is None or self.plan.dataset.id != self.app.state.selected_dataset.id:
            self.invalidate()
            return
        self.cancel_event.clear()
        self.set_busy(True, exporting=True)
        self.progress.set(f'Exporting 0 / {self.plan.summary.ready}')
        threading.Thread(target=self.export_worker, args=(self.generation, self.plan), daemon=False).start()

    def export_worker(self, generation, plan):
        try:
            result = execute_batch_export(plan, cancel_event=self.cancel_event,
                on_progress=lambda p: self.events.put(('progress', generation, p)))
            self.events.put(('result', generation, result))
        except Exception as error:
            self.events.put(('error', generation, str(error)))

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
            if kind == 'progress':
                self.progress.set(f'Exporting {value.current} / {value.total}\n'
                                  f'{value.result.item.street_id} - {value.result.item.street_name}: {value.result.result}')
                continue
            self.set_busy(False)
            if kind == 'plan':
                self.plan = value
                s = value.summary
                self.summary.set(f'{value.dataset.display_name}: {s.total} prepared\nReady: {s.ready} | Manual review: {s.manual_review}\n'
                                 f'Not reviewed: {s.not_reviewed} | QA problems: {s.qa_blocked - s.not_reviewed} | Do Not Use: {s.excluded}\n'
                                 f'Unrenderable: {s.unrenderable} | Asset errors: {s.asset_errors}\nExisting files: {s.existing}')
                self.start.configure(text=f'Export {s.ready} Production-Ready PNGs',
                                     state='normal' if s.ready else 'disabled')
            elif kind == 'result':
                self.report_path = value.report_path
                self.report_button.configure(state='normal')
                self.summary.set(result_summary(value))
                self.progress.set('Report saved. Refresh the plan before another batch.')
                self.plan = None
            else:
                self.plan = None
                self.summary.set(f'Batch cannot start or finish: {value}')
        self.after(50, self.drain)

    def open_folder(self):
        path = self.report_path.parent if self.report_path else (
            Path(self.destination.get()) / PROVIDERS[self.provider.get()] if self.destination.get() else None)
        if path is not None and not path.exists():
            path = path.parent
        self._open(path)

    def open_report(self):
        self._open(self.report_path)

    def _open(self, path):
        try:
            if path is None:
                raise ValueError('Choose an export folder first')
            open_local_path(path)
        except Exception as error:
            self.app._show_error(str(error))
