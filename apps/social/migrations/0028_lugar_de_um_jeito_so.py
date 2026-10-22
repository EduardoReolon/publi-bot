# Os prompts das redes passam a dizer o lugar de UM jeito so: se a capa ou a 1a
# cena mostra um lugar real da conta (com foto), o texto nao repete a cidade.
# As versoes anteriores dos prompts ficam no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_linkedin", "social_instagram", "social_gmn"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0027_numeros_e_personagens"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
