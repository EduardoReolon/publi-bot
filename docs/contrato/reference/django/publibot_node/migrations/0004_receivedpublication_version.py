from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("publibot_node", "0003_receivedpublication_faq"),
    ]

    operations = [
        migrations.AddField(
            model_name="receivedpublication",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="receivedpublication",
            name="updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="receivedpublication",
            name="last_update_key",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
