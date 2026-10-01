"""Poe no indice, como provisorio, o que ja esta no acervo esperando curadoria.

Aplica aos dados de hoje o que `knowledge.provisorias` faz com o que chega:

    manage.py tenant_command vetorizar_fontes_provisorias --schema=acme --seco
    manage.py tenant_command vetorizar_fontes_provisorias --schema=acme
    manage.py tenant_command vetorizar_fontes_provisorias --schema=acme --local

1. Paginas sugeridas ainda sem decisao: vao para o acervo (baixa e extrai o
   texto principal). A conversao roda na fila; rode o comando de novo depois
   para vetoriza-las.
2. Artigos cientificos sem PDF (sugeridos, ou aprovados esperando o PDF): o
   resumo do OpenAlex vira documento provisorio.
3. Documentos esperando curadoria e ainda fora do indice: vetorizados como
   provisorios (todos os blocos com texto, menos referencias e afins).

A vetorizacao vai para a fila do worker da placa. Sem worker que vetoriza,
so roda com `--local` (no servidor: lento, e ocupa a CPU). Pode rodar de novo
quantas vezes quiser: o que ja foi feito e pulado.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.knowledge import provisorias
from apps.knowledge.models import CandidatoDeFonte, Document


class Command(BaseCommand):
    help = "Vetoriza como provisorio o que espera curadoria (paginas, resumos, documentos)."

    def add_arguments(self, parser):
        parser.add_argument("--seco", action="store_true", help="So conta, nao muda nada.")
        parser.add_argument(
            "--local",
            action="store_true",
            help="Vetoriza no servidor (sem worker, ou para nao esperar por ele).",
        )

    def handle(self, *args, seco: bool, local: bool, **opcoes):
        if not local and not provisorias.ligado():
            self.stderr.write(
                "Nenhum worker marcado para 'Vetorizacao de documentos' em Inferencia. "
                "Marque a carga na conexao do worker (docs/WORKER_VETORIZACAO.md), "
                "ou rode com --local para vetorizar no servidor."
            )
            return

        paginas = CandidatoDeFonte.objects.filter(
            tipo=CandidatoDeFonte.Tipo.PAGINA, situacao=CandidatoDeFonte.Situacao.PENDENTE
        )
        artigos = CandidatoDeFonte.objects.filter(
            tipo=CandidatoDeFonte.Tipo.ARTIGO,
            situacao__in=[
                CandidatoDeFonte.Situacao.PENDENTE,
                CandidatoDeFonte.Situacao.AGUARDANDO_PDF,
            ],
            documento__isnull=True,
        ).exclude(trecho="")
        fora_do_indice = Document.objects.filter(
            status=Document.Status.PENDING_CURATION, chunks__isnull=True
        ).distinct()

        na_fila = fora_do_indice.exclude(indexacao_pedida={})
        self.stdout.write(
            f"Paginas sugeridas sem decisao: {paginas.count()}\n"
            f"Artigos sem PDF, com resumo: {artigos.count()}\n"
            f"Documentos esperando curadoria fora do indice: {fora_do_indice.count()}"
            f" (ja na fila de vetorizacao: {na_fila.count()})"
        )
        # Por que os da fila ainda nao foram vetorizados (o worker diz).
        motivos: dict[str, int] = {}
        for pedido in na_fila.values_list("indexacao_pedida", flat=True):
            motivo = pedido.get("aguardando_worker") or "na fila, ainda nao tentado"
            motivos[motivo] = motivos.get(motivo, 0) + 1
        for motivo, total in motivos.items():
            self.stdout.write(f"  {total}: {motivo}")
        falhas = Document.objects.exclude(indexacao_erro="").filter(
            status=Document.Status.PENDING_CURATION
        )
        for documento in falhas[:10]:
            self.stdout.write(f"  falhou: {documento.title[:60]} — {documento.indexacao_erro}")
        if seco:
            return

        acolhidas = 0
        for candidato in paginas:
            try:
                acolhidas += provisorias.acolher(candidato, local=local)
            except Exception as exc:  # uma pagina fora do ar nao para as outras
                self.stderr.write(f"  pagina {candidato.url}: {exc}")

        resumos = 0
        for candidato in artigos:
            documento = provisorias.documento_do_resumo(candidato)
            resumos += documento is not None

        pedidos = sum(provisorias.indexar(d, local=local) for d in fora_do_indice.all())

        self.stdout.write(
            self.style.SUCCESS(
                f"Paginas enviadas ao acervo: {acolhidas} (a conversao roda na fila; "
                f"rode de novo depois para vetoriza-las).\n"
                f"Resumos de artigos viraram documento: {resumos}.\n"
                f"Documentos na fila de vetorizacao: {pedidos}."
            )
        )
