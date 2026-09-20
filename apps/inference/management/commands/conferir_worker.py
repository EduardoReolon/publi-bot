"""Exercita, de ponta a ponta, tudo o que o PubliBot pede ao worker de GPU.

Existe porque os tres `configurar_*` respondem a pergunta errada. Eles
conferem que o ENDERECO responde — e o que eles chamam e `/health/` ou
`/v1/models`, que nao tocam a placa. Passar nos tres e compativel com:

- a credencial certa no `.env` e a errada no banco;
- o Ollama de pe sem o modelo cadastrado no disco;
- os pesos da difusao faltando;
- o Docling que sobe e explode na primeira pagina.

Todos esses aparecem so dentro de um trabalho, minutos ou horas depois, num
painel de operacao — longe de quem acabou de instalar.

Aqui cada rota e chamada DE VERDADE, pelos mesmos adaptadores que os fluxos
usam: `get_provider`, `get_image_provider`, `converter_no_worker`. Uma
conferencia que roda um codigo parecido confere o codigo parecido.

    manage.py conferir_worker              # as tres rotas
    manage.py conferir_worker --rapido     # so texto (segundos)
    manage.py conferir_worker --so imagem

Sai com codigo 1 se qualquer recurso falhar, entao serve em script de
implantacao.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import httpx
from django.core.management.base import BaseCommand, CommandError

from apps.inference.models import InferenceConnection

# Um PDF de uma pagina, escrito a mao. Nao ha arquivo de apoio de proposito:
# a conferencia tem de rodar num servidor recem-provisionado, onde `fixtures/`
# pode nem ter sido copiado.
PDF_MINIMO = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]"
    b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
    b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"5 0 obj<</Length 52>>stream\n"
    b"BT /F1 18 Tf 20 60 Td (Teste de conversao) Tj ET\n"
    b"endstream endobj\n"
    b"trailer<</Root 1 0 R>>\n"
)


@dataclass
class Resultado:
    nome: str
    ok: bool = False
    segundos: float = 0.0
    detalhes: list[str] = field(default_factory=list)
    erro: str = ""


class Command(BaseCommand):
    help = "Chama de verdade as rotas do worker de GPU que o PubliBot usa."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rapido",
            action="store_true",
            help="So o texto. Pula imagem e conversao, que levam dezenas de segundos.",
        )
        parser.add_argument(
            "--so",
            choices=["texto", "imagem", "conversao"],
            help="Confere um recurso so.",
        )

    def handle(self, *args, **options):
        quais = ["texto", "imagem", "conversao"]
        if options["so"]:
            quais = [options["so"]]
        elif options["rapido"]:
            quais = ["texto"]

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("worker de GPU — conferencia das rotas"))
        self.stdout.write("")

        saude = self._saude()
        resultados = [saude]

        for qual in quais:
            metodo = getattr(self, f"_conferir_{qual}")
            resultados.append(self._cronometrar(qual, metodo))

        self._resumir(resultados)

        if any(not r.ok for r in resultados):
            raise CommandError("algum recurso do worker nao respondeu como o PubliBot espera.")

    # -- cada recurso --------------------------------------------------------
    def _saude(self) -> Resultado:
        """O `/health/` primeiro, e sem credencial: quando algo abaixo falhar,
        o estado aqui e a primeira coisa que se quer ter na mesma tela."""
        resultado = Resultado("/health/")
        conexao = self._conexao_de_texto(obrigatoria=False)
        if conexao is None:
            resultado.erro = "nenhuma conexao cadastrada; nao sei que endereco consultar."
            return resultado

        url = f"{conexao.base_url.rstrip('/')}/health/"
        self.stdout.write(f"Endereco  {conexao.base_url}")

        try:
            resposta = httpx.get(url, timeout=10.0)
        except httpx.HTTPError as erro:
            resultado.erro = f"{url} inalcancavel: {erro}"
            return resultado

        if not resposta.is_success:
            resultado.erro = f"{url} respondeu HTTP {resposta.status_code}."
            return resultado

        try:
            dados = resposta.json()
        except ValueError:
            resultado.erro = f"{url} respondeu 200, mas nao em JSON."
            return resultado

        self.stdout.write(f"Contrato  {dados.get('contrato_versao', '(nao declarado)')}")
        self.stdout.write("")

        ollama = self._bloco(dados, "ollama", resultado)
        if ollama is not None:
            carregados = ollama.get("carregados") or []
            resultado.detalhes.append(
                f"ollama {'de pe' if ollama.get('de_pe') else 'FORA DO AR'}"
                + (f", carregado: {', '.join(carregados)}" if carregados else ", nada carregado")
            )
            for baixando in ollama.get("baixando") or []:
                resultado.detalhes.append(
                    f"baixando {baixando.get('modelo')} ({baixando.get('porcento')}%)"
                )

        imagem = self._bloco(dados, "imagem", resultado)
        if imagem is not None:
            resultado.detalhes.append(
                f"imagem: {imagem.get('dispositivo')}, "
                + ("pesos no disco" if imagem.get("baixado") else "PESOS AUSENTES")
            )

        conversao = self._bloco(dados, "conversao", resultado)
        if conversao is not None:
            resultado.detalhes.append(
                f"conversao: {conversao.get('dispositivo')}, "
                f"ocr {'ligado' if conversao.get('ocr') else 'desligado'}"
            )

        resultado.ok = not resultado.erro
        return resultado

    def _bloco(self, dados: dict, nome: str, resultado: Resultado) -> dict | None:
        """Um bloco do `/health/`, ou `None` com o motivo anotado.

        O worker nunca devolve 500 aqui: um bloco que falha ao ser coletado
        vira `{"erro": ...}` com HTTP 200. Ler `.get()` direto devolveria
        `None` e a conferencia diria "tudo certo" sobre uma rota doente.
        """
        bloco = dados.get(nome)
        if bloco is None:
            if dados.get("rotas", {}).get(nome) is False:
                resultado.detalhes.append(f"{nome}: rota DESLIGADA no worker")
            return None
        if isinstance(bloco, dict) and "erro" in bloco:
            resultado.erro = f"o bloco {nome!r} do /health/ esta com defeito: {bloco['erro']}"
            return None
        return bloco if isinstance(bloco, dict) else None

    def _conferir_texto(self, resultado: Resultado) -> None:
        from apps.inference.providers.base import get_provider

        conexao = self._conexao_de_texto()
        resposta = get_provider(conexao, timeout=120.0).chat(
            model=conexao.default_model,
            system="Responda em uma palavra.",
            user="Diga: funcionou",
            temperature=0.0,
            max_tokens=16,
        )
        if not (resposta.text or "").strip():
            raise RuntimeError("o modelo respondeu, mas com texto vazio.")

        resultado.detalhes.append(f"modelo {resposta.model}")
        resultado.detalhes.append(f"resposta: {resposta.text.strip()[:60]!r}")

    def _conferir_imagem(self, resultado: Resultado) -> None:
        from apps.inference.providers.base import get_image_provider

        conexao = self._conexao(InferenceConnection.Kind.IMAGE, "imagem")
        # Uma imagem so, e pequena: a conferencia responde "o caminho
        # funciona", nao "a qualidade esta boa". O tamanho quase nao muda o
        # tempo (o custo e mover pesos), mas uma so em vez de tres corta o
        # relogio em tres.
        imagens = get_image_provider(conexao, timeout=300.0).generate(
            model=conexao.default_model,
            prompt="a single red apple on a white table, product photo",
            quantidade=1,
            tamanho="512x288",
        )
        if not imagens or not imagens[0].conteudo:
            raise RuntimeError("o worker respondeu, mas sem imagem dentro.")

        resultado.detalhes.append(f"{len(imagens[0].conteudo) // 1024} KB de PNG")

    def _conferir_conversao(self, resultado: Resultado) -> None:
        from apps.knowledge.extraction import converter_no_worker

        conexao = self._conexao(InferenceConnection.Kind.DOCLING, "conversao")
        extraido = converter_no_worker(
            conexao,
            nome="conferencia.pdf",
            conteudo=PDF_MINIMO,
            sha256=hashlib.sha256(PDF_MINIMO).hexdigest(),
            chave_da_reserva="conferir_worker",
            timeout=600.0,
        )
        if not (extraido.markdown or "").strip():
            raise RuntimeError(
                "o worker converteu e devolveu Markdown VAZIO. Num PDF com "
                "camada de texto isso costuma ser o Docling falhando por "
                "dentro — veja o journal do worker."
            )

        resultado.detalhes.append(f"{len(extraido.markdown)} caracteres de Markdown")
        resultado.detalhes.append(f"primeira linha: {extraido.markdown.splitlines()[0][:60]!r}")

    # -- apoio ---------------------------------------------------------------
    def _conexao_de_texto(self, *, obrigatoria: bool = True):
        return self._conexao(InferenceConnection.Kind.OPENAI_COMPATIBLE, "texto", obrigatoria)

    def _conexao(self, kind: str, rotulo: str, obrigatoria: bool = True):
        """A conexao do BANCO, e nao o `.env`.

        E o banco que os fluxos usam. Conferir o `.env` deixaria passar
        exatamente o caso que ja aconteceu aqui: o arquivo corrigido, a linha
        antiga preservada, e o `configurar_*` avisando que preservou numa
        mensagem que se perde no meio do log.
        """
        conexao = InferenceConnection.objects.filter(
            kind=kind, tenant__isnull=True, is_active=True
        ).first()
        if conexao is None and obrigatoria:
            raise CommandError(
                f"nenhuma conexao ativa de {rotulo} cadastrada.\n"
                f"  manage.py configurar_{'inferencia' if rotulo == 'texto' else rotulo}"
            )
        return conexao

    def _cronometrar(self, nome: str, metodo) -> Resultado:
        resultado = Resultado(nome)
        self.stdout.write(f"{nome} ... ", ending="")
        self.stdout.flush()

        inicio = time.perf_counter()
        try:
            metodo(resultado)
            resultado.ok = True
        except CommandError:
            raise
        except Exception as exc:
            resultado.erro = f"{type(exc).__name__}: {exc}"
        resultado.segundos = time.perf_counter() - inicio

        if resultado.ok:
            self.stdout.write(self.style.SUCCESS(f"ok ({resultado.segundos:.1f}s)"))
        else:
            self.stdout.write(self.style.ERROR("FALHOU"))
        return resultado

    def _resumir(self, resultados: list[Resultado]) -> None:
        self.stdout.write("")
        for resultado in resultados:
            marca = "ok  " if resultado.ok else "FALHOU"
            self.stdout.write(f"  [{marca}] {resultado.nome}")
            for linha in resultado.detalhes:
                self.stdout.write(f"          {linha}")
            if resultado.erro:
                self.stdout.write(self.style.ERROR(f"          {resultado.erro}"))
        self.stdout.write("")
