# Separada da 0009: no Postgres, gravar linhas em social_anuncio e criar o indice
# da tabela na mesma transacao falha ("pending trigger events").

import uuid

import django.utils.timezone
from django.db import migrations


def impulsos_digitados(apps, schema_editor):
    """O valor digitado antes (so no post) vira um impulso na lista do post."""
    Post = apps.get_model("social", "Post")
    Anuncio = apps.get_model("social", "Anuncio")
    for post in Post.objects.filter(impulsionado=True, anuncios__isnull=True).exclude(
        custo_impulso=None
    ):
        quando = post.impulso_em or post.publicado_em or django.utils.timezone.now()
        Anuncio.objects.create(
            destino_id=post.destino_id,
            id_remoto=f"manual:{uuid.uuid4().hex}",
            nome="Impulso digitado",
            gasto=post.custo_impulso,
            inicio=django.utils.timezone.localtime(quando).date(),
            post=post,
            origem="manual",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("social", "0009_publico_do_impulso"),
    ]

    operations = [
        migrations.RunPython(impulsos_digitados, migrations.RunPython.noop),
    ]
