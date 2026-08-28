"""Confere as heuristicas de extracao contra casos reais.

As heuristicas de `blocos.py` e `flows.py` foram calibradas a mao, olhando PDF
por PDF. Nao aprendem sozinhas — e o risco disso e conhecido: mexer numa regra
para consertar um artigo pode quebrar outro que ja funcionava. Este comando e o
que torna esse risco visivel.

Dois modos.

    # 1. Regressao sobre uma pasta de PDFs (nao toca no banco)
    manage.py conferir_extracao --pasta casos/
    manage.py conferir_extracao --pasta casos/ --gravar   # snapshot inicial

    # 2. Os casos que o acervo ja coletou sozinho
    manage.py tenant_command conferir_extracao --schema=acme --acervo

O modo `--acervo` nao precisa de PDF nenhum: a curadoria e o gabarito. Todo
documento em que a pessoa corrigiu um campo e um caso rotulado, de graca.
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.conferencia import (
    caminho_do_esperado,
    carregar_esperado,
    casos_do_acervo,
    comparar_com_o_esperado,
    extrair_de_arquivo,
    gravar_esperado,
    taxa_de_acerto,
)


class Command(BaseCommand):
    help = "Compara o que a extracao propoe com o que se esperava dela."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pasta",
            help="Diretorio com os PDFs de conferencia.",
        )
        parser.add_argument(
            "--esperados",
            default="fixtures/extracao",
            help="Onde ficam os JSON de resultado esperado (default: fixtures/extracao).",
        )
        parser.add_argument(
            "--gravar",
            action="store_true",
            help="Grava o resultado atual como esperado. Use depois de conferir a olho.",
        )
        parser.add_argument(
            "--acervo",
            action="store_true",
            help="Lista os documentos ja curados em que a extracao errou.",
        )

    def handle(self, *args, **options):
        if not options["pasta"] and not options["acervo"]:
            raise CommandError("Escolha --pasta <dir> ou --acervo.")

        falhou = False
        if options["pasta"]:
            falhou = self._conferir_pasta(
                Path(options["pasta"]),
                Path(options["esperados"]),
                gravar=options["gravar"],
            )
        if options["acervo"]:
            self._conferir_acervo()

        if falhou:
            raise CommandError("Ha divergencias. Confira acima antes de gravar.")

    # -- pasta de PDFs ------------------------------------------------------
    def _conferir_pasta(self, pasta: Path, esperados: Path, *, gravar: bool) -> bool:
        if not pasta.is_dir():
            raise CommandError(f"{pasta} nao e um diretorio.")

        pdfs = sorted(pasta.glob("*.pdf"))
        if not pdfs:
            raise CommandError(f"Nenhum PDF em {pasta}.")

        houve_diferenca = False
        for pdf in pdfs:
            resultado = extrair_de_arquivo(pdf)
            alvo = caminho_do_esperado(pdf, esperados)
            esperado = carregar_esperado(alvo)

            if esperado is None:
                self._novo(pdf, resultado, alvo, gravar=gravar)
                continue

            diferencas = comparar_com_o_esperado(resultado, esperado)
            if not diferencas:
                self.stdout.write(self.style.SUCCESS(f"IGUAL  {pdf.name}"))
                continue

            houve_diferenca = True
            self.stdout.write(self.style.WARNING(f"MUDOU  {pdf.name}"))
            for diferenca in diferencas:
                self.stdout.write(f"  {diferenca}")
            if gravar:
                gravar_esperado(alvo, resultado)
                self.stdout.write(f"  -> gravado em {alvo}")

        if houve_diferenca and not gravar:
            self.stdout.write("")
            self.stdout.write(
                "Diferenca nao e erro por si so: pode ser a melhora que voce acabou de "
                "fazer. Confira a olho e, se estiver certo, rode de novo com --gravar."
            )
        return houve_diferenca and not gravar

    def _novo(self, pdf: Path, resultado: dict, alvo: Path, *, gravar: bool) -> None:
        self.stdout.write(f"NOVO   {pdf.name}")
        self.stdout.write(f"  titulo : {resultado['title']!r}")
        self.stdout.write(f"  autores: {resultado['authors']!r}")
        self.stdout.write(f"  ano/doi: {resultado['year']} {resultado['doi']!r}")
        self.stdout.write(f"  blocos : {len(resultado['blocos'])}")
        for titulo in resultado["blocos"]:
            self.stdout.write(f"     - {titulo or '(sem titulo)'}")
        if gravar:
            gravar_esperado(alvo, resultado)
            self.stdout.write(f"  -> gravado em {alvo}")
        else:
            self.stdout.write("  (sem esperado ainda; rode com --gravar depois de conferir)")

    # -- acervo -------------------------------------------------------------
    def _conferir_acervo(self) -> None:
        resumo = taxa_de_acerto()
        if not resumo["documentos"]:
            self.stdout.write(
                "Nenhum documento curado ainda. A comparacao precisa da conferencia "
                "humana: antes dela os campos SAO a sugestao."
            )
            return

        self.stdout.write(
            f"Documentos conferidos: {resumo['documentos']} | "
            f"campos certos: {resumo['acertos']}/{resumo['campos']} ({resumo['percentual']}%)"
        )

        casos = casos_do_acervo()
        if not casos:
            self.stdout.write(self.style.SUCCESS("Nenhuma correcao: a extracao acertou tudo."))
            return

        self.stdout.write("")
        self.stdout.write("Onde a curadoria discordou da extracao:")
        for caso in casos:
            self.stdout.write(f"\n  {caso.titulo[:70]}  [{caso.metodo}]")
            self.stdout.write(f"  {caso.documento_id}")
            for divergencia in caso.divergencias:
                self.stdout.write(f"     {divergencia.campo}:")
                self.stdout.write(f"       extraiu : {divergencia.sugerido!r}")
                self.stdout.write(f"       era     : {divergencia.corrigido!r}")

        self.stdout.write("")
        self.stdout.write(
            "Cada um destes e um caso para calibrar. Copie o PDF para a pasta de "
            "conferencia e rode --pasta para iterar sem quebrar os que ja funcionam. "
            "Ver docs/EXTRACAO.md."
        )
