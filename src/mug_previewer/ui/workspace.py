"""Navigation over a backend plan; visible streets never define export scope."""
from collections import Counter
from ..batch_export import Eligibility, build_batch_plan

WORKFLOW_FILTERS = ('All', 'Production Ready', 'Manual Review', 'QA Attention', 'Asset Error', 'Do Not Use', 'Unrenderable')
CATEGORIES = {
    Eligibility.READY: 'Production Ready',
    Eligibility.MANUAL_REVIEW: 'Manual Review',
    Eligibility.QA_BLOCKED: 'QA Attention',
    Eligibility.ASSET_ERROR: 'Asset Error',
    Eligibility.EXCLUDED: 'Do Not Use',
    Eligibility.UNRENDERABLE: 'Unrenderable',
}


def load_workflow(root, dataset):
    plan = build_batch_plan(root, dataset, 'inkthreadable_11oz_white', root / '.workspace-scope')
    return {item.street_id: item for item in plan.items}


def workflow_counts(items):
    counts = Counter(CATEGORIES[item.eligibility] for item in items.values())
    return {name: len(items) if name == 'All' else counts[name] for name in WORKFLOW_FILTERS}


def filter_workflow(streets, items, selected):
    if selected not in WORKFLOW_FILTERS:
        raise ValueError('Unknown workflow filter')
    return [street for street in streets if selected == 'All' or
            (street.id in items and CATEGORIES[items[street.id].eligibility] == selected)]
