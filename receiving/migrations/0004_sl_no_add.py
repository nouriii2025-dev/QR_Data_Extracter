from django.db import migrations, models


def fill(apps, schema_editor):
    ScanRecord = apps.get_model("receiving", "ScanRecord")
    for r in ScanRecord.objects.all():
        r.sl_no = r.id          # keep the numbers people already see
        r.save(update_fields=["sl_no"])


class Migration(migrations.Migration):

    dependencies = [("receiving", "0003_remove_po_grn")]

    operations = [
        migrations.AddField("scanrecord", "sl_no", models.PositiveIntegerField(null=True)),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
