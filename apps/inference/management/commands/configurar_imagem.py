"""Cadastra a conexao de geracao de imagem a partir do `.env`.

Terceiro irmao de `configurar_inferencia` e `configurar_conversao`, e existe
pelo mesmo motivo: a conexao e uma LINHA no banco (ADR-0012), e numa
instalacao nova essa linha nao existe. Sem ela o artigo sai sem capa e a
mensagem aparece tarde, na tela de revisao:

    nenhuma conexao de geracao de imagem disponivel.

Uma diferenca importante em relacao aos irmaos: **nao ter esta conexao e um
estado legitimo.** O texto e o produto; a ilustracao nao. Por isso o passo de
capa nao derruba a geracao, e por isso este comando aceita `--opcional`.

    manage.py configurar_imagem
    manage.py configurar_imagem --atualizar
    manage.py configurar_imagem --testar

Idempotente: cria se faltar, PRESERVA o que ja estiver no banco.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.inference.models import InferenceConnection
from apps.inference.security import guardar_chave


class Command(BaseCommand):
    help = "Cria ou confere a conexao de geracao de imagem descrita no .env."

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
            help="Depois de cadastrar, chama /health/ e diz em que dispositivo o modelo roda.",
        )
        parser.add_argument(
            "--opcional",
            action="store_true",
            help=(
                "Sem IMAGEM_BASE_URL, avisa e sai com sucesso em vez de falhar. "
                "E assim que o release.sh chama."
            ),
        )

    def handle(self, *args, **options):
        nome = settings.IMAGEM_NOME
        base_url = settings.IMAGEM_BASE_URL

        if not base_url and options["opcional"]:
            self.stdout.write(
                "IMAGEM_BASE_URL vazia: nenhuma conexao de imagem cadastrada.\n"
                "Os artigos sairao sem capa, e o texto nao e afetado."
            )
            return

        if not base_url:
            raise CommandError(
                "IMAGEM_BASE_URL nao esta definida no .env.\n"
                "Com o worker de imagem na propria maquina:\n"
                "    IMAGEM_BASE_URL=http://127.0.0.1:8101\n"
                "Em producao, o endereco Tailscale da maquina que tem a placa.\n"
                "O endereco e a RAIZ do servico, sem /v1 — o cliente acrescenta "
                "/v1/images/generations sozinho.\n"
                "Atencao: o Ollama nao serve aqui. Ele nao gera imagem."
            )

        segredo = settings.IMAGEM_SEGREDO
        if not segredo:
            raise CommandError(
                "IMAGEM_SEGREDO nao esta definido no .env.\n"
                "Para o worker proprio, e o MESMO valor de WORKER_SHARED_SECRET "
                "no .env dele; para um provedor pago, a chave da conta. Sem ele "
                "a resposta e 401, e o erro nao diz que o problema e credencial."
            )

        modelo = settings.IMAGEM_MODELO
        if not modelo:
            raise CommandError(
                "IMAGEM_MODELO nao esta definido no .env. Sem modelo padrao a "
                "conexao existe e recusa toda geracao."
            )

        existente = InferenceConnection.objects.filter(
            name=nome, tenant__isnull=True, kind=InferenceConnection.Kind.IMAGE
        ).first()

        if existente and not options["atualizar"]:
            self.stdout.write(
                f"Conexao {nome!r} ja existe e foi preservada "
                f"({existente.base_url}, modelo {existente.default_model!r}).\n"
                f"Para sobrescrever com o .env:  manage.py configurar_imagem --atualizar"
            )
            if options["testar"]:
                self._testar(existente)
            return

        conexao = existente or InferenceConnection(name=nome)
        conexao.tenant = None
        conexao.kind = InferenceConnection.Kind.IMAGE
        conexao.base_url = base_url
        conexao.default_model = modelo
        conexao.workloads = [InferenceConnection.Workload.IMAGE]
        # Uma geracao por vez, e o worker tambem recusa a segunda com 503. Sao
        # dois lugares de proposito: este evita a viagem, aquele protege a
        # placa de um cliente que nao respeite este.
        conexao.max_concurrency = 1
        conexao.lease_seconds = settings.INFERENCIA_RESERVA_SEGUNDOS
        conexao.is_active = True

        # Reabre o disjuntor: reconfigurar uma conexao que passou a tarde fora
        # do ar nao pode deixar o circuito fechado por mais 15 minutos.
        conexao.consecutive_failures = 0
        conexao.circuit_open_until = None
        conexao.health_status = InferenceConnection.Health.UNKNOWN

        guardar_chave(conexao, segredo)
        conexao.save()

        verbo = "atualizada" if existente else "criada"
        self.stdout.write(
            self.style.SUCCESS(f"Conexao de imagem {nome!r} {verbo}: {base_url} ({modelo}).")
        )
        self._avisar_sobre_a_placa(conexao)

        if options["testar"]:
            self._testar(conexao)

    def _avisar_sobre_a_placa(self, conexao: InferenceConnection) -> None:
        """Diz com quem esta conexao vai dividir a maquina.

        A reserva conta vagas por MAQUINA (`leases.vizinhas_de_hardware`), e
        isso muda o comportamento do sistema inteiro: a partir de agora a
        geracao de texto e a de imagem passam a se revezar. E o que se quer
        numa placa so — mas e melhor dizer, porque o efeito visivel e "ficou
        mais lento para gerar artigo".
        """
        vizinhas = [
            outra
            for outra in InferenceConnection.objects.filter(is_active=True).exclude(pk=conexao.pk)
            if outra.maquina and outra.maquina == conexao.maquina
        ]
        if not vizinhas:
            return

        nomes = ", ".join(sorted(f"{o.name!r}" for o in vizinhas))
        self.stdout.write(
            f"Esta conexao divide a maquina {conexao.maquina} com: {nomes}.\n"
            f"A reserva conta vagas por maquina, entao elas passam a se revezar "
            f"em vez de disputar a placa — de proposito. O efeito visivel e um "
            f"trabalho esperar o outro, e nao os dois caindo para CPU."
        )

    def _testar(self, conexao: InferenceConnection) -> None:
        """Confere que o servico responde AGORA, e em que dispositivo.

        O dispositivo e o que importa na resposta. Um worker que caiu para CPU
        — porque a placa nao aparece, ou porque a VRAM faltou no ultimo pedido
        — continua funcionando e entregando imagem. Ele so leva minutos em vez
        de segundos, e a conclusao natural seria "gerar imagem e lento mesmo".
        """
        import httpx

        url = f"{conexao.base_url.rstrip('/')}/health/"
        self.stdout.write(f"Testando {url} ...")

        try:
            resposta = httpx.get(url, timeout=10.0)
        except httpx.HTTPError as erro:
            raise CommandError(
                f"nao foi possivel chegar a {url}: {erro}\n"
                f"Confira se o servico esta de pe (uvicorn imagem_api:app), se a "
                f"porta confere e — em producao — se o Tailscale esta conectado.\n"
                f"\n"
                f"Instalado como unit e mesmo assim recusando conexao? O suspeito "
                f"e o BIND_HOST no .env do WORKER: a unit escuta naquele endereco, "
                f"e nao neste. Veja:\n"
                f"    systemctl --user status imagem-api\n"
                f"    journalctl --user -u imagem-api -n 30"
            ) from erro

        if not resposta.is_success:
            raise CommandError(
                f"{url} respondeu HTTP {resposta.status_code}. O endereco esta "
                f"acessivel, mas nao e o servico de imagem."
            )

        try:
            dados = resposta.json()
        except ValueError:
            # Um provedor pago nao tem `/health/`; o 200 pode ser uma pagina
            # HTML qualquer. Nao e erro — so nao ha nada a relatar.
            self.stdout.write(self.style.SUCCESS(f"{url} respondeu 200."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Worker respondeu: modelo={dados.get('model')}, "
                f"dispositivo={dados.get('device')}, "
                f"ultimo={dados.get('ultimo_dispositivo')}, "
                f"carregado={dados.get('carregado')}, ocupado={dados.get('busy')}."
            )
        )

        if dados.get("baixado") is False:
            self.stdout.write(
                self.style.WARNING(
                    "Os pesos do modelo ainda NAO estao no disco do worker. A "
                    "primeira geracao vai baixa-los (alguns GB) dentro da "
                    "requisicao, e a tela vai parecer travada ate esgotar o "
                    "tempo.\n"
                    "Na maquina do worker, antes de usar:\n"
                    "    ./venv/bin/python baixar_modelo.py"
                )
            )

        if dados.get("device") == "cpu":
            self.stdout.write(
                "Configurado para CPU: uma imagem leva MINUTOS. Para usar a "
                "placa, ponha IMAGEM_DEVICE=cuda no .env do worker e reinicie-o."
            )
        elif dados.get("ultimo_dispositivo") == "cpu":
            self.stdout.write(
                self.style.WARNING(
                    "A ultima geracao caiu para CPU apesar de CUDA estar pedido: "
                    "faltou VRAM. Costuma ser outro modelo ocupando a placa. "
                    "Reduza IMAGEM_OCIOSO_SEGUNDOS, ou o tamanho da imagem."
                )
            )

        endereco = urlparse(conexao.base_url)
        if endereco.hostname in {"0.0.0.0", "::"}:  # noqa: S104  (conferencia, nao bind)
            self.stdout.write(
                self.style.WARNING("base_url em 0.0.0.0 nao e um endereco para se chamar.")
            )
