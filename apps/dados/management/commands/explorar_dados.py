"""Procura series no catalogo de uma instituicao e grava como sugeridas.

    manage.py explorar_dados ibge "plano de saude"
    manage.py explorar_dados bcb "IPCA" --limite 10
    manage.py explorar_dados oms "obesity"
    manage.py explorar_dados ibge --nichos     # os termos de TERMOS_POR_NICHO

As series ficam "Sugerida": a curadoria aprova na tela Dados. Nada de codigo
de tabela de memoria: tudo sai da busca na propria instituicao.
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Procura series no catalogo de uma instituicao (pelo adaptador)."

    def add_arguments(self, parser):
        parser.add_argument("adaptador", help="ibge, bcb ou oms")
        parser.add_argument("termo", nargs="?", default="")
        parser.add_argument("--limite", type=int, default=20)
        parser.add_argument("--nichos", action="store_true")

    def handle(self, *args, **opcoes):
        from apps.dados.catalogo import procurar_e_sugerir
        from apps.dados.catalogo_inicial import TERMOS_POR_NICHO
        from apps.dados.models import Instituicao

        instituicao = Instituicao.objects.filter(adaptador=opcoes["adaptador"]).first()
        if instituicao is None:
            raise CommandError(f"Nenhuma instituicao com o adaptador {opcoes['adaptador']!r}.")
        if opcoes["nichos"]:
            termos = TERMOS_POR_NICHO.get(opcoes["adaptador"], [])
        else:
            termos = [opcoes["termo"]]
        if not any(t.strip() for t in termos):
            raise CommandError("Diga o termo, ou use --nichos.")
        for termo in termos:
            series = procurar_e_sugerir(instituicao, termo, limite=opcoes["limite"])
            self.stdout.write(f"{termo!r}: {len(series)} serie(s)")
            for serie in series:
                self.stdout.write(f"  {serie.codigo:<20} {serie.titulo[:90]}")
