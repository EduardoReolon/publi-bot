# Os prompts das redes passam a pedir o numero no MESMO sentido do artigo e a
# liberar a cena com os personagens fixos da conta; as abordagens de partida que
# ninguem editou trocam o "nao invente historia" pelo texto que explica isso.
# As versoes anteriores dos prompts ficam no historico.

from django.db import migrations


def publicar(apps, schema_editor):
    from apps.content.services import atualizar_prompts_padrao
    from apps.social.abordagens import NAO_INVENTE, NAO_INVENTE_ANTES, SEMENTES

    atualizar_prompts_padrao({"social_linkedin", "social_instagram", "social_gmn"})
    Abordagem = apps.get_model("social", "Abordagem")
    for semente in SEMENTES:
        antes = semente["instrucao"].replace(NAO_INVENTE, NAO_INVENTE_ANTES)
        Abordagem.objects.filter(nome=semente["nome"], instrucao=antes).update(
            instrucao=semente["instrucao"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0026_cenas_e_lugares"),
    ]

    operations = [
        migrations.RunPython(publicar, migrations.RunPython.noop),
    ]
