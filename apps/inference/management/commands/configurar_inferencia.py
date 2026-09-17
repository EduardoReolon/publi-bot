"""Cadastra a conexao de inferencia a partir do `.env`, sem passar pelo admin.

Por que este comando existe: uma `InferenceConnection` e uma LINHA no banco, nao
uma configuracao de arquivo. Isso e deliberado — trocar de modelo ou de endereco
nao deve exigir implantacao (ADR-0012). Mas tem um custo: numa instalacao nova,
NADA funciona ate alguem abrir o admin e preencher um formulario a mao.

O sintoma de esquecer esse passo e ruim. A aplicacao sobe, as telas abrem, a
pauta e criada, o trabalho entra na fila — e so entao `SemModeloConfigurado`
aparece no painel de operacao, longe de onde a causa estava.

Aqui o `.env` e a SEMENTE, nao a fonte da verdade: o comando cria a conexao se
ela nao existir e, por padrao, nao toca no que ja esta la. Assim o deploy roda
todo dia sem desfazer um ajuste feito na tela — e `--atualizar` existe para
quando a intencao for justamente sobrescrever.

    manage.py configurar_inferencia
    manage.py configurar_inferencia --atualizar
    manage.py configurar_inferencia --testar

E idempotente: rodar de novo apenas confirma o que ja existe.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.inference.models import InferenceConnection
from apps.inference.security import guardar_chave


class Command(BaseCommand):
    help = "Cria ou confere a conexao de inferencia descrita no .env."

    def add_arguments(self, parser):
        parser.add_argument(
            "--atualizar",
            action="store_true",
            help=(
                "Sobrescreve uma conexao que ja exista com os valores do .env. "
                "Sem isto, o comando preserva o que estiver no banco."
            ),
        )
        parser.add_argument(
            "--testar",
            action="store_true",
            help="Depois de cadastrar, chama o endpoint para confirmar que responde.",
        )

    def handle(self, *args, **options):
        nome = settings.INFERENCIA_NOME
        base_url = settings.INFERENCIA_BASE_URL

        if not base_url:
            raise CommandError(
                "INFERENCIA_BASE_URL nao esta definida no .env. "
                "Para um Ollama local: INFERENCIA_BASE_URL=http://127.0.0.1:11434"
            )

        modelo = settings.INFERENCIA_MODELO
        if not modelo:
            raise CommandError(
                "INFERENCIA_MODELO nao esta definido no .env. "
                "Use o nome exato do `ollama list`, por exemplo: qwen2.5:7b-instruct"
            )

        existente = InferenceConnection.objects.filter(name=nome, tenant__isnull=True).first()

        if existente and not options["atualizar"]:
            self.stdout.write(
                f"Conexao {nome!r} ja existe e foi preservada "
                f"({existente.base_url}, modelo {existente.default_model!r}).\n"
                f"Para sobrescrever com o .env:  manage.py configurar_inferencia --atualizar"
            )
            if options["testar"]:
                self._testar(existente)
            return

        conexao = existente or InferenceConnection(name=nome)
        conexao.tenant = None
        conexao.kind = InferenceConnection.Kind.OPENAI_COMPATIBLE
        conexao.base_url = base_url
        conexao.default_model = modelo
        conexao.workloads = [InferenceConnection.Workload.TEXT]
        conexao.max_concurrency = settings.INFERENCIA_CONCORRENCIA
        conexao.lease_seconds = settings.INFERENCIA_RESERVA_SEGUNDOS
        conexao.is_active = True

        # Reabre o disjuntor. Sem isto, reconfigurar uma conexao que passou a
        # tarde inteira fora do ar deixaria o circuito fechado por mais 15
        # minutos, e a pessoa concluiria que o comando nao funcionou.
        conexao.consecutive_failures = 0
        conexao.circuit_open_until = None
        conexao.health_status = InferenceConnection.Health.UNKNOWN

        # O Ollama nao exige chave. As demais APIs compativeis exigem, e a
        # ausencia de chave nelas so aparece como 401 dentro de um job.
        chave = settings.INFERENCIA_API_KEY
        if chave:
            guardar_chave(conexao, chave)

        conexao.save()

        verbo = "atualizada" if existente else "criada"
        self.stdout.write(
            self.style.SUCCESS(
                f"Conexao {nome!r} {verbo}: {base_url}, modelo {modelo!r}, "
                f"concorrencia {conexao.max_concurrency}."
            )
        )

        if options["testar"]:
            self._testar(conexao)

    def _testar(self, conexao: InferenceConnection) -> None:
        """Confere que o endereco responde AGORA, e distingue os dois motivos.

        Vale o segundo que custa: o erro mais comum aqui nao e de configuracao e
        sim de rede — o Ollama escutando so em 127.0.0.1 quando o worker fala
        com ele por outro endereco, ou o Tailscale fora do ar. Sem esta
        confirmacao, isso so apareceria dentro de um job, minutos depois.

        A requisicao e feita aqui, e nao pelo `health()` do provedor, porque
        aquele metodo devolve `False` para qualquer falha. Diagnosticar exige
        separar "nao consegui chegar ate voce" de "cheguei e voce respondeu
        outra coisa": a primeira e rede, a segunda e endereco errado, e mandar a
        pessoa para o lado errado custa a tarde dela.
        """
        import httpx

        url = f"{conexao.base_url.rstrip('/')}/v1/models"
        self.stdout.write(f"Testando {url} ...")

        try:
            resposta = httpx.get(url, timeout=10.0)
        except httpx.HTTPError as erro:
            raise CommandError(
                f"nao foi possivel chegar a {url}: {erro}\n"
                f"Confira se o servico esta de pe, se a porta confere e — em "
                f"producao — se o Tailscale esta conectado."
            ) from erro

        if not resposta.is_success:
            raise CommandError(
                f"{url} respondeu HTTP {resposta.status_code}. O endereco esta "
                f"acessivel, mas nao e um endpoint compativel com OpenAI. "
                f"No Ollama, use a raiz do servico (sem /v1) em "
                f"INFERENCIA_BASE_URL."
            )

        self.stdout.write(self.style.SUCCESS(f"Endpoint respondeu HTTP {resposta.status_code}."))
