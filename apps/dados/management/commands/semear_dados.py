"""Cria as instituicoes do catalogo de dados que faltarem (schema public).

    manage.py semear_dados              # so cria o que falta
    manage.py semear_dados --atualizar  # tambem atualiza dominios, notas e nichos

Nunca mexe em `confiavel` nem nas series: sao decisao da curadoria.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Semeia as instituicoes do catalogo de dados publicos."

    def add_arguments(self, parser):
        parser.add_argument("--atualizar", action="store_true")

    def handle(self, *args, **opcoes):
        from apps.dados.catalogo_inicial import INSTITUICOES
        from apps.dados.models import Instituicao

        criadas = atualizadas = 0
        for item in INSTITUICOES:
            campos = {k: v for k, v in item.items() if k != "sigla"}
            instituicao, nova = Instituicao.objects.get_or_create(
                sigla=item["sigla"], defaults=campos
            )
            if nova:
                criadas += 1
            elif opcoes["atualizar"]:
                for campo, valor in campos.items():
                    setattr(instituicao, campo, valor)
                instituicao.save()
                atualizadas += 1
        self.stdout.write(f"Instituicoes: {criadas} criada(s), {atualizadas} atualizada(s).")
