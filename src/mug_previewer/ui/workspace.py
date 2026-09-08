"""Grid navigation never defines batch export scope."""
from ..batch_export import Eligibility, build_batch_plan

WORKFLOW_FILTERS = ('All', 'Included', 'Excluded', 'Edited', 'Needs Attention')


def load_workflow(root, dataset):
    plan = build_batch_plan(root, dataset, 'inkthreadable_11oz_white', root / '.workspace-scope')
    return {item.street_id: item for item in plan.items}


def matches(item, selected):
    if selected == 'All':
        return True
    if item is None:
        return selected == 'Needs Attention'
    return {
        'Included': item.eligibility == Eligibility.READY,
        'Excluded': item.eligibility == Eligibility.EXCLUDED,
        'Edited': item.production_state == 'MANUAL_APPROVED',
        'Needs Attention': item.production_state == 'MANUAL_REVIEW' or item.eligibility in
            (Eligibility.ASSET_ERROR, Eligibility.UNRENDERABLE, Eligibility.QA_BLOCKED),
    }[selected]


def workflow_counts(items):
    return {name: sum(matches(item, name) for item in items.values()) for name in WORKFLOW_FILTERS}


def filter_workflow(streets, items, selected):
    if selected not in WORKFLOW_FILTERS:
        raise ValueError('Unknown workflow filter')
    return [street for street in streets if matches(items.get(street.id), selected)]
