"""Perfil da Empresa no Google (Google Meu Negocio): posts no perfil, que
aparecem na busca local e no mapa.

API: Business Profile. Os posts ainda sao da v4 (`localPosts`); contas e locais,
das APIs novas (Account Management e Business Information). O acesso a API
precisa ser PEDIDO ao Google (cota zero ate aprovarem): ate la, o destino fica
no "Copiar para postar".

Nao ha comentario em post do Perfil da Empresa: so publica e mede pelo link.
"""

from __future__ import annotations

from urllib.parse import urlencode

from apps.social.redes.base import ErroDaRede, Formato, Publicado, Publicador, Rede
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
