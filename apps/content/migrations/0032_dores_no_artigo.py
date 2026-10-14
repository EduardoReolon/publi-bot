# Publica a versao nova do plano e da abertura do artigo (as DORES do publico,
# com as falas reais do banco de dores do Radar) nas contas que ja existem. A
# anterior fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"article_outline", "article_framing"})


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0031_prompts_de_atencao"),
        ("radar", "0036_dores_e_falas"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
