"""O que as fontes do acervo citam de dados: alimenta o catalogo e os pedidos.

Algoritmo, sem modelo: os links dos documentos e dos trechos, agrupados por
instituicao. Uma vez por dia, somando todos os clientes:

* link de instituicao com adaptador que reconhece o codigo -> serie sugerida
  no catalogo (com o numero de fontes que a citam);
* link de lugar de dados sem adaptador -> pedido de adaptador, com exemplos,
  para a curadoria copiar ao desenvolvedor.
"""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlsplit

from django.utils import timezone

PADRAO_URL = re.compile(r"https?://[^\s<>\"')\]]+")
# Cara de lugar de dados, quando a instituicao ainda nao esta no catalogo.
SUFIXOS_DE_DADOS = (".gov.br", ".gov", ".int", "oecd.org", "europa.eu", "worldbank.org")
EXEMPLOS = 5


def links_do_acervo() -> dict[str, set[str]]:
    """{url: {ids dos documentos que a citam}} do tenant atual."""
    from apps.knowledge.models import Document, SuperChunk

    links: dict[str, set[str]] = defaultdict(set)
    for pk, url in Document.objects.exclude(source_url="").values_list("pk", "source_url"):
        links[url.rstrip(".,;")].add(str(pk))
    trechos = SuperChunk.objects.filter(is_active=True).values_list("document_id", "content")
    for documento, texto in trechos.iterator():
        for url in PADRAO_URL.findall(texto or ""):
            links[url.rstrip(".,;")].add(str(documento))
    return links


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def classificar(links: dict[str, set[str]], acumulado: dict) -> None:
    """Soma em `acumulado` (series e pedidos) o que estes links citam."""
    from apps.dados.adaptadores import adaptador_de
    from apps.dados.catalogo import instituicao_do_link

    for url, documentos in links.items():
        instituicao = instituicao_do_link(url)
        if instituicao is not None:
            adaptador = adaptador_de(instituicao)
            if adaptador is None:
                chave = ("pedido", instituicao.dominios[0] if instituicao.dominios else _host(url))
            else:
                codigo = adaptador.codigo_do_link(url)
                if not codigo:
                    continue
                chave = ("serie", instituicao.pk, codigo)
        elif _host(url).endswith(SUFIXOS_DE_DADOS):
            chave = ("pedido", _host(url))
        else:
            continue
        item = acumulado.setdefault(chave, {"documentos": 0, "exemplos": [], "url": url})
        item["documentos"] += len(documentos)
        if url not in item["exemplos"] and len(item["exemplos"]) < EXEMPLOS:
            item["exemplos"].append(url)


def gravar(acumulado: dict) -> dict:
    """Grava no public: series sugeridas e pedidos de adaptador."""
    from apps.dados.models import Instituicao, PedidoDeAdaptador, Serie

    series = pedidos = 0
    for chave, item in acumulado.items():
        if chave[0] == "serie":
            _tipo, instituicao_id, codigo = chave
            serie, nova = Serie.objects.get_or_create(
                instituicao=Instituicao.objects.get(pk=instituicao_id),
                codigo=codigo,
                defaults={
                    "titulo": f"{codigo} (citada no acervo)",
                    "url": item["url"],
                    "origem": Serie.Origem.ACERVO,
                },
            )
            serie.citacoes = item["documentos"]
            serie.save(update_fields=["citacoes"])
            series += nova
        else:
            pedido, novo = PedidoDeAdaptador.objects.get_or_create(dominio=chave[1])
            pedido.citacoes = item["documentos"]
            pedido.exemplos = item["exemplos"]
            pedido.atualizado_em = timezone.now()
            pedido.save(update_fields=["citacoes", "exemplos", "atualizado_em"])
            pedidos += novo
    return {"series": series, "pedidos": pedidos}


def varrer_todos() -> dict:
    """A rodada diaria: todos os tenants, somados, gravados no public."""
    from apps.accounts.varredura import para_cada_tenant

    acumulado: dict = {}

    def um_tenant() -> int:
        classificar(links_do_acervo(), acumulado)
        return 0

    para_cada_tenant(um_tenant, "varrer_dados_do_acervo")
    return gravar(acumulado)


def texto_do_pedido(pedido) -> str:
    """O pedido de adaptador, para colar na conversa com o desenvolvedor."""
    exemplos = "\n".join(f"- {url}" for url in pedido.exemplos) or "- (sem exemplo)"
    return (
        f"Pedido de adaptador de dados: {pedido.dominio}\n\n"
        f"Citado por {pedido.citacoes} fonte(s) do acervo. Links de exemplo:\n{exemplos}\n\n"
        f"Notas da curadoria: {pedido.notas or '-'}\n\n"
        "Explore esses enderecos e escreva o adaptador em apps/dados/adaptadores.py "
        "(procurar, valores, codigo_do_link), com testes sobre respostas gravadas. "
        "Ver docs/DADOS_PUBLICOS.md."
    )
