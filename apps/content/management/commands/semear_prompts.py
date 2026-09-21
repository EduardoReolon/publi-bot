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

Essa preservacao tem um custo que so aparece com o tempo: **melhorar uma
semente no codigo nao alcanca ninguem que ja esteja rodando.** O conserto
sai, a suite fica verde, e todo tenant antigo continua com o texto velho. Foi
o que aconteceu com o `image_prompt`: o prompt reescrito para o SDXL nao
chegaria a nenhum tenant existente.

    manage.py semear_prompts --atualizar
    manage.py semear_prompts --todos --atualizar --so image_prompt

`--atualizar` publica a semente como uma VERSAO NOVA, ativa, e desativa a
anterior — sem apagar. A melhoria chega, e o ajuste de quem mexeu na tela
continua no historico, a um clique de voltar. So mexe no que diverge.
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
        parser.add_argument(
            "--atualizar",
            action="store_true",
            help=(
                "Publica a semente como versao nova onde ela divergir da ativa. "
                "A anterior e desativada, nao apagada. NAO entra na implantacao "
                "automatica: sobrescrever o ajuste de um cliente tem de ser uma "
                "decisao de alguem."
            ),
        )
        parser.add_argument(
            "--so",
            metavar="CHAVE",
            action="append",
            default=[],
            help=(
                "Limita a estas chaves (repetivel). Sem isto, `--atualizar` "
                "alcanca todos os prompts que divergirem."
            ),
        )

    def handle(self, *args, **options):
        self.atualizar = options["atualizar"]
        self.chaves = set(options["so"]) or None

        if options["todos"]:
            from apps.accounts.varredura import para_cada_tenant

            total = para_cada_tenant(self._semear_um, "semear_prompts")
            self.stdout.write(self.style.SUCCESS(f"{total} prompt(s) escrito(s) no total."))
            return

        escritos = self._semear_um()
        self.stdout.write(
            self.style.SUCCESS(f"{escritos} prompt(s) escrito(s) em {connection.schema_name!r}.")
            if escritos
            else f"Nada a fazer em {connection.schema_name!r}."
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
        from apps.content.services import atualizar_prompts_padrao, garantir_prompts_padrao

        antes = PromptVersion.objects.count()
        garantir_prompts_padrao()
        criados = PromptVersion.objects.count() - antes

        if criados:
            self.stdout.write(f"  {connection.schema_name}: {criados} prompt(s) criado(s)")

        if not getattr(self, "atualizar", False):
            return criados

        # Depois do `garantir`, e nao antes: num tenant novo os dois rodariam,
        # e atualizar o que acabou de ser criado geraria uma v2 identica a v1.
        atualizados = atualizar_prompts_padrao(getattr(self, "chaves", None))
        if atualizados:
            self.stdout.write(
                f"  {connection.schema_name}: versao nova de {', '.join(sorted(atualizados))}"
            )

        return criados + len(atualizados)
