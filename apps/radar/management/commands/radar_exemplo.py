"""Preenche o radar de um tenant com dados de EXEMPLO, para ver as telas.

Sem a DataForSEO respondendo, o radar fica quase vazio e nao da para conferir
temas, oportunidades, concorrentes e atualizacoes. Este comando cria sinais
com volume, historico e custo por clique inventados, agrupa, calcula as notas
e as oportunidades, e acrescenta um concorrente, sementes e uma atualizacao
sugeridos.

Tudo fica marcado (`"exemplo": true`) e sai com `--apagar`. Os temas de
exemplo nunca sao buscados pelo radar (a expansao os ignora), mas APAGUE antes
de usar o radar de verdade: eles entram na nota dos outros temas.

    python manage.py tenant_command radar_exemplo --schema=acme
    python manage.py tenant_command radar_exemplo --schema=acme --apagar
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


def _serie(base: int, crescimento: float, sazonal: bool = False) -> list[list[int]]:
    """24 meses: 2024-10 a 2026-09, com crescimento anual e sazonalidade."""
    meses = []
    for i in range(24):
        ano, mes = 2024 + (9 + i) // 12, (9 + i) % 12 + 1
        fator = (1 + crescimento) ** (i / 12)
        onda = 1 + (0.5 if sazonal and mes in (11, 12, 1) else 0)
        meses.append([ano, mes, int(base * fator * onda)])
    return meses


SINAIS = [
    # (texto, fonte, volume, cpc, crescimento anual, sazonal)
    ("como calcular o lifetime value do cliente", "paa", 1900, 2.1, 0.1, False),
    ("formula do ltv", "relacionada", 880, 1.4, 0.05, False),
    ("ltv e cac qual a relacao", "paa", 590, 2.8, 0.3, False),
    ("equipe de vendas desmotivada o que fazer", "paa", 1300, 3.8, 0.6, False),
    ("como motivar vendedores que nao batem meta", "relacionada", 720, 3.2, 0.5, False),
    ("clientes somem depois da primeira compra", "paa", 390, 4.2, 0.4, False),
    ("como reativar clientes inativos", "relacionada", 1600, 2.9, 0.2, True),
    ("curva abc de clientes no excel", "relacionada", 720, 1.1, 0.0, False),
    ("Posso medir o LTV sem ter um CRM?", "visitante", None, None, 0, False),
    ("vale a pena fazer programa de fidelidade em loja pequena?", "youtube", None, None, 0, False),
    ("previsibilidade de receita recorrente", "conc_conteudo", 260, 5.1, 0.8, False),
]
CONCORRENTE = "concorrente-exemplo.com.br"


class Command(BaseCommand):
    help = "Cria (ou apaga, com --apagar) dados de exemplo no radar do tenant."

    def add_arguments(self, parser):
        parser.add_argument("--apagar", action="store_true", help="Remove os dados de exemplo.")

    def handle(self, *args, apagar=False, **options):
        from django_tenants.utils import get_public_schema_name

        if connection.schema_name == get_public_schema_name():
            raise CommandError(
                "Rode dentro de um tenant: manage.py tenant_command radar_exemplo --schema=<schema>"
            )
        if apagar:
            self._apagar()
        else:
            self._criar()

    def _criar(self):
        from apps.radar.agrupamento import agrupar, pontuar
        from apps.radar.models import (
            ConcorrenteSugerido,
            GrupoDeDemanda,
            SementeSugerida,
            SinalDeDemanda,
            SugestaoDeAtualizacao,
        )
        from apps.radar.oportunidades import atualizar_oportunidades

        if SinalDeDemanda.objects.filter(extra__exemplo=True).exists():
            raise CommandError("Ja ha dados de exemplo. Apague antes com --apagar.")

        sinais = []
        for texto, fonte, volume, cpc, cresc, sazonal in SINAIS:
            extra = {"exemplo": True}
            if volume is not None:
                extra["metricas"] = {
                    "exemplo": {
                        "volume": volume,
                        "cpc": cpc,
                        "competicao": 55,
                        "meses": _serie(volume, cresc, sazonal),
                    }
                }
            sinais.append(
                SinalDeDemanda.objects.create(texto=texto, fonte=fonte, volume=volume, extra=extra)
            )
        grupos = agrupar(sinais)
        for grupo in GrupoDeDemanda.objects.filter(pk__in=grupos):
            pontuar(grupo)
        oportunidades = atualizar_oportunidades()

        ConcorrenteSugerido.objects.get_or_create(
            dominio=CONCORRENTE,
            defaults={
                "consultas": {"lifetime value": 2, "reativar clientes": 4},
                "exemplos": [
                    {
                        "url": f"https://{CONCORRENTE}/blog/ltv",
                        "titulo": "O que e LTV (exemplo)",
                        "consulta": "lifetime value",
                    }
                ],
            },
        )
        for texto, tipo, origem in (
            ("previsibilidade de receita", "semente", "pagina"),
            ("vendedor nao bate meta", "dor", "modelo"),
        ):
            SementeSugerida.objects.get_or_create(
                chave=f"exemplo {texto}",
                tipo=tipo,
                defaults={"texto": texto, "origem": origem, "evidencia": {"exemplo": True}},
            )
        SugestaoDeAtualizacao.objects.create(
            url="https://exemplo.com.br/como-calcular-ltv",
            titulo="Como calcular o LTV (artigo de exemplo)",
            tipo="quase_la",
            prioridade=40,
            evidencia={
                "exemplo": True,
                "impressoes": 400,
                "consultas": [{"consulta": "ltv formula", "posicao": 11.2, "impressoes": 400}],
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(sinais)} sinais, {len(grupos)} temas, {oportunidades} oportunidade(s), "
                "1 concorrente, 2 sementes e 1 atualizacao sugeridos. Veja em Radar. "
                "Apague com --apagar antes de usar o radar de verdade."
            )
        )

    def _apagar(self):
        from apps.radar.models import (
            ConcorrenteSugerido,
            GrupoDeDemanda,
            SementeSugerida,
            SinalDeDemanda,
            SugestaoDeAtualizacao,
        )

        sinais = SinalDeDemanda.objects.filter(extra__exemplo=True)
        grupos = set(sinais.exclude(grupo__isnull=True).values_list("grupo_id", flat=True))
        total = sinais.count()
        sinais.delete()
        # So o grupo que ficou vazio: um sinal real no mesmo grupo o mantem.
        vazios = GrupoDeDemanda.objects.filter(pk__in=grupos, sinais__isnull=True)
        n_grupos = vazios.count()
        vazios.delete()
        ConcorrenteSugerido.objects.filter(dominio=CONCORRENTE).delete()
        SementeSugerida.objects.filter(evidencia__exemplo=True).delete()
        SugestaoDeAtualizacao.objects.filter(evidencia__exemplo=True).delete()
        self.stdout.write(
            self.style.SUCCESS(f"Apagados {total} sinais e {n_grupos} temas de exemplo.")
        )
