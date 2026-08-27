"""Confere se o PostgreSQL esta preparado para este projeto.

Existe porque a falha real chega tarde e sem contexto. Um banco novo aceita as
migrations compartilhadas sem reclamar; o erro so aparece quando o primeiro
tenant e provisionado, dentro de uma task do worker, no meio de um traceback:

    django.db.utils.ProgrammingError: tipo "vector" nao existe
    LINE 1: ... "embedding" vector(1024)...

Nada nessa mensagem diz que falta uma EXTENSAO, nem qual, nem como instalar.

Pior: tres causas diferentes produzem exatamente esse texto.

  1. A extensao nao esta instalada.
  2. Esta instalada, mas fora do `search_path` da conexao.
  3. Esta instalada e no caminho, mas o usuario da aplicacao nao tem USAGE no
     schema onde ela vive.

Por isso a verificacao principal aqui nao consulta catalogo: ela CRIA uma
tabela temporaria com uma coluna `vector`. E o mesmo que a migration faz, e so
passa quando as tres condicoes estao satisfeitas ao mesmo tempo.
"""

from __future__ import annotations

import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Verifica extensoes, search_path e o registro do tenant public."

    def handle(self, *args, **options):
        self.problemas: list[str] = []

        connection.set_schema_to_public()
        self._contexto()
        self._extensoes()
        self._prova_do_vector()
        self._template1()
        self._tenant_public()

        if not self.problemas:
            self.stdout.write(self.style.SUCCESS("\nBanco pronto."))
            return

        # Varias verificacoes falham pela MESMA causa: extensao ausente derruba
        # as duas consultas de catalogo e tambem a prova pratica. Repetir o
        # mesmo paragrafo tres vezes esconde os outros problemas.
        self.problemas = list(dict.fromkeys(self.problemas))

        self.stdout.write(self.style.ERROR(f"\n{len(self.problemas)} problema(s) a resolver:\n"))
        for problema in self.problemas:
            self.stdout.write(problema + "\n")
        sys.exit(1)

    # -- relatorio ---------------------------------------------------------
    def _ok(self, texto: str) -> None:
        self.stdout.write(self.style.SUCCESS(f"  ok    {texto}"))

    def _falha(self, texto: str, remedio: str) -> None:
        self.stdout.write(self.style.ERROR(f"  FALHA {texto}"))
        self.problemas.append(remedio)

    # -- verificacoes ------------------------------------------------------
    def _contexto(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user, version()")
            banco, usuario, versao = cursor.fetchone()
            cursor.execute("SHOW search_path")
            caminho = cursor.fetchone()[0]

        conf = settings.DATABASES["default"]
        self.stdout.write(
            f"banco       : {banco} em {conf['HOST']}:{conf['PORT']} (usuario {usuario})"
        )
        self.stdout.write(f"servidor    : {versao.split(' on ')[0]}")
        self.stdout.write(f"search_path : {caminho}\n")

    def _extensoes(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT extname, extversion, n.nspname "
                "FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                "WHERE extname IN ('vector', 'unaccent')"
            )
            encontradas = {linha[0]: linha for linha in cursor.fetchall()}

        for nome in ("vector", "unaccent"):
            linha = encontradas.get(nome)
            if linha is None:
                self._falha(f"extensao '{nome}' nao instalada", _remedio_extensao())
            else:
                self._ok(f"extensao {linha[0]} {linha[1]} no schema '{linha[2]}'")

    def _prova_do_vector(self) -> None:
        """A verificacao que vale: fazer o mesmo que a migration faz."""
        dim = settings.EMBEDDING_DIM
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE TEMP TABLE _probe_vector (v vector({dim}))")
                cursor.execute("DROP TABLE _probe_vector")
        except Exception as exc:
            self._falha(
                f"nao foi possivel criar uma coluna vector({dim}): {str(exc).splitlines()[0]}",
                _remedio_extensao(),
            )
        else:
            self._ok(f"coluna vector({dim}) alcancavel pelo search_path")

    def _template1(self) -> None:
        """O banco de teste do pytest herda do template1.

        `vector` nao e uma extensao "trusted": cria-la exige superusuario, e o
        usuario da aplicacao nao e (nem deveria ser). Como o pytest cria o
        banco de teste em tempo de execucao com o usuario da aplicacao, ele
        nunca conseguiria instalar a extensao sozinho. Preparar o template1
        resolve de uma vez.
        """
        conf = settings.DATABASES["default"]
        try:
            import psycopg

            with (
                psycopg.connect(
                    dbname="template1",
                    user=conf["USER"],
                    password=conf["PASSWORD"],
                    host=conf["HOST"],
                    port=conf["PORT"],
                    connect_timeout=5,
                ) as conexao,
                conexao.cursor() as cursor,
            ):
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                presente = cursor.fetchone() is not None
        except Exception as exc:
            self.stdout.write(
                f"  ?     template1 nao pode ser consultado ({str(exc).splitlines()[0]}). "
                "So afeta a suite de testes."
            )
            return

        if presente:
            self._ok("template1 preparado (o banco de teste do pytest herda a extensao)")
        else:
            self._falha(
                "template1 sem a extensao 'vector' — `pytest` vai falhar",
                _remedio_template1(),
            )

    def _tenant_public(self) -> None:
        from django_tenants.utils import get_public_schema_name

        from apps.accounts.models import Domain, Tenant

        try:
            tenant = Tenant.objects.filter(schema_name=get_public_schema_name()).first()
        except Exception:
            self._falha(
                "as tabelas do schema public nao existem",
                "  Rode:\n      python manage.py migrate_schemas --shared",
            )
            return

        if tenant is None:
            self._falha(
                "tenant 'public' nao registrado — a home devolve 404",
                "  Rode:\n      python manage.py bootstrap_public",
            )
            return

        dominio = Domain.objects.filter(domain=settings.ROOT_DOMAIN).first()
        if dominio is None:
            self._falha(
                f"dominio raiz '{settings.ROOT_DOMAIN}' nao aponta para nenhum tenant",
                "  Rode:\n      python manage.py bootstrap_public",
            )
        else:
            self._ok(
                f"dominio raiz '{settings.ROOT_DOMAIN}' -> tenant '{dominio.tenant.schema_name}'"
            )


def _sql_de_preparo(banco: str, *, com_search_path: bool = True) -> str:
    """SQL para colar DENTRO do psql, uma instrucao por linha.

    Nao e um `psql -c "..."` de proposito. O SQL precisa de aspas duplas — o
    `"$user"` do search_path e obrigatorio — e nao existe forma de aninhar isso
    num `-c` que sobreviva ao bash E ao cmd.exe. Colar no prompt do psql
    funciona igual nos dois.
    """
    usuario = settings.DATABASES["default"]["USER"]
    linhas = [
        "CREATE SCHEMA IF NOT EXISTS extensions;",
        f"GRANT USAGE ON SCHEMA extensions TO {usuario};",
        "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;",
        "CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;",
    ]
    if com_search_path:
        linhas.append(f'ALTER DATABASE {banco} SET search_path TO "$user", public, extensions;')
    return "\n".join("         " + linha for linha in linhas)


def _remedio_extensao() -> str:
    banco = settings.DATABASES["default"]["NAME"]
    sql = _sql_de_preparo(banco)

    if sys.platform == "win32":
        instalacao = (
            "  1. O pgvector nao vem com o PostgreSQL e nao ha binario oficial para\n"
            "     Windows: precisa ser compilado uma vez. Com o 'C++ support' do\n"
            "     Visual Studio instalado, abra o **x64 Native Tools Command Prompt**\n"
            "     COMO ADMINISTRADOR (o prompt comum falha com\n"
            "     `error C2196: case value '4' already used`) e rode:\n"
            "\n"
            '         set "PGROOT=C:\\Program Files\\PostgreSQL\\16"\n'
            "         git clone --branch v0.8.6 https://github.com/pgvector/pgvector.git\n"
            "         cd pgvector\n"
            "         nmake /F Makefile.win\n"
            "         nmake /F Makefile.win install\n"
            "\n"
            "     Ajuste o 16 para a sua versao do PostgreSQL.\n"
            "\n"
            "  2. Depois, como superusuario (usuario `postgres`):\n"
        )
    else:
        instalacao = (
            "  1. Instale o pacote do pgvector para a SUA versao do servidor:\n"
            "         sudo apt install postgresql-16-pgvector\n"
            "     (ou rode ./scripts/setup-db.sh, que faz tudo isto)\n"
            "\n"
            "  2. Depois, como superusuario:\n"
        )

    return (
        "* A extensao `vector` nao esta utilizavel.\n"
        "\n" + instalacao + f"\n         psql -U postgres -d {banco}\n"
        "\n     e cole:\n\n" + sql + "\n"
        "\n"
        "  A extensao vai num schema `extensions` dedicado, e nao no `public`: com\n"
        "  um schema por tenant, uma extensao so no `public` nao fica alcancavel da\n"
        "  forma que as migrations esperam ao criar o segundo tenant. O\n"
        "  `PG_EXTRA_SEARCH_PATHS` do settings fecha o circuito.\n"
        "\n"
        "  O `GRANT USAGE` nao e opcional: sem ele a extensao existe e mesmo assim\n"
        "  o erro e identico ao de extensao ausente."
    )


def _remedio_template1() -> str:
    # Sem o ALTER DATABASE: quem manda no search_path do banco de teste e o
    # settings, nao o template. Aqui so a extensao precisa ser herdada.
    sql = _sql_de_preparo("template1", com_search_path=False)
    return (
        "* O `template1` nao tem a extensao, entao o banco que o pytest cria\n"
        '  tambem nao tera, e a suite falha com o mesmo `type "vector" does not\n'
        "  exist`. Como superusuario:\n"
        "\n         psql -U postgres -d template1\n"
        "\n     e cole:\n\n" + sql + "\n"
    )
