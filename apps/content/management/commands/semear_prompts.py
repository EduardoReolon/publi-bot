"""Cria os prompts iniciais nos tenants que ainda nao os tem.

Existe por um defeito encontrado em uso: `garantir_prompts_padrao` era chamada
apenas pelos TESTES. O provisionamento criava o schema, rodava as migrations e
marcava o tenant como ativo — e nenhuma linha de prompt era escrita.

O sintoma e tardio e desnorteante. O cadastro funciona, as telas abrem, o
acervo indexa, a pauta e criada. So ao clicar em "gerar artigo" o sistema
revela que nunca teve com o que gerar:

    LookupError: nenhuma versao ativa para o prompt 'consensus_filter'

O provisionamento passou a semear sozinho, mas isso nao alcanca os tenants que
ja existem. Este comando alcanca.

    manage.py tenant_command semear_prompts --schema=acme
    manage.py semear_prompts --todos

Idempotente: cria o que falta e nao toca no que ja esta la — inclusive nas
versoes que alguem ajustou pela tela, que e o motivo de os prompts viverem no
banco (ADR-0012).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Cria os prompts iniciais que faltarem neste tenant (ou em todos)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--todos",
            action="store_true",
            help=(
                "Percorre todos os tenants ativos em vez de so o atual. "
                "E assim que o release.sh chama, para uma implantacao "
                "consertar quem ficou sem prompts."
            ),
        )

    def handle(self, *args, **options):
        if options["todos"]:
            from apps.accounts.varredura import para_cada_tenant

            total = para_cada_tenant(self._semear_um, "semear_prompts")
            self.stdout.write(self.style.SUCCESS(f"{total} prompt(s) criado(s) no total."))
            return

        criados = self._semear_um()
        self.stdout.write(
            self.style.SUCCESS(f"{criados} prompt(s) criado(s) em {connection.schema_name!r}.")
            if criados
            else f"Nada a fazer em {connection.schema_name!r}: os prompts ja existem."
        )

    def _semear_um(self) -> int:
        """Semeia o schema atual e devolve quantos prompts passaram a existir.

        Conta antes e depois em vez de confiar no retorno de
        `garantir_prompts_padrao`, que nao informa nada: sem o numero, o
        comando diria "pronto" tanto para um tenant que estava quebrado quanto
        para um que ja estava certo, e essas duas coisas precisam ser
        distinguiveis na saida de um deploy.
        """
        from apps.content.models import PromptVersion
        from apps.content.services import garantir_prompts_padrao

        antes = PromptVersion.objects.count()
        garantir_prompts_padrao()
        criados = PromptVersion.objects.count() - antes

        if criados:
            self.stdout.write(f"  {connection.schema_name}: {criados} prompt(s) criado(s)")

        return criados
