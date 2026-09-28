"""Confere o que so o servidor de verdade mostra, sem abrir tela nenhuma.

    manage.py conferir_instalacao            # todos os tenants ativos
    manage.py conferir_instalacao --schema=acme
    manage.py conferir_instalacao --completo # tambem as chamadas de uso real

Os testes automaticos cobrem a logica; o que eles nao alcancam e o ambiente:
migracao que nao rodou, beat parado, pasta de midia sem permissao, chave de
API errada, site que nao reconhece a assinatura. Cada linha sai com OK, FALHA
(com o que fazer) ou "--" (nao configurado, e tudo bem).

Custo: nada. DataForSEO pela rota gratuita de dados da conta; YouTube pela
rota de idiomas (1 unidade da cota diaria de 10.000); Search Console so le a
permissao. Nada vai para o livro-caixa.

Com --completo, cada servico passa tambem pelas MESMAS funcoes que o sistema
usa no dia a dia (busca no Google, volume, videos, comentarios, legenda,
OpenAlex, Unpaywall, Search Analytics, leitura de pagina), com uma consulta
minima, e confere se a resposta chegou no formato que o codigo espera. Isso
custa: cerca de US$ 0,09 da DataForSEO (quase tudo do volume) e 101 unidades
da cota do YouTube, por tenant. Essas chamadas vao para o livro-caixa como
"busca manual".
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context

TIMEOUT = 15.0
# Casos conhecidos para o verificador de links quebrados, com o codigo
# esperado: pagina viva (200), pagina que sumiu (404) e dominio que nao existe
# (0). Passam pela mesma funcao que a tarefa de fundo usa. Esperar 200, e nao
# "nao quebrado", pega o site que recusa o robo (403) em vez de passar calado.
LINKS_DE_REFERENCIA = [
    ("https://pt.wikipedia.org/wiki/Brasil", 200),
    ("https://pt.wikipedia.org/wiki/Pagina_que_nao_existe_publibot_conferencia", 404),
    ("https://dominio-que-nao-existe.invalid/", 0),
]
# Consultas do --completo: genericas de proposito, para que "sem resultado"
# signifique formato ou acesso errado, e nao tema sem busca.
CONSULTA_DE_TESTE = "como fazer bolo de cenoura"
CONSULTA_ACADEMICA = "diabetes mellitus"
PAGINA_DE_TESTE = "https://pt.wikipedia.org/wiki/Brasil"


class Command(BaseCommand):
    help = "Confere banco, fila, beat, midia, contas externas e o site de cada tenant."

    def add_arguments(self, parser):
        parser.add_argument("--schema", default="", help="So este tenant.")
        parser.add_argument(
            "--completo",
            action="store_true",
            help="Tambem as chamadas de uso real (custa ~US$ 0,09 e 101 unidades do YouTube).",
        )

    def handle(self, *args, **options):
        self.falhas = 0
        self._titulo("Instalacao")
        self._rodar("migracoes do public", self._migracoes)
        self._rodar("fila (broker)", self._fila)
        self._rodar("worker do Celery", self._worker)
        self._rodar("beat (agendamentos)", self._beat)
        self._rodar("pasta de midia", self._midia)
        self._rodar("conta do Search Console", self._conta_do_console)
        self._rodar("links quebrados (verificador)", self._links_quebrados)
        if options["completo"]:
            self._rodar("leitura de pagina (fontes)", self._leitura_de_pagina)

        from apps.accounts.varredura import schemas_ativos

        schemas = [options["schema"]] if options["schema"] else schemas_ativos()
        for schema in schemas:
            with schema_context(schema):
                self._titulo(f"Tenant {schema}")
                self._rodar("migracoes", self._migracoes)
                self._rodar("prompts", self._prompts)
                self._rodar("site (assinatura e recusas)", self._site)
                self._rodar("DataForSEO", self._dataforseo)
                self._rodar("YouTube", self._youtube)
                self._rodar("SearXNG", self._searxng)
                self._rodar("OpenAlex (artigos cientificos)", self._openalex)
                self._rodar("Search Console", self._console)
                if options["completo"]:
                    self._rodar("DataForSEO: busca no Google", self._uso_serp)
                    self._rodar("DataForSEO: volume de busca", self._uso_volume)
                    self._rodar("YouTube: videos, comentarios e legenda", self._uso_youtube)
                    self._rodar("SearXNG: busca", self._uso_searxng)
                    self._rodar("OpenAlex e Unpaywall: artigos", self._uso_academicos)
                    self._rodar("Search Console: cliques e posicoes", self._uso_console)

        self.stdout.write("")
        if self.falhas:
            self.stdout.write(self.style.ERROR(f"{self.falhas} falha(s). Veja as dicas acima."))
            raise SystemExit(1)
        self.stdout.write(
            self.style.SUCCESS(
                "Tudo certo. O modelo de texto e o de imagem: manage.py conferir_worker."
            )
        )

    # -- saida ------------------------------------------------------------------

    def _titulo(self, texto: str) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{texto}"))

    def _rodar(self, nome: str, conferir) -> None:
        try:
            resultado = conferir()
        except Exception as exc:  # o comando existe para relatar, e nao para parar
            self.falhas += 1
            self.stdout.write(f"  {self.style.ERROR('FALHA')} {nome}: {exc}")
            return
        if resultado is None:
            self.stdout.write(f"  --    {nome}: nao configurado")
        else:
            self.stdout.write(f"  {self.style.SUCCESS('OK')}    {nome}: {resultado}")

    # -- instalacao -------------------------------------------------------------

    def _migracoes(self) -> str:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        pendentes = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pendentes:
            nomes = ", ".join(f"{m.app_label}.{m.name}" for m, _ in pendentes[:5])
            raise RuntimeError(
                f"{len(pendentes)} pendente(s) ({nomes}). Rode: manage.py migrate_schemas"
            )
        return "em dia"

    def _fila(self) -> str:
        from apps.ops.broker import FILA_PADRAO, mensagens_pendentes

        pendentes = mensagens_pendentes(FILA_PADRAO)
        if pendentes is None:
            raise RuntimeError("o broker nao respondeu. Suba o Redis (ou veja broker_status).")
        return f"{pendentes} mensagem(ns) esperando"

    def _worker(self) -> str:
        from core.celery import app

        respostas = app.control.ping(timeout=3.0) or []
        if not respostas:
            raise RuntimeError("nenhum worker respondeu. Confira: systemctl status celery-publibot")
        return f"{len(respostas)} respondendo"

    def _beat(self) -> str:
        from django_celery_beat.models import PeriodicTask

        with schema_context(get_public_schema_name()):
            ultima = (
                PeriodicTask.objects.filter(enabled=True, last_run_at__isnull=False)
                .order_by("-last_run_at")
                .values_list("last_run_at", flat=True)
                .first()
            )
        if ultima is None:
            raise RuntimeError(
                "nenhum agendamento rodou ainda. Confira: systemctl status celery-beat-publibot"
            )
        atraso = timezone.now() - ultima
        if atraso > timedelta(minutes=30):
            raise RuntimeError(
                f"o ultimo agendamento rodou ha {int(atraso.total_seconds() // 60)} min "
                "(o da fila do radar roda a cada 5). O beat parou?"
            )
        return f"ultimo agendamento ha {int(atraso.total_seconds() // 60)} min"

    def _midia(self) -> str:
        raiz = Path(settings.MEDIA_ROOT)
        raiz.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=raiz, prefix=".conferencia-", delete=True) as f:
            f.write(b"ok")
            f.flush()
        return f"{raiz} aceita gravacao"

    def _conta_do_console(self) -> str | None:
        from apps.radar.search_console import _token_de_acesso, conta_de_servico

        if not getattr(settings, "GSC_CONTA_DE_SERVICO_ARQUIVO", ""):
            return None
        conta = conta_de_servico()
        if conta is None:
            raise RuntimeError(
                "o arquivo de GSC_CONTA_DE_SERVICO_ARQUIVO nao abre ou nao e uma conta de servico."
            )
        _token_de_acesso()
        return f"{conta['client_email']} (token emitido)"

    def _links_quebrados(self) -> str:
        from apps.radar.links_quebrados import codigo_http

        erradas = []
        for url, esperado in LINKS_DE_REFERENCIA:
            obtido = codigo_http(url)
            if obtido != esperado:
                erradas.append(f"{url} deu {obtido!r}, esperado {esperado!r}")
        if erradas:
            raise RuntimeError(
                "; ".join(erradas) + ". 403 e recusa do robo: confira PUBLIBOT_DOMINIO_PUBLICO "
                "(vai no User-Agent como contato). Sem resposta: o servidor sai para a internet?"
            )
        return f"{len(LINKS_DE_REFERENCIA)} casos conhecidos como esperado"

    def _leitura_de_pagina(self) -> str:
        from apps.knowledge.web import PaginaIndisponivel, texto_da_pagina

        try:
            texto = texto_da_pagina(PAGINA_DE_TESTE)
        except PaginaIndisponivel as exc:
            if "403" in str(exc):
                raise RuntimeError(
                    f"{exc}. O site recusou o robo: confira PUBLIBOT_DOMINIO_PUBLICO "
                    "(vai no User-Agent como contato)."
                ) from exc
            raise
        return f"{len(texto)} caracteres de texto principal de {PAGINA_DE_TESTE}"

    # -- por tenant -------------------------------------------------------------

    def _prompts(self) -> str:
        from apps.content.models import PromptTemplate
        from apps.content.prompts_iniciais import PROMPTS_INICIAIS

        existentes = set(PromptTemplate.objects.values_list("key", flat=True))
        faltam = sorted(set(PROMPTS_INICIAIS) - existentes)
        if faltam:
            raise RuntimeError(
                f"faltam {', '.join(faltam)}. Rode: manage.py semear_prompts --todos"
            )
        return f"{len(existentes)} cadastrados"

    def _site(self) -> str | None:
        from apps.integrations.diagnostico import testar_site
        from apps.integrations.models import Site

        site = Site.objects.first()
        if site is None or not site.base_url:
            return None
        etapas = testar_site(site)
        falhas = [f"{e.nome}: {e.detalhe}" for e in etapas if not e.ok]
        if falhas:
            raise RuntimeError("; ".join(falhas))
        return (
            f"{site.base_url}: "
            + "; ".join(f"{e.nome} ({e.detalhe})" for e in etapas[:1])
            + (f"; mais {len(etapas) - 1} etapa(s) ok")
        )

    def _dataforseo(self) -> str | None:
        from apps.radar.models import ContasExternas
        from apps.radar.provedores import (
            DATAFORSEO_BASE,
            _credenciais_dataforseo,
            conferir_http_dataforseo,
        )

        contas = ContasExternas.carregar()
        if not contas.dataforseo_login:
            return None
        login, senha = _credenciais_dataforseo(contas)
        resposta = httpx.get(
            f"{DATAFORSEO_BASE}/appendix/user_data", auth=(login, senha), timeout=TIMEOUT
        )
        conferir_http_dataforseo(resposta)
        tarefa = (resposta.json().get("tasks") or [{}])[0]
        if tarefa.get("status_code") != 20000:
            raise RuntimeError(f"{tarefa.get('status_code')} {tarefa.get('status_message', '')}")
        dados = (tarefa.get("result") or [{}])[0]
        saldo = (dados.get("money") or {}).get("balance")
        return f"conta ativa, saldo US$ {saldo}" if saldo is not None else "conta ativa"

    def _youtube(self) -> str | None:
        from apps.inference.security import decifrar
        from apps.radar.models import ContasExternas
        from apps.radar.youtube import API, motivo_do_erro

        contas = ContasExternas.carregar()
        if not contas.tem_youtube:
            return None
        resposta = httpx.get(
            f"{API}/i18nLanguages",
            params={
                "part": "snippet",
                "hl": "pt",
                "key": decifrar(contas.youtube_chave_ciphertext),
            },
            timeout=TIMEOUT,
        )
        if not resposta.is_success:
            motivo = motivo_do_erro(resposta)
            raise RuntimeError(
                f"HTTP {resposta.status_code} {motivo}. Confira se a chave e da "
                "'YouTube Data API v3' e se a API esta ativada no projeto do Google."
            )
        return "chave aceita"

    def _openalex(self) -> str | None:
        from apps.knowledge.academicos import OPENALEX, _parametros_da_conta
        from apps.radar.models import ConfiguracaoDoRadar, ContasExternas

        if not ConfiguracaoDoRadar.carregar().artigos_cientificos:
            return None
        parametros = {"search": "teste", "per_page": 1, **_parametros_da_conta()}
        resposta = httpx.get(OPENALEX, params=parametros, timeout=TIMEOUT)
        if not resposta.is_success:
            raise RuntimeError(
                f"HTTP {resposta.status_code}. Cadastre a chave gratuita do OpenAlex "
                "em Radar > Configuracao > Contas externas."
            )
        contas = ContasExternas.carregar()
        avisos = []
        if not contas.tem_openalex:
            avisos.append("sem chave (a cota sem chave e minima)")
        if not contas.email_para_bases_academicas:
            avisos.append("sem e-mail (o Unpaywall, que acha o PDF pelo DOI, fica de fora)")
        return "responde" + (f"; {'; '.join(avisos)}" if avisos else "")

    def _searxng(self) -> str | None:
        from apps.radar.models import ContasExternas
        from apps.radar.provedores import url_do_searxng

        url = url_do_searxng(ContasExternas.carregar())
        if not url:
            return None
        resposta = httpx.get(
            f"{url}/search", params={"q": "teste", "format": "json"}, timeout=TIMEOUT
        )
        if not resposta.is_success:
            raise RuntimeError(
                f"HTTP {resposta.status_code}. O formato json precisa estar ligado "
                "no settings.yml do SearXNG (search.formats)."
            )
        return f"{len(resposta.json().get('results') or [])} resultado(s) para 'teste'"

    def _console(self) -> str | None:
        from urllib.parse import quote

        from apps.radar.models import ConfiguracaoDoRadar
        from apps.radar.search_console import API, _token_de_acesso, conta_de_servico

        propriedade = ConfiguracaoDoRadar.carregar().propriedade_search_console
        if not propriedade or conta_de_servico() is None:
            return None
        resposta = httpx.get(
            f"{API}/sites/{quote(propriedade, safe='')}",
            headers={"Authorization": f"Bearer {_token_de_acesso()}"},
            timeout=TIMEOUT,
        )
        if resposta.status_code in {403, 404}:
            raise RuntimeError(
                f"sem acesso a {propriedade}. No Search Console, em Configuracoes > "
                "Usuarios e permissoes, adicione o e-mail da conta de servico."
            )
        resposta.raise_for_status()
        return f"{propriedade}: {resposta.json().get('permissionLevel', 'acesso ok')}"

    # -- uso real (--completo) --------------------------------------------------

    @staticmethod
    def _exigir(condicao, mensagem: str) -> None:
        if not condicao:
            raise RuntimeError(f"a resposta nao veio como o sistema espera: {mensagem}")

    def _uso_serp(self) -> str | None:
        from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas
        from apps.radar.provedores import buscar_dataforseo

        contas = ContasExternas.carregar()
        if not contas.dataforseo_login:
            return None
        resultado = buscar_dataforseo(
            CONSULTA_DE_TESTE,
            config=ConfiguracaoDoRadar.carregar(),
            contas=contas,
            finalidade=ChamadaExterna.Finalidade.MANUAL,
        )
        self._exigir(resultado.resultados, "nenhum resultado organico")
        self._exigir(
            all(item.url.startswith("http") and item.titulo for item in resultado.resultados),
            "resultado organico sem endereco ou titulo",
        )
        return (
            f"{len(resultado.resultados)} organicos, {len(resultado.perguntas)} perguntas, "
            f"{len(resultado.relacionadas)} relacionadas, {len(resultado.academicos)} "
            f"academicos, {len(resultado.noticias)} noticias (US$ {resultado.custo})"
        )

    def _uso_volume(self) -> str | None:
        from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas
        from apps.radar.provedores import metricas_dataforseo, palavra_para_volume

        contas = ContasExternas.carregar()
        if not contas.dataforseo_login:
            return None
        metricas = metricas_dataforseo(
            [CONSULTA_DE_TESTE],
            config=ConfiguracaoDoRadar.carregar(),
            contas=contas,
            finalidade=ChamadaExterna.Finalidade.MANUAL,
        )
        linha = metricas.get(palavra_para_volume(CONSULTA_DE_TESTE))
        self._exigir(linha is not None, f"a palavra {CONSULTA_DE_TESTE!r} nao voltou")
        self._exigir("volume" in linha, "sem o campo de volume")
        return f"{CONSULTA_DE_TESTE!r}: {linha['volume']} buscas/mes"

    def _uso_youtube(self) -> str | None:
        from apps.knowledge.videos import LegendaIndisponivel, buscar_legenda
        from apps.radar.models import ContasExternas
        from apps.radar.youtube import buscar_videos, comentarios

        contas = ContasExternas.carregar()
        if not contas.tem_youtube:
            return None
        videos = buscar_videos(CONSULTA_DE_TESTE, quantos=1, contas=contas)
        self._exigir(videos, "a busca nao trouxe video")
        video = videos[0]
        self._exigir(video["id"] and video["titulo"], "video sem id ou titulo")
        lista = comentarios(video["id"], contas=contas, quantos=5)
        self._exigir(all(c["texto"] for c in lista), "comentario sem texto")
        try:
            legenda = f"{len(buscar_legenda(video['id']))} trechos de legenda"
        except LegendaIndisponivel as exc:
            if "recusou" in str(exc):
                raise
            legenda = f"sem legenda neste video ({exc})"
        return f"video {video['id']}: {len(lista)} comentarios, {legenda}"

    def _uso_searxng(self) -> str | None:
        from apps.radar.models import ChamadaExterna, ConfiguracaoDoRadar, ContasExternas
        from apps.radar.provedores import buscar_searxng, url_do_searxng

        contas = ContasExternas.carregar()
        if not url_do_searxng(contas):
            return None
        resultado = buscar_searxng(
            CONSULTA_DE_TESTE,
            config=ConfiguracaoDoRadar.carregar(),
            contas=contas,
            finalidade=ChamadaExterna.Finalidade.MANUAL,
        )
        self._exigir(resultado.resultados, "nenhum resultado")
        return f"{len(resultado.resultados)} resultados"

    def _uso_academicos(self) -> str | None:
        from apps.knowledge.academicos import buscar_openalex, consultar_unpaywall
        from apps.radar.models import ConfiguracaoDoRadar, ContasExternas

        if not ConfiguracaoDoRadar.carregar().artigos_cientificos:
            return None
        trabalhos = buscar_openalex(CONSULTA_ACADEMICA, quantos=3)
        self._exigir(trabalhos, "o OpenAlex nao trouxe artigo")
        self._exigir(all(t.url for t in trabalhos), "artigo sem DOI nem pagina")
        com_doi = next((t for t in trabalhos if t.doi), None)
        if not ContasExternas.carregar().email_para_bases_academicas:
            pdf = "Unpaywall fora (sem e-mail)"
        elif com_doi is None:
            pdf = "nenhum com DOI para testar o Unpaywall"
        else:
            achado = consultar_unpaywall(com_doi.doi)
            pdf = f"Unpaywall: {'PDF aberto' if achado else 'sem PDF aberto'} para {com_doi.doi}"
        return f"{len(trabalhos)} artigos com titulo e endereco; {pdf}"

    def _uso_console(self) -> str | None:
        from apps.radar.models import ConfiguracaoDoRadar
        from apps.radar.search_console import consultar, conta_de_servico

        propriedade = ConfiguracaoDoRadar.carregar().propriedade_search_console
        if not propriedade or conta_de_servico() is None:
            return None
        hoje = timezone.now().date()
        linhas = consultar(propriedade, hoje - timedelta(days=30), hoje - timedelta(days=3))
        if not linhas:
            return "respondeu, sem linhas no periodo (site novo ou sem impressoes)"
        self._exigir(
            all(len(x.get("keys") or []) == 2 and "position" in x for x in linhas[:50]),
            "linha sem consulta, pagina ou posicao",
        )
        return f"{len(linhas)} linhas (consulta, pagina) nos ultimos 30 dias"
