"""Autores gravados como a lista inteira do OpenAlex ("A, B, C, D, E") viram
"A et al.": a ancora da citacao saia com cinco nomes no artigo."""

from django.db import migrations


def _citacao(texto: str) -> str:
    texto = (texto or "").strip()
    if "et al" in texto or texto.count(",") < 2:
        return texto
    primeiro = next((p.strip() for p in texto.replace(";", ",").split(",") if p.strip()), "")
    return f"{primeiro} et al." if primeiro else texto


def corrigir(apps, schema_editor):
    Document = apps.get_model("knowledge", "Document")
    SuperChunk = apps.get_model("knowledge", "SuperChunk")
    for modelo, campo in ((Document, "authors"), (SuperChunk, "source_authors")):
        for pk, valor in modelo.objects.filter(**{f"{campo}__contains": ","}).values_list(
            "pk", campo
        ):
            novo = _citacao(valor)
            if novo != valor:
                modelo.objects.filter(pk=pk).update(**{campo: novo})


class Migration(migrations.Migration):
    dependencies = [("knowledge", "0019_rotulos_de_calibracao")]

    operations = [migrations.RunPython(corrigir, migrations.RunPython.noop)]
