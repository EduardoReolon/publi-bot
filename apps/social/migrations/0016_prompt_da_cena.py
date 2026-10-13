# Publica a versao nova do prompt da cena (agora devolve tambem o prompt de
# movimento, para animar a lamina) nas contas que ja existem: o `semear_prompts`
# do deploy so cria o que falta. A anterior fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_cena"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0015_identidade_por_tipo"),
        ("content", "0031_prompts_de_atencao"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
