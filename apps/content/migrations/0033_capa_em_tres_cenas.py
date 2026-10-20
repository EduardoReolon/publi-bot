# Publica a versao nova do prompt da capa (tres descricoes, uma por tipo de
# cena) nas contas que ja existem. A anterior fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"image_prompt"})


class Migration(migrations.Migration):
    dependencies = [("content", "0032_dores_no_artigo")]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
