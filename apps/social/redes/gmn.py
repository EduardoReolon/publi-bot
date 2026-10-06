"""Perfil da Empresa no Google (Google Meu Negocio): posts no perfil, que
aparecem na busca local e no mapa.

API: Business Profile. Os posts ainda sao da v4 (`localPosts`); contas e locais,
das APIs novas (Account Management e Business Information). O acesso a API
precisa ser PEDIDO ao Google (cota zero ate aprovarem): ate la, o destino fica
no "Copiar para postar".

Nao ha comentario em post do Perfil da Empresa: so publica e mede pelo link.
O passado: os posts (texto e data) e o resultado da conta mes a mes
(visualizacoes, ligacoes, rotas, cliques no site) mais o resumo das avaliacoes.
"""

from __future__ import annotations

from urllib.parse import urlencode

from apps.social.redes.base import ErroDaRede, Formato, Publicado, Publicador, Rede, SemSuporte
from apps.social.redes.oauth import OAuth, acesso_valido

ENDERECO_DE_ACESSO = "https://oauth2.googleapis.com/token"


class OAuthGoogle(OAuth):
    ID_DO_APP = "SOCIAL_GOOGLE_CLIENT_ID"
    SEGREDO_DO_APP = "SOCIAL_GOOGLE_CLIENT_SECRET"
    ESCOPO = "https://www.googleapis.com/auth/business.manage"

    def url_de_autorizacao(self, destino, redirect_uri: str, state: str) -> str:
        cliente, _segredo = self.app()
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": cliente,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": self.ESCOPO,
                # offline + consent: sem isto o Google nao manda o refresh_token.
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )

    def trocar_codigo(self, destino, codigo: str, redirect_uri: str) -> dict:
        cliente, segredo = self.app()
        dados = self._json(
            self.http.post(
                ENDERECO_DE_ACESSO,
                data={
                    "code": codigo,
                    "client_id": cliente,
                    "client_secret": segredo,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            ),
            "Google (token)",
        )
        return {
            "access_token": dados["access_token"],
            "refresh_token": dados.get("refresh_token", ""),
            "expira_em": self.expira(dados.get("expires_in")),
        }

    def renovar(self, destino) -> bool:
        cliente, segredo = self.app()
        atual = destino.ler_credenciais()
        if not atual.get("refresh_token"):
            return False
        dados = self._json(
            self.http.post(
                ENDERECO_DE_ACESSO,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": atual["refresh_token"],
                    "client_id": cliente,
                    "client_secret": segredo,
                },
            ),
            "Google (renovar)",
        )
        self.gravar(
            destino,
            {
                **atual,
                "access_token": dados["access_token"],
                "expira_em": self.expira(dados.get("expires_in")),
            },
        )
        return True

    def contas(self, destino) -> list[tuple[str, str]]:
        token = destino.ler_credenciais()["access_token"]
        cabecalho = {"Authorization": f"Bearer {token}"}
        contas = self._json(
            self.http.get(
                "https://mybusinessaccountmanagement.googleapis.com/v1/accounts", headers=cabecalho
            ),
            "Google (contas)",
        )
        locais = []
        for conta in contas.get("accounts", []):
            dados = self._json(
                self.http.get(
                    f"https://mybusinessbusinessinformation.googleapis.com/v1/{conta['name']}/locations",
                    params={"readMask": "name,title"},
                    headers=cabecalho,
                ),
                "Google (locais)",
            )
            for local in dados.get("locations", []):
                # A v4 dos posts quer accounts/{a}/locations/{l}.
                locais.append(
                    (f"{conta['name']}/{local['name']}", local.get("title", local["name"]))
                )
        return locais


class PublicadorGoogle(Publicador):
    def publicar(self, post, texto: str, imagens) -> Publicado:
        if any(i.video for i in imagens):
            raise SemSuporte(
                "o Perfil do Google nao recebe video por post: use 'copiar para postar'."
            )
        token = acesso_valido(self.destino, OAuthGoogle(http=self.http))["access_token"]
        corpo = {
            "languageCode": "pt-BR",
            "summary": texto[:1500],
            "topicType": "STANDARD",
        }
        link = (post.extras or {}).get("link") or post.artigo_url
        if link:
            corpo["callToAction"] = {"actionType": "LEARN_MORE", "url": link}
        if imagens:
            corpo["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": imagens[0].url}]
        resposta = self._conferir(
            self.http.post(
                f"https://mybusiness.googleapis.com/v4/{self.destino.conta_id}/localPosts",
                json=corpo,
                headers={"Authorization": f"Bearer {token}"},
            ),
            "Google (post)",
        ).json()
        if not resposta.get("name"):
            raise ErroDaRede(f"Google nao devolveu o post: {resposta}")
        return Publicado(id_remoto=resposta["name"], url=resposta.get("searchUrl", ""))

    # -- Passado e resultado ----------------------------------------------------
    # O Google nao mede cada post (a leitura por post foi desligada): mede a
    # CONTA, dia a dia. O PubliBot importa os posts (texto e data) e o resultado
    # da conta mes a mes, e o diagnostico compara meses com e sem post.
    def _get(self, url: str, contexto: str, params=None) -> dict:
        token = acesso_valido(self.destino, OAuthGoogle(http=self.http))["access_token"]
        return self._conferir(
            self.http.get(url, params=params, headers={"Authorization": f"Bearer {token}"}),
            contexto,
        ).json()

    def historico(self, limite: int = 500) -> list[dict]:
        url = f"https://mybusiness.googleapis.com/v4/{self.destino.conta_id}/localPosts"
        saida: list[dict] = []
        pagina = ""
        while len(saida) < limite:
            dados = self._get(
                url,
                "Google (posts antigos)",
                {"pageSize": 100, **({"pageToken": pagina} if pagina else {})},
            )
            for item in dados.get("localPosts", []):
                if item.get("state", "LIVE") not in ("LIVE", "PROCESSING"):
                    continue
                saida.append(
                    {
                        "id_remoto": item.get("name", ""),
                        "legenda": item.get("summary", "") or "",
                        "formato": "IMAGE" if item.get("media") else "TEXT",
                        "link": item.get("searchUrl", ""),
                        "publicado_em": item.get("createTime", ""),
                    }
                )
            pagina = dados.get("nextPageToken", "")
            if not pagina:
                break
        return saida[:limite]

    def desempenho(self) -> dict | None:
        """Cada parte por si: sem a permissao de uma, a outra ainda vem."""
        saida, erro = {}, None
        for nome, ler in (("meses", self._meses), ("avaliacoes", self._avaliacoes)):
            try:
                saida[nome] = ler()
            except ErroDaRede as exc:
                erro = exc
        if not saida and erro is not None:
            raise erro
        return saida

    def _meses(self, dias: int = 540) -> list[dict]:
        """Visualizacoes, ligacoes, rotas, cliques no site e conversas por mes
        (Business Profile Performance API; guarda ate 18 meses)."""
        from datetime import timedelta

        from django.utils import timezone

        fim = timezone.localdate() - timedelta(days=1)
        inicio = fim - timedelta(days=dias)
        params: list[tuple[str, str | int]] = [("dailyMetrics", m) for m in METRICAS_DA_CONTA]
        for nome, dia in (("startDate", inicio), ("endDate", fim)):
            params += [
                (f"dailyRange.{nome}.year", dia.year),
                (f"dailyRange.{nome}.month", dia.month),
                (f"dailyRange.{nome}.day", dia.day),
            ]
        local = "locations/" + self.destino.conta_id.rsplit("locations/", 1)[-1]
        dados = self._get(
            f"https://businessprofileperformance.googleapis.com/v1/{local}"
            ":fetchMultiDailyMetricsTimeSeries",
            "Google (desempenho)",
            params,
        )
        meses: dict[str, dict] = {}
        for grupo in dados.get("multiDailyMetricTimeSeries", []):
            for serie in grupo.get("dailyMetricTimeSeries", []):
                nosso = METRICAS_DA_CONTA.get(serie.get("dailyMetric", ""))
                if not nosso:
                    continue
                for valor in (serie.get("timeSeries") or {}).get("datedValues", []):
                    d = valor.get("date") or {}
                    mes = f"{d.get('year', 0):04d}-{d.get('month', 0):02d}"
                    linha = meses.setdefault(mes, {"mes": mes})
                    linha[nosso] = linha.get(nosso, 0) + int(valor.get("value") or 0)
        return [meses[m] for m in sorted(meses)]

    def _avaliacoes(self, paginas: int = 10) -> dict:
        """Quantas avaliacoes, a nota media e quantas estao sem resposta."""
        url = f"https://mybusiness.googleapis.com/v4/{self.destino.conta_id}/reviews"
        notas = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
        lidas: list[dict] = []
        pagina, dados = "", {}
        for _ in range(paginas):
            dados_da_pagina = self._get(
                url,
                "Google (avaliacoes)",
                {"pageSize": 50, **({"pageToken": pagina} if pagina else {})},
            )
            dados = dados or dados_da_pagina
            lidas += dados_da_pagina.get("reviews", [])
            pagina = dados_da_pagina.get("nextPageToken", "")
            if not pagina:
                break
        sem_resposta = [r for r in lidas if not r.get("reviewReply")]
        return {
            "total": dados.get("totalReviewCount", len(lidas)),
            "media": dados.get("averageRating"),
            "lidas": len(lidas),
            "sem_resposta": len(sem_resposta),
            "baixas_sem_resposta": sum(
                1 for r in sem_resposta if notas.get(r.get("starRating", ""), 5) <= 3
            ),
        }


# Metrica diaria do Google -> nome no PubliBot (as 4 de impressao somam).
METRICAS_DA_CONTA = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "visualizacoes",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "visualizacoes",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS": "visualizacoes",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "visualizacoes",
    "CALL_CLICKS": "ligacoes",
    "BUSINESS_DIRECTION_REQUESTS": "rotas",
    "WEBSITE_CLICKS": "site",
    "BUSINESS_CONVERSATIONS": "conversas",
}


REDE = Rede(
    codigo="gmn",
    nome="Perfil da Empresa no Google",
    formato=Formato(max_caracteres=1500, max_hashtags=0, link="botao", imagem="capa", dobra=100),
    estilo=(
        "Quem ve e alguem procurando o servico perto de casa, no Google ou no mapa, "
        "prestes a decidir. Texto curto e pratico (400 a 900 caracteres): o que a pessoa "
        "ganha sabendo isso, com o local quando fizer sentido ('em Curitiba'), e a "
        "chamada para o botao 'Saiba mais'. Sem hashtag, sem emoji em excesso, sem link "
        "no texto (o botao leva ao artigo). Sem telefone no texto (o Google recusa)."
    ),
    publico_padrao="Pessoas da regiao procurando o servico agora.",
    tom_padrao="claro, util, local",
    autores=(("local", "Local (endereco) da empresa"),),
    preparo=(
        "Pedir acesso a Business Profile API no formulario do Google (pode levar semanas; "
        "a cota comeca em zero). Ate aprovar, use 'Copiar para postar'.",
        "No Google Cloud do mesmo projeto: ativar 'My Business Account Management API', "
        "'My Business Business Information API' e 'Google My Business API'.",
        "Criar credencial OAuth (Aplicativo da Web) com o endereco de retorno que a tela de "
        "conexao mostra.",
        "Copiar ID e chave secreta para SOCIAL_GOOGLE_CLIENT_ID e SOCIAL_GOOGLE_CLIENT_SECRET.",
    ),
    publicador=PublicadorGoogle,
    oauth=OAuthGoogle,
)
