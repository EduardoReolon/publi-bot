"""Tira da busca os trechos ja indexados que sao ruido (`blocos.e_ruido`).

O filtro vale para o que entra no indice de agora em diante; isto aplica a
mesma regra ao que entrou antes. Nao apaga: marca o trecho como inativo, e
citacoes publicadas continuam intactas.

    manage.py tenant_command desligar_trechos_ruidosos --schema=ekron --seco
    manage.py tenant_command desligar_trechos_ruidosos --schema=ekron
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.knowledge.blocos import e_ruido
from apps.knowledge.models import SuperChunk


class Command(BaseCommand):
    help = "Desliga da busca os trechos indexados que sao ruido (legenda, DOI, letras soltas)."

    def add_arguments(self, parser):
        parser.add_argument("--seco", action="store_true", help="So lista, nao muda nada.")
        parser.add_argument("--mostrar", type=int, default=20, help="Quantos exemplos listar.")

    def handle(self, *args, seco, mostrar, **opcoes):
        ativos = SuperChunk.objects.filter(is_active=True).only("pk", "content", "source_title")
        ruidosos = [t for t in ativos.iterator() if e_ruido(t.content)]
        self.stdout.write(f"{len(ruidosos)} trecho(s) ruidoso(s) de {ativos.count()} ativo(s).")
        for trecho in ruidosos[:mostrar]:
            texto = " ".join(trecho.content.split())[:110]
            self.stdout.write(f"  - {trecho.source_title[:40]!r}: {texto}")
        if seco or not ruidosos:
            return
        desligados = SuperChunk.objects.filter(pk__in=[t.pk for t in ruidosos]).update(
            is_active=False
        )
        self.stdout.write(self.style.SUCCESS(f"{desligados} trecho(s) fora da busca."))
