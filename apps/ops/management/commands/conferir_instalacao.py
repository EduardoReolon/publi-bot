"""Confere o que so o servidor de verdade mostra, sem abrir tela nenhuma.

    manage.py conferir_instalacao            # todos os tenants ativos
    manage.py conferir_instalacao --schema=acme

Os testes automaticos cobrem a logica; o que eles nao alcancam e o ambiente:
migracao que nao rodou, beat parado, pasta de midia sem permissao, chave de
API errada, site que nao reconhece a assinatura. Cada linha sai com OK, FALHA
(com o que fazer) ou "--" (nao configurado, e tudo bem).

Custo: nada. DataForSEO pela rota gratuita de dados da conta; YouTube pela
rota de idiomas (1 unidade da cota diaria de 10.000); Search Console so le a
permissao. Nada vai para o livro-caixa.
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


class Command(BaseCommand):
    help = "Confere banco, fila, beat, midia, contas externas e o site de cada tenant."

    def add_arguments(self, parser):
        parser.add_argument("--schema", default="", help="So este tenant.")

    def handle(self, *args, **options):
        self.falhas = 0
        self._titulo("Instalacao")
        self._rodar("migracoes do public", self._migracoes)
        self._rodar("fila (broker)", self._fila)
        self._rodar("worker do Celery", self._worker)
        self._rodar("beat (agendamentos)", self._beat)
        self._rodar("pasta de midia", self._midia)
        self._rodar("conta do Search Console", self._conta_do_console)

        from apps.accounts.varredura import schemas_ativos

        schemas = [options["schema"]] if options["schema"] else schemas_ativos()
        for schema in schemas:
            with schema_context(schema):
                self._titulo(f"Tenant {schema}")
                self._rodar("migracoes", self._migracoes)
                self._rodar("prompts", self._prompts)
                self._rodar("site (rota /health/ assinada)", self._site)
                self._rodar("DataForSEO", self._dataforseo)
                self._rodar("YouTube", self._youtube)
                self._rodar("SearXNG", self._searxng)
                self._rodar("OpenAlex (artigos cientificos)", self._openalex)
                self._rodar("Search Console", self._console)

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
        from apps.integrations.client import SiteClient
        from apps.integrations.models import Site

        site = Site.objects.first()
        if site is None or not site.base_url:
            return None
        resposta = SiteClient(site).health() or {}
        versao = resposta.get("contract_version") or resposta.get("version") or "?"
        recursos = resposta.get("features") or resposta.get("supports") or []
        extra = f", recursos: {', '.join(map(str, recursos))}" if recursos else ""
        return f"{site.base_url} respondeu (contrato {versao}{extra})"

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
        from apps.radar.youtube import API

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
            try:
                motivo = resposta.json()["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError, TypeError):
                motivo = ""
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
