"""Teste da busca semantica do OpenAlex, lado a lado com a busca por palavras.

So le: nao grava candidato nem documento. Usa a chave e o e-mail do OpenAlex
cadastrados no tenant (Configuracao > Contas externas), por isso roda dentro
dele:

    manage.py tenant_command testar_busca_semantica --schema=ekron
    manage.py tenant_command testar_busca_semantica --schema=ekron --texto "outra consulta"
    manage.py tenant_command testar_busca_semantica --schema=ekron --so-semantica --quantos 50

Para cada consulta mostra o tempo de resposta, os temas dos artigos agrupados
(o `primary_topic` do OpenAlex: area > subarea > topico) e os titulos — prontos
para colar numa LLM e perguntar se foram para o tema certo.

A busca semantica do OpenAlex usa um modelo so de ingles (GTE Large EN): por
isso as consultas vem em ingles e em portugues, para comparar.
"""

from __future__ import annotations

import time
from collections import Counter

import httpx
from django.core.management.base import BaseCommand

from apps.knowledge.academicos import OPENALEX, _parametros_da_conta

CONSULTAS = [
    # A mesma ideia em ingles e em portugues: mostra quanto a lingua pesa.
    "How small service businesses can recover customer satisfaction and loyalty "
    "after a service failure",
    "Como pequenas empresas de servicos recuperam a satisfacao e a fidelidade do "
    "cliente depois de uma falha no atendimento",
    # Termo tecnico x descricao do problema, como o publico escreve.
    "RFM analysis to segment customers and predict churn in small retail businesses",
    "Why customers stop buying after the first purchase and how to bring them back",
    # Tema de preco de servico.
    "Pricing strategies for professional consulting services in small firms",
]
CAMPOS = "id,display_name,publication_year,cited_by_count,language,primary_topic,relevance_score"
FILTRO = "type:article|review,has_abstract:true"


class Command(BaseCommand):
    help = "Compara a busca semantica do OpenAlex com a busca por palavras."

    def add_arguments(self, parser):
        parser.add_argument("--texto", action="append", help="Consulta (repita para varias).")
        parser.add_argument("--quantos", type=int, default=20, help="Ate 50 (padrao 20).")
        parser.add_argument(
            "--so-semantica", action="store_true", help="Nao roda a busca por palavras."
        )
        parser.add_argument(
            "--sem-filtro",
            action="store_true",
            help=f"Sem o filtro padrao ({FILTRO}).",
        )

    def handle(self, *args, texto, quantos, so_semantica, sem_filtro, **opcoes):
        conta = _parametros_da_conta()
        self.stdout.write(
            f"Conta do OpenAlex: {'com chave' if conta.get('api_key') else 'SEM chave'}"
            f"{', com e-mail' if conta.get('mailto') else ''}."
        )
        quantos = max(1, min(quantos, 50))
        modos = [("semantica", "search.semantic")]
        if not so_semantica:
            modos.append(("palavras", "search"))

        for consulta in texto or CONSULTAS:
            self.stdout.write("\n" + "=" * 78 + f"\nCONSULTA: {consulta}\n" + "=" * 78)
            for nome, parametro in modos:
                self._buscar(consulta, nome, parametro, quantos, conta, sem_filtro)
                time.sleep(1.1)  # a busca semantica aceita 1 requisicao por segundo

    def _buscar(self, consulta, nome, parametro, quantos, conta, sem_filtro):
        parametros = {
            parametro: consulta,
            "per_page": quantos,
            "select": CAMPOS,
            **conta,
        }
        if not sem_filtro:
            parametros["filter"] = FILTRO
        inicio = time.monotonic()
        try:
            resposta = httpx.get(OPENALEX, params=parametros, timeout=60)
        except httpx.HTTPError as exc:
            self.stdout.write(f"\n[{nome}] falhou: {exc}")
            return
        ms = (time.monotonic() - inicio) * 1000
        if resposta.status_code != 200:
            self.stdout.write(
                f"\n[{nome}] HTTP {resposta.status_code} em {ms:.0f} ms: {resposta.text[:500]}"
            )
            return
        dados = resposta.json()
        itens = dados.get("results") or []
        total = (dados.get("meta") or {}).get("count")
        self.stdout.write(
            f"\n[{nome}] {len(itens)} artigo(s) em {ms:.0f} ms"
            + (f" (total que casa: {total})" if total is not None else "")
        )

        areas, subareas, topicos = Counter(), Counter(), Counter()
        for item in itens:
            topico = item.get("primary_topic") or {}
            areas[(topico.get("field") or {}).get("display_name", "?")] += 1
            subareas[(topico.get("subfield") or {}).get("display_name", "?")] += 1
            topicos[topico.get("display_name", "?")] += 1
        for rotulo, contagem in (("Areas", areas), ("Subareas", subareas), ("Topicos", topicos)):
            self.stdout.write(f"  {rotulo}:")
            for valor, n in contagem.most_common(8):
                self.stdout.write(f"    {n:>2}  {valor}")

        self.stdout.write("  Titulos:")
        for i, item in enumerate(itens, start=1):
            nota = item.get("relevance_score")
            nota = f"{nota:.2f}" if isinstance(nota, int | float) else "-"
            self.stdout.write(
                f"    {i:>2}. [{nota}] {item.get('display_name', '')} "
                f"({item.get('publication_year') or '?'}, {item.get('cited_by_count') or 0} cit., "
                f"{item.get('language') or '?'})"
            )
