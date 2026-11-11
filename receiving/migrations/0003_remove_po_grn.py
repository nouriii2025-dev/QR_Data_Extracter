from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("receiving", "0002_serial_and_range")]

    operations = [
        migrations.RemoveField("scanrecord", "po_number"),
        migrations.RemoveField("scanrecord", "grn_number"),
    ]
