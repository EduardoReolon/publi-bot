# Publica a versao nova dos prompts das redes (o post comeca pela DOR, com as
# falas reais do publico, sem alarmismo) nas contas que ja existem. A anterior
# fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_linkedin", "social_instagram", "social_gmn"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0019_regiao_nos_posts"),
        ("radar", "0036_dores_e_falas"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
