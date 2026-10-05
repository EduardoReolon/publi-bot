"""Meta Ads (Marketing API): quanto se gastou, em que anuncio e com que post.

Le so (permissao `ads_read`), com o mesmo acesso da conta do Instagram. A
permissao e pedida a parte ("Conectar anuncios"), porque a Meta exige uma
revisao propria do app para ela: se nao for liberada, o resto da conta
continua funcionando e o gasto entra pela planilha exportada (anuncios.py).

* `contas()` — as contas de anuncio que a pessoa administra;
* `gastos()` — por anuncio, o total do periodo maximo que a Meta guarda
  (37 meses): gasto, alcance, impressoes, cliques no link e resultados;
* `criativos()` — de cada anuncio, o post do Instagram que ele impulsiona
  (`effective_instagram_media_id`): e o que liga o gasto ao post.
"""

from __future__ import annotations

from apps.social.redes.base import ErroDaRede, NaoConectado
from apps.social.redes.instagram import OAuthInstagram, graph
from apps.social.redes.oauth import acesso_valido

ESCOPO = "ads_read"
CAMPOS_DO_GASTO = (
    "ad_id,ad_name,campaign_name,objective,spend,reach,impressions,inline_link_clicks,"
    "actions,date_start,date_stop"
)
POR_LOTE = 50


class AnunciosMeta:
    def __init__(self, destino, *, http=None):
        import httpx

        self.destino = destino
        self.http = http or httpx.Client(timeout=60.0)

    def _token(self) -> str:
        return acesso_valido(self.destino, OAuthInstagram(http=self.http))["access_token"]

    def _get(self, caminho: str, contexto: str, **params) -> dict:
        resposta = self.http.get(
            f"{graph()}/{caminho}", params={**params, "access_token": self._token()}
        )
        return self._conferir(resposta, contexto)

    @staticmethod
    def _conferir(resposta, contexto: str) -> dict:
        if resposta.status_code >= 400:
            try:
                corpo = resposta.json()
            except ValueError:
                corpo = resposta.text[:500]
            raise ErroDaRede(f"{contexto}: HTTP {resposta.status_code} — {corpo}")
        return resposta.json()

    def _paginas(self, dados: dict, contexto: str, limite: int = 5000) -> list[dict]:
        itens = list(dados.get("data", []))
        proxima = (dados.get("paging") or {}).get("next")
        while proxima and len(itens) < limite:
            dados = self._conferir(self.http.get(proxima), contexto)
            itens += dados.get("data", [])
            proxima = (dados.get("paging") or {}).get("next")
        return itens

    def liberado(self) -> bool:
        """A pessoa deu a permissao de ler anuncios?"""
        if not self.destino.conectado:
            raise NaoConectado(f"{self.destino.nome}: conta nao conectada.")
        dados = self._get("me/permissions", "Meta (permissoes)")
        return any(
            p.get("permission") == ESCOPO and p.get("status") == "granted"
            for p in dados.get("data", [])
        )

    def contas(self) -> list[tuple[str, str]]:
        dados = self._get(
            "me/adaccounts", "Meta (contas de anuncio)", fields="name,account_id,currency"
        )
        return [
            (c["id"], f"{c.get('name') or c['id']} ({c.get('currency', '')})".strip())
            for c in self._paginas(dados, "Meta (contas de anuncio)")
            if c.get("id")
        ]

    def gastos(self, conta: str) -> list[dict]:
        dados = self._get(
            f"{conta}/insights",
            "Meta (gastos)",
            level="ad",
            date_preset="maximum",
            fields=CAMPOS_DO_GASTO,
            limit=200,
        )
        return self._paginas(dados, "Meta (gastos)")

    def criativos(self, ids: list[str]) -> dict[str, dict]:
        """{ad_id: {"media_id", "link", "imagem", "texto"}}"""
        saida = {}
        for i in range(0, len(ids), POR_LOTE):
            lote = ids[i : i + POR_LOTE]
            dados = self._get(
                "",
                "Meta (criativos)",
                ids=",".join(lote),
                fields=(
                    "creative{effective_instagram_media_id,instagram_permalink_url,"
                    "thumbnail_url,body}"
                ),
            )
            for ad_id, item in dados.items():
                criativo = (item or {}).get("creative") or {}
                saida[ad_id] = {
                    "media_id": str(criativo.get("effective_instagram_media_id") or ""),
                    "link": criativo.get("instagram_permalink_url") or "",
                    "imagem": criativo.get("thumbnail_url") or "",
                    "texto": criativo.get("body") or "",
                }
        return saida
