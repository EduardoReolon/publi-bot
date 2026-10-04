"""Confere os adaptadores contra as APIs de verdade (precisa de internet).

    manage.py conferir_adaptadores

Para cada adaptador pronto: uma busca e o valor mais recente do primeiro
resultado, no Brasil. Os testes da suite usam respostas gravadas; este comando
e o que diz se a instituicao mudou o formato. Nao grava nada.
"""

from django.core.management.base import BaseCommand

# Um termo que certamente existe em cada catalogo.
TERMOS = {"ibge": "populacao", "bcb": "IPCA", "oms": "life expectancy"}


class Command(BaseCommand):
    help = "Confere os adaptadores de dados publicos contra as APIs reais."

    def handle(self, *args, **opcoes):
        from types import SimpleNamespace

        from apps.dados.adaptadores import ADAPTADORES

        falhas = 0
        for chave, adaptador in ADAPTADORES.items():
            if not adaptador.pronto:
                continue
            try:
                achadas = adaptador.procurar(TERMOS.get(chave, "a"), limite=3)
                if not achadas:
                    raise ValueError("a busca nao achou nada")
                primeira = achadas[0]
                serie = SimpleNamespace(
                    codigo=primeira.codigo, periodicidade=primeira.periodicidade
                )
                valores = adaptador.valores(serie, local="Brasil", ultimos=1)
                if not valores:
                    raise ValueError(f"sem valor para {primeira.codigo} no Brasil")
                v = valores[0]
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{adaptador.nome}: ok — {primeira.titulo[:60]} = {v.valor} ({v.periodo})"
                    )
                )
            except Exception as exc:
                falhas += 1
                self.stdout.write(self.style.ERROR(f"{adaptador.nome}: FALHOU — {exc}"))
        if falhas:
            raise SystemExit(1)
