"""Denormalize the action kind onto each run, then prune pre-column orphans.

The run now self-describes what ran (`WatchActionRun.kind`), so its typed result
stays renderable even if the action is later deleted. New rows get the kind
stamped at enqueue and re-stamped at completion, so they reflect what actually
executed. This one-shot backfills existing rows from their action's CURRENT kind,
with one fidelity caveat: WatchAction.kind is editable and a pre-column run
recorded no kind of its own, so a historical run whose action kind was changed is
stamped with the current kind, not the (unrecoverable) kind it ran under.

A run whose action no longer exists has no recoverable kind (nothing to type its
result), so it is removed. Pruning is by PARENT ABSENCE, not the "" sentinel, so
a run whose action is still live survives even if it briefly carries the default
(a still-deliverable run must not be lost). That delete is destructive and
intended. Account-agnostic (a data migration). Both steps push the join into the
DB via subqueries, so neither the actions nor the run ids are materialized in
Python (no per-row memory, no IN-parameter ceiling to batch under).
"""

from django.db import migrations, models


def _backfill_kind_and_prune_orphans(apps, schema_editor):
    run_model = apps.get_model("watches", "WatchActionRun")
    action_model = apps.get_model("watches", "WatchAction")
    # One UPDATE per distinct kind, each joining WatchAction -> WatchActionRun in
    # the DB (an `action_id IN (SELECT ...)` subquery). The distinct set is tiny
    # (the WatchActionKind values), so no action rows are pulled into Python.
    for kind in action_model.objects.values_list("kind", flat=True).distinct():
        run_model.objects.filter(
            action_id__in=action_model.objects.filter(kind=kind).values("id")
        ).update(kind=kind)
    # Orphans: a run whose action no longer exists. Prune by PARENT ABSENCE, not
    # the "" sentinel, so a run whose action is still live survives even if it
    # momentarily carries "" (an old pre-kind code path enqueuing during a non-
    # stop-the-world deploy); such a run is still deliverable (the drain never
    # reads kind), so the sentinel alone would lose queued work. Expressed as a
    # single `NOT IN (SELECT id FROM watchaction)` subquery: no id list in Python.
    run_model.objects.filter(kind="").exclude(
        action_id__in=action_model.objects.values("id")
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('watches', '0009_watchactiondelivery_watchdeliv_created_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='watchactionrun',
            name='kind',
            field=models.CharField(default='', help_text="WatchActionKind value; denormalized from the action so the run's typed result is renderable even if the action is later deleted", max_length=32, verbose_name='kind'),
        ),
        # Reverse can't resurrect the deleted orphans (and the dropped column takes
        # the backfilled kinds with it), so the data step's reverse is a no-op.
        migrations.RunPython(_backfill_kind_and_prune_orphans, migrations.RunPython.noop),
    ]
