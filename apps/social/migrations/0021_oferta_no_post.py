# Publica a versao nova dos prompts das redes (o post deixa claro o que o
# negocio faz por aquela dor, a OFERTA, sem virar anuncio) nas contas que ja
# existem. A anterior fica no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao

    atualizar_prompts_padrao({"social_linkedin", "social_instagram", "social_gmn"})


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0020_dor_primeiro"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
