"""Read-only Tk smoke check against a supplied prepared dataset."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import tkinter as tk

from mug_previewer.ui.app import MugPreviewerApp
from mug_previewer.ui.workspace import WORKFLOW_FILTERS, workflow_counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset-root', type=Path, required=True)
    parser.add_argument('--dataset-id', required=True)
    parser.add_argument('--preprocessed', type=Path, required=True)
    args = parser.parse_args()
    def snapshots():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in args.preprocessed.glob('svg_review_results*.json')}
    before = snapshots()
    root = tk.Tk()
    root.geometry('1280x850+20+20')
    errors = []
    root.report_callback_exception = lambda *error: errors.append(str(error))
    app = MugPreviewerApp(root, dataset_root=args.dataset_root, preprocessed=args.preprocessed)
    try:
        root.update()
        label = next(label for label, data in app.dataset_by_label.items() if data.id == args.dataset_id)
        app.dataset_var.set(label)
        app._select_dataset()
        deadline = time.monotonic() + 120
        while not app.workflow_items and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        counts = workflow_counts(app.workflow_items)
        for selected in WORKFLOW_FILTERS:
            app.workflow_var.set(selected)
            app._apply_filter()
            assert len(app.state.filtered_streets) == counts[selected]
        app.workflow_var.set('Production Ready')
        app._apply_filter()
        app.street_list.selection_set(0)
        started = time.perf_counter()
        app._select_street()
        selection_seconds = time.perf_counter() - started
        assert app.state.current_front_preview is not None
        assert app.state.current_wrap is None
        assert str(app.export_button['state']) == 'normal'
        app._preview_artwork('authoritative')
        assert app._review_target is not None
        app._start_render()
        deadline = time.monotonic() + 60
        ticks = 0
        while app.state.current_wrap is None and time.monotonic() < deadline:
            root.update()
            ticks += 1
            time.sleep(.01)
        assert app.state.current_wrap is not None, app.status_var.get()
        assert app._mug_front_image is not None
        assert app.state.current_rear_preview is not None
        for index in range(4):
            app.preview_tabs.select(index)
            root.update()
        app._open_batch_window()
        root.update()
        assert app.workflow_tabs.select() == str(app.batch_panel)
        app.workflow_tabs.select(0)
        app.preview_tabs.select(0)
        root.update()
        root.attributes('-topmost', True)
        root.lift()
        root.update()
        time.sleep(.2)
        from PIL import ImageGrab
        screenshots = []
        for width, height in ((1280, 850), (1440, 900)):
            root.geometry(f'{width}x{height}+20+20')
            for tab, label in ((0, 'workflow'), (1, 'batch')):
                app.workflow_tabs.select(tab)
                deadline = time.monotonic() + .5
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                output = Path(f'output/workspace_{width}x{height}_{label}.png')
                output.parent.mkdir(exist_ok=True)
                ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                    root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())).save(output)
                screenshots.append(str(output))
        assert not errors, errors
        assert snapshots() == before, 'Real QA changed'
        print(json.dumps(dict(counts=counts, selection_seconds=round(selection_seconds, 3),
                              responsive_ticks=ticks, tk_errors=errors, qa_unchanged=True, screenshots=screenshots)))
    finally:
        app._shutdown()


if __name__ == '__main__':
    main()
