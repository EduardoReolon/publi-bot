"""Tira do servidor os documentos em que a extracao errou.

O servidor de producao e um **deploy, nao um clone**: nao ha `casos/` la, nem
faria sentido haver. Mas e la que os PDFs problematicos aparecem, porque e la
que as pessoas trabalham. Este comando resolve essa distancia — materializa cada
caso marcado numa pasta portatil, que voce baixa e joga no `casos/` do seu
repositorio.

    # no servidor
    python manage.py tenant_command exportar_casos --schema=acme --destino=/tmp/casos

    # na sua maquina
    scp -r servidor:/tmp/casos/* casos/
    python manage.py conferir_extracao --pasta casos/

Cada caso vira dois arquivos de mesmo nome: o PDF e um JSON com o que a extracao
propos, o que a curadoria corrigiu e o problema apontado. O JSON e o gabarito —
sem ele o PDF sozinho nao diz o que era para ter saido.

Os PDFs **nao** entram no git (sao obras de terceiros). O que entra e o esperado
gerado depois por `conferir_extracao --gravar`, em `fixtures/extracao/`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.knowledge.conferencia import comparar_com_a_curadoria
from apps.knowledge.models import Document

PADRAO_DE_NOME = re.compile(r"[^a-z0-9]+")


class Command(BaseCommand):
    help = "Exporta os documentos com extracao marcada como ruim, para calibrar."

    def add_arguments(self, parser):
        parser.add_argument("--destino", required=True, help="Pasta onde gravar os casos.")
        parser.add_argument(
            "--todos",
            action="store_true",
            help="Exporta tambem os nao marcados, desde que a curadoria tenha corrigido algo.",
        )
        parser.add_argument(
            "--sem-pdf",
            action="store_true",
            help="So o JSON. Util quando o PDF nao pode sair do servidor.",
        )

    def handle(self, *args, **options):
        destino = Path(options["destino"])
        destino.mkdir(parents=True, exist_ok=True)

        documentos = list(self._selecionar(todos=options["todos"]))
        if not documentos:
            self.stdout.write(
                "Nenhum caso a exportar. Marque a extracao na tela de curadoria, "
                "ou use --todos para incluir os que a curadoria corrigiu."
            )
            return

        for documento in documentos:
            nome = self._nome_do_arquivo(documento)
            self._gravar_json(destino / f"{nome}.json", documento)
            if not options["sem_pdf"]:
                self._gravar_pdf(destino / f"{nome}.pdf", documento)
            self.stdout.write(f"{nome}  {documento.get_extraction_problem_display() or '—'}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{len(documentos)} caso(s) em {destino}."))
        self.stdout.write(
            "Leve para o seu repositorio e rode: "
            "manage.py conferir_extracao --pasta <pasta>. Ver docs/EXTRACAO.md."
        )

    def _selecionar(self, *, todos: bool):
        marcados = Document.objects.filter(extraction_flagged_at__isnull=False)
        if not todos:
            return marcados.order_by("-extraction_flagged_at")

        # `--todos` acrescenta o que a comparacao automatica pegou: documento
        # curado em que a pessoa mudou algum campo. Sao casos de verdade, so que
        # ninguem se deu ao trabalho de marcar.
        vistos = {d.pk for d in marcados}
        extras = [
            documento
            for documento in Document.objects.filter(
                metadata_confidence=Document.MetadataConfidence.MANUAL
            ).exclude(metadata_suggested={})
            if documento.pk not in vistos and comparar_com_a_curadoria(documento)
        ]
        return list(marcados) + extras

    def _nome_do_arquivo(self, documento) -> str:
        base = documento.title or documento.nome_do_arquivo or str(documento.pk)
        limpo = PADRAO_DE_NOME.sub("-", base.lower()).strip("-")[:60]
        # O sufixo evita colisao entre dois artigos de titulo parecido, e amarra
        # o caso ao documento de origem sem precisar abrir o JSON.
        return f"{limpo or 'documento'}-{str(documento.pk)[:8]}"

    def _gravar_json(self, caminho: Path, documento) -> None:
        divergencias = comparar_com_a_curadoria(documento)
        caso = {
            "documento_id": str(documento.pk),
            "arquivo": documento.nome_do_arquivo,
            "metodo": documento.extraction_method,
            "problema": documento.extraction_problem,
            "observacao": documento.extraction_note,
            "extraiu": documento.metadata_suggested or {},
            "conferido": {
                "title": documento.title,
                "authors": documento.authors,
                "year": documento.year,
                "doi": documento.doi,
            },
            "corrigidos": [d.campo for d in divergencias],
            "blocos": self._blocos(documento),
        }
        caminho.write_text(json.dumps(caso, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _blocos(self, documento) -> list[str]:
        from apps.knowledge.blocos import dividir_em_blocos

        return [
            bloco.titulo
            for bloco in dividir_em_blocos(
                documento.markdown_full or "", e_markdown=documento.texto_e_markdown
            )
        ]

    def _gravar_pdf(self, caminho: Path, documento) -> None:
        if not documento.original_file:
            self.stdout.write(self.style.WARNING(f"  sem arquivo: {documento.pk}"))
            return
        try:
            documento.original_file.open("rb")
            try:
                caminho.write_bytes(documento.original_file.read())
            finally:
                documento.original_file.close()
        except FileNotFoundError:
            # A regra de retencao ou uma limpeza podem ter levado o arquivo. O
            # JSON continua util: ele guarda o que saiu e o que era certo.
            self.stdout.write(self.style.WARNING(f"  arquivo ausente no disco: {documento.pk}"))
