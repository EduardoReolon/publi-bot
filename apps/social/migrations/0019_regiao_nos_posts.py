# Publica a versao nova dos prompts das redes (a REGIAO onde o negocio atende,
# trazida para perto do leitor) nas contas que ja existem. A anterior fica no
# historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_linkedin", "social_instagram", "social_gmn"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0018_quem_escreve"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
