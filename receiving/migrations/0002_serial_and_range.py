from django.db import migrations, models


def backfill(apps, schema_editor):
    """Fill the new columns for rows saved by the previous version."""
    from receiving.extractor import pick_range, pick_serial
    ScanRecord = apps.get_model("receiving", "ScanRecord")
    for r in ScanRecord.objects.all():
        r.serial_number = pick_serial(r.details or {}, r.url)
        r.range_text = pick_range(r.details or {})
        r.save(update_fields=["serial_number", "range_text"])


class Migration(migrations.Migration):

    dependencies = [("receiving", "0001_initial")]

    operations = [
        migrations.AddField("scanrecord", "serial_number",
                            models.CharField(blank=True, db_index=True, default="", max_length=100)),
        migrations.AddField("scanrecord", "range_text",
                            models.CharField(blank=True, default="", max_length=100)),
        migrations.AlterModelOptions("scanrecord", options={"ordering": ["id"]}),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
