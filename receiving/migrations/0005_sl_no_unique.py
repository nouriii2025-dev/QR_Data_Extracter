from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("receiving", "0004_sl_no_add")]

    operations = [
        migrations.AlterField("scanrecord", "sl_no", models.PositiveIntegerField(unique=True)),
        migrations.AlterModelOptions("scanrecord", options={"ordering": ["sl_no"]}),
    ]
