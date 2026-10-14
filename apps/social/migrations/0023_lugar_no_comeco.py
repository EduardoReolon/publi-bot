# Publica a versao nova dos prompts das redes (o lugar onde o negocio atende
# logo no comeco: gancho, capa ou segunda lamina) nas contas que ja existem. A
# anterior fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_linkedin", "social_instagram", "social_gmn"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0022_local_nos_posts"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
