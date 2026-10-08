"""Copia as contas externas (DataForSEO, YouTube, OpenAlex) de um tenant para outro.

As chaves ficam cifradas com a `NODE_KEY_ENCRYPTION_KEY`, a mesma para todos os
tenants do servidor: a copia passa o valor cifrado como esta, sem decifrar e sem
mostrar nada. A tela nunca mostra a chave de novo (o YouTube nem deixa ver de
novo no console do Google), entao este e o caminho para reaproveitar.

    python manage.py copiar_contas_externas --de clinica_a --para clinica_b
    python manage.py copiar_contas_externas --de clinica_a --para clinica_b --sobrescrever
    python manage.py copiar_contas_externas --de clinica_a --mostrar

Sem `--sobrescrever`, so preenche o que estiver vazio no destino. `--mostrar`
imprime as chaves decifradas do tenant de origem no terminal (e nao copia):
so para quando a chave precisar ir para fora do PubliBot.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from apps.accounts.models import Tenant

# (campo, rotulo, cifrado)
CAMPOS = (
    ("dataforseo_login", "DataForSEO: login", False),
    ("dataforseo_senha_ciphertext", "DataForSEO: senha", True),
    ("youtube_chave_ciphertext", "YouTube: chave", True),
    ("openalex_chave_ciphertext", "OpenAlex: chave", True),
    ("email_para_bases_academicas", "E-mail para as bases academicas", False),
    ("searxng_url", "SearXNG", False),
)


def _bytes(valor):
    return valor.tobytes() if isinstance(valor, memoryview) else valor


class Command(BaseCommand):
    help = "Copia DataForSEO, YouTube e OpenAlex de um tenant para outro (sem mostrar as chaves)."

    def add_arguments(self, parser):
        parser.add_argument("--de", required=True, help="schema do tenant de origem")
        parser.add_argument("--para", help="schema do tenant de destino")
        parser.add_argument(
            "--sobrescrever",
            action="store_true",
            help="troca tambem o que o destino ja tem (sem isto, so preenche o vazio)",
        )
        parser.add_argument(
            "--mostrar",
            action="store_true",
            help="imprime as chaves decifradas da origem, em vez de copiar",
        )

    def handle(self, *args, de, para, sobrescrever, mostrar, **opcoes):
        from apps.radar.models import ContasExternas

        for schema in filter(None, (de, para)):
            if not Tenant.objects.filter(schema_name=schema).exists():
                nomes = ", ".join(Tenant.objects.values_list("schema_name", flat=True))
                raise CommandError(f"Tenant '{schema}' nao existe. Os que existem: {nomes}")
        if not mostrar and not para:
            raise CommandError("Diga o destino (--para) ou use --mostrar.")
        if para == de:
            raise CommandError("Origem e destino sao o mesmo tenant.")

        with schema_context(de):
            origem = ContasExternas.carregar()
            valores = {campo: _bytes(getattr(origem, campo)) for campo, _r, _c in CAMPOS}

        if mostrar:
            from apps.inference.security import decifrar

            self.stdout.write(self.style.WARNING(f"Contas externas de '{de}' (nao cole em chat):"))
            for campo, rotulo, cifrado in CAMPOS:
                valor = decifrar(valores[campo]) if cifrado else valores[campo]
                self.stdout.write(f"  {rotulo}: {valor or '(vazio)'}")
            return

        copiados, mantidos = [], []
        with schema_context(para):
            destino = ContasExternas.carregar()
            for campo, rotulo, _cifrado in CAMPOS:
                if not valores[campo]:
                    continue
                if getattr(destino, campo) and not sobrescrever:
                    mantidos.append(rotulo)
                    continue
                setattr(destino, campo, valores[campo])
                copiados.append(rotulo)
            if copiados:
                destino.save()

        for rotulo in copiados:
            self.stdout.write(self.style.SUCCESS(f"  copiado: {rotulo}"))
        for rotulo in mantidos:
            self.stdout.write(f"  mantido (o destino ja tinha; use --sobrescrever): {rotulo}")
        if not copiados and not mantidos:
            self.stdout.write(f"'{de}' nao tem contas externas preenchidas.")
