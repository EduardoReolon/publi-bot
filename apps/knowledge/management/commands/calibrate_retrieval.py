"""Mede as distancias reais do corpus para calibrar o limiar de recuperacao.

Existe porque `RAG_MAX_COSINE_DISTANCE` NAO e transferivel entre modelos nem
entre corpora. Medicao feita neste projeto com `multilingual-e5-large`: as
distancias se concentraram entre 0.119 e 0.200 — ate uma passagem
completamente fora do assunto ficou em 0.2004. Um limiar de 0.35, que soa
razoavel em abstrato, deixaria passar tudo.

Uso:

    manage.py tenant_command calibrate_retrieval --schema=acme \\
        --consulta "monitoramento de pressao na gravidez"
"""

from __future__ import annotations

import statistics

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.embeddings import get_embedding_client
from apps.knowledge.models import SuperChunk


class Command(BaseCommand):
    help = "Lista as distancias de cosseno entre uma consulta e todo o corpus do tenant."

    def add_arguments(self, parser):
        parser.add_argument("--consulta", required=True, help="Texto da consulta.")
        parser.add_argument(
            "--limite", type=int, default=20, help="Quantos trechos exibir (default: 20)."
        )

    def handle(self, *args, **options):
        from pgvector.django import CosineDistance

        cliente = get_embedding_client()
        total = SuperChunk.objects.filter(is_active=True, embedding__isnull=False).count()
        if total == 0:
            raise CommandError("Nenhum trecho indexado neste tenant.")

        vetor = cliente.embed_query(options["consulta"])
        trechos = (
            SuperChunk.objects.filter(is_active=True, embedding__isnull=False)
            .annotate(distancia=CosineDistance("embedding", vetor))
            .order_by("distancia")[: options["limite"]]
        )

        self.stdout.write(f"Modelo: {cliente.model_name}")
        self.stdout.write(f"Corpus: {total} trechos indexados")
        self.stdout.write(f"Consulta: {options['consulta']!r}\n")
        self.stdout.write(f"{'dist':>8}  {'doc':>4}  titulo")
        self.stdout.write("-" * 66)

        distancias = []
        for t in trechos:
            distancias.append(float(t.distancia))
            titulo = (t.source_title or str(t.document_id))[:44]
            self.stdout.write(f"{t.distancia:8.4f}  {t.kind[:4]:>4}  {titulo}")

        if len(distancias) < 2:
            return

        self.stdout.write("")
        self.stdout.write(f"menor  : {min(distancias):.4f}")
        self.stdout.write(f"mediana: {statistics.median(distancias):.4f}")
        self.stdout.write(f"maior  : {max(distancias):.4f}")
        self.stdout.write(
            "\nEscolha o limiar OLHANDO a lista: o valor certo fica entre a "
            "ultima linha que voce considera relevante e a primeira que nao e. "
            "A faixa costuma ser estreita, entao um limiar generoso nao filtra "
            "nada."
        )
