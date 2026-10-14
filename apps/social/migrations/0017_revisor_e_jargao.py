# Publica a versao nova do revisor de clareza (cobra jargao para o publico, a
# segunda lamina que prende e a sequencia que responde) nas contas que ja
# existem. A anterior fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_revisor"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0016_prompt_da_cena"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
