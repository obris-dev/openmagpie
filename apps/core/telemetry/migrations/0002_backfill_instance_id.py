import uuid

from django.db import migrations


def backfill_instance_id(apps, schema_editor):
    """Telemetry became opt-OUT: every non-disabled instance now emits, so the
    default (UNSET) singleton needs the anonymous distinct_id that used to be minted
    only on opt-in. Mint one for any row still missing it (a pre-upgrade install)."""
    TelemetrySettings = apps.get_model("telemetry", "TelemetrySettings")
    for row in TelemetrySettings.objects.filter(instance_id=""):
        row.instance_id = str(uuid.uuid4())
        row.save(update_fields=["instance_id"])


class Migration(migrations.Migration):
    dependencies = [("telemetry", "0001_initial")]

    operations = [migrations.RunPython(backfill_instance_id, migrations.RunPython.noop)]
