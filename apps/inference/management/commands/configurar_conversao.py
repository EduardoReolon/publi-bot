"""Cadastra a conexao de conversao de PDF (Docling) a partir do `.env`.

Irmao do `configurar_inferencia`, e pelo mesmo motivo: a conexao e uma LINHA no
banco, para trocar o endereco do worker nao exigir implantacao — mas numa
instalacao nova essa linha nao existe, e sem ela NADA avisa. O sistema nao
quebra: ele silenciosamente cai no extrator local, que devolve a camada de
texto do PDF sem interpretar a estrutura da pagina.

Esse e o ponto. A degradacao e invisivel no resultado: o texto parece correto.
Num PDF de coluna dupla as duas colunas se intercalam e as frases se misturam,
e isso so aparece muito depois, como artigo publicado citando uma fonte cujo
conteudo foi lido errado.

    manage.py configurar_conversao
    manage.py configurar_conversao --atualizar
    manage.py configurar_conversao --testar

Idempotente: cria se faltar, PRESERVA o que ja estiver no banco.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.inference.models import InferenceConnection
from apps.inference.security import guardar_chave


class Command(BaseCommand):
    help = "Cria ou confere a conexao de conversao de PDF descrita no .env."

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
            help="Depois de cadastrar, chama /health/ para confirmar que responde.",
        )
        parser.add_argument(
            "--opcional",
            action="store_true",
            help=(
                "Sem CONVERSAO_BASE_URL, avisa e sai com sucesso em vez de "
                "falhar. E assim que o release.sh chama: uma instalacao sem "
                "worker de conversao e um estado legitimo, nao um erro."
            ),
        )

    def handle(self, *args, **options):
        nome = settings.CONVERSAO_NOME
        base_url = settings.CONVERSAO_BASE_URL

        if not base_url and options["opcional"]:
            self.stdout.write(
                "CONVERSAO_BASE_URL vazia: nenhuma conexao de conversao cadastrada.\n"
                "Os PDFs passarao pelo extrator local, SEM analise de layout "
                "(e em producao serao recusados)."
            )
            return

        if not base_url:
            raise CommandError(
                "CONVERSAO_BASE_URL nao esta definida no .env.\n"
                "Em desenvolvimento, com o worker na propria maquina:\n"
                "    CONVERSAO_BASE_URL=http://127.0.0.1:8100\n"
                "Em producao, o endereco Tailscale da maquina que roda o Docling."
            )

        segredo = settings.CONVERSAO_SEGREDO
        if not segredo:
            raise CommandError(
                "CONVERSAO_SEGREDO nao esta definido no .env.\n"
                "Precisa ser o MESMO valor de WORKER_SHARED_SECRET no .env do "
                "worker — sem ele o worker devolve 401, e o erro nao diz que o "
                "problema e credencial.\n"
                "Gere um com:\n"
                '    python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        existente = InferenceConnection.objects.filter(
            name=nome, tenant__isnull=True, kind=InferenceConnection.Kind.DOCLING
        ).first()

        if existente and not options["atualizar"]:
            self.stdout.write(
                f"Conexao {nome!r} ja existe e foi preservada ({existente.base_url}).\n"
                f"Para sobrescrever com o .env:  manage.py configurar_conversao --atualizar"
            )
            if options["testar"]:
                self._testar(existente)
            return

        conexao = existente or InferenceConnection(name=nome)
        conexao.tenant = None
        conexao.kind = InferenceConnection.Kind.DOCLING
        conexao.base_url = base_url
        # Nao ha "modelo" a escolher: o Docling tem o proprio pipeline, e o
        # dispositivo (cpu/cuda) e decisao do worker, no `.env` dele.
        conexao.default_model = ""
        conexao.workloads = [InferenceConnection.Workload.VISION_PARSE]
        # Uma conversao por vez, e o worker tambem recusa a segunda com 503.
        # Sao dois lugares de proposito: este evita a viagem, aquele protege a
        # maquina de um cliente que nao respeite este.
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
        self.stdout.write(self.style.SUCCESS(f"Conexao de conversao {nome!r} {verbo}: {base_url}."))
        self.stdout.write(
            "A partir de agora os PDFs passam pelo Docling. Documentos ja "
            "convertidos pelo extrator local continuam como estao — use "
            '"Converter de novo" na tela de curadoria para refaze-los.'
        )

        if options["testar"]:
            self._testar(conexao)

    def _testar(self, conexao: InferenceConnection) -> None:
        """Confere que o worker responde AGORA, e diz em que dispositivo roda.

        O dispositivo importa na resposta: trocar `DOCLING_DEVICE` no worker e
        esquecer de reinicia-lo nao produz erro nenhum — so faz a conversao
        continuar lenta, e a conclusao natural e "o Docling e lento mesmo".
        """
        import httpx

        url = f"{conexao.base_url.rstrip('/')}/health/"
        self.stdout.write(f"Testando {url} ...")

        try:
            resposta = httpx.get(url, timeout=10.0)
        except httpx.HTTPError as erro:
            raise CommandError(
                f"nao foi possivel chegar a {url}: {erro}\n"
                f"Confira se o worker de GPU esta de pe, se a porta confere e "
                f"— em producao — se o Tailscale esta conectado.\n"
                f"\n"
                f"Instalado como unit e mesmo assim recusando conexao? O suspeito "
                f"e o BIND_HOST no .env do WORKER: a unit escuta naquele endereco, "
                f"e nao neste. Veja:\n"
                f"    systemctl --user status worker-gpu\n"
                f"    journalctl --user -u worker-gpu -n 30"
            ) from erro

        if not resposta.is_success:
            raise CommandError(
                f"{url} respondeu HTTP {resposta.status_code}. O endereco esta "
                f"acessivel, mas nao e o servico de conversao."
            )

        dados = resposta.json()

        # Aninhado em `conversao`: o worker publica tres rotas no mesmo
        # `/health/`. Ler na raiz devolve `None` sem erro, e a unica pista de
        # que algo esta errado seria um "dispositivo=None" que ninguem le.
        conversao = dados.get("conversao")

        # Mesmo motivo do comando de imagem: um bloco doente vira
        # `{"erro": "..."}` com HTTP 200. Ler `.get("ocr")` ali devolveria
        # `None`, e o comando imprimiria "ocr=None" como se fosse configuracao.
        if isinstance(conversao, dict) and "erro" in conversao:
            raise CommandError(
                f"o worker respondeu, mas a rota de conversao esta com defeito: "
                f"{conversao['erro']}\n"
                f"O processo esta de pe (por isso 200); e a parte de conversao "
                f"que nao consegue reportar estado. Veja o journal do worker."
            )

        if conversao is None:
            if dados.get("rotas", {}).get("conversao") is False:
                raise CommandError(
                    f"{url} respondeu, mas o worker esta com a rota de conversao "
                    f"DESLIGADA. Ponha CONVERSAO_ATIVA=sim no .env do worker e "
                    f"reinicie-o."
                )
            raise CommandError(
                f"{url} respondeu 200, mas sem a secao `conversao`. O endereco "
                f"nao parece ser o worker de GPU."
            )

        dispositivo = conversao.get("dispositivo", "?")
        self.stdout.write(
            self.style.SUCCESS(
                f"Worker respondeu: dispositivo={dispositivo}, "
                f"ocr={conversao.get('ocr')}, ocupado={dados.get('ocupada')}."
            )
        )
        if dispositivo == "cpu":
            self.stdout.write(
                "Em CPU o resultado e o MESMO; muda o tempo. Para usar a placa, "
                "ponha DOCLING_DEVICE=cuda no .env do worker e reinicie-o."
            )
