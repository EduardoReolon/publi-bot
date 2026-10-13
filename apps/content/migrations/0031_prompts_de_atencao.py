# Publica a versao nova dos prompts que escrevem o artigo (atencao, destaque,
# Cialdini) nas contas que ja existem: o `semear_prompts` do deploy so cria o
# que falta. A versao anterior fica no historico, a um clique de voltar.

from django.db import migrations

CHAVES = {"section_draft", "seo_draft", "article_framing"}


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao(CHAVES)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0030_topic_debate_alter_topic_origin"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
