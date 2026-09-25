from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("publibot_node", "0002_authorphoto_receivedpublication_author_reference"),
    ]

    operations = [
        migrations.AddField(
            model_name="receivedpublication",
            name="faq",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
