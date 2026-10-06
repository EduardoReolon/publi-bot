"""LinkedIn: perfil pessoal e pagina da empresa.

API: Posts API (`/rest/posts`, versionada pelo cabecalho LinkedIn-Version),
imagens por `/rest/images?action=initializeUpload`, comentarios por
`/rest/socialActions/{urn}/comments`.

* Perfil pessoal: produto "Share on LinkedIn" (escopo w_member_social),
  liberacao simples. A API NAO deixa ler os comentarios do perfil pessoal
  (escopo restrito): ai so publica e comenta o link.
* Pagina: "Community Management API" (w_organization_social,
  r_organization_social), com revisao do LinkedIn, e a pessoa administra a
  pagina.

O acesso dura ~60 dias e o app comum nao recebe renovacao: o painel avisa e
pede para reconectar.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from django.conf import settings

from apps.social.redes.base import (
    ComentarioLido,
    ErroDaRede,
    Formato,
    Publicado,
    Publicador,
    Rede,
    SemSuporte,
)
from apps.social.redes.oauth import OAuth, acesso_valido

API = "https://api.linkedin.com"


def versao() -> str:
    return getattr(settings, "SOCIAL_LINKEDIN_VERSAO", "") or "202509"


# O texto do post usa o formato "little text" do LinkedIn: estes caracteres
# precisam de barra antes, senao o post e recusado ou sai cortado.
_RESERVADOS = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_HASHTAG = re.compile(r"(?<![\w\\])#(\w+)")


def texto_do_linkedin(texto: str) -> str:
    """Escapa o texto e transforma #palavra em hashtag de verdade."""
    hashtags: list[str] = []

    def guardar(achado: re.Match) -> str:
        hashtags.append(achado.group(1))
        return f"\x00{len(hashtags) - 1}\x00"

    texto = _HASHTAG.sub(guardar, texto)
    texto = _RESERVADOS.sub(r"\\\1", texto)
    return re.sub(
        r"\x00(\d+)\x00", lambda m: "{hashtag|\\#|" + hashtags[int(m.group(1))] + "}", texto
    )


class OAuthLinkedIn(OAuth):
    ID_DO_APP = "SOCIAL_LINKEDIN_CLIENT_ID"
    SEGREDO_DO_APP = "SOCIAL_LINKEDIN_CLIENT_SECRET"
    AUTORIZAR = "https://www.linkedin.com/oauth/v2/authorization"
    ENDERECO_DE_ACESSO = "https://www.linkedin.com/oauth/v2/accessToken"

    def _app_de(self, destino) -> tuple[str, str]:
        """O LinkedIn exige que a Community Management API (paginas) fique
        SOZINHA num app: o perfil pessoal (login + compartilhar) usa outro.
        Pagina usa SOCIAL_LINKEDIN_PAGINA_CLIENT_ID/SECRET, se definidos."""
        if destino.autor == "organizacao":
            cliente = getattr(settings, "SOCIAL_LINKEDIN_PAGINA_CLIENT_ID", "") or ""
            segredo = getattr(settings, "SOCIAL_LINKEDIN_PAGINA_CLIENT_SECRET", "") or ""
            if cliente and segredo:
                return cliente, segredo
        return self.app()

    def escopos(self, destino) -> list[str]:
        if destino.autor == "organizacao":
            return ["w_organization_social", "r_organization_social", "rw_organization_admin"]
        return ["openid", "profile", "w_member_social"]

    def url_de_autorizacao(self, destino, redirect_uri: str, state: str) -> str:
        cliente, _segredo = self._app_de(destino)
        return f"{self.AUTORIZAR}?" + urlencode(
            {
                "response_type": "code",
                "client_id": cliente,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": " ".join(self.escopos(destino)),
            }
        )

    def trocar_codigo(self, destino, codigo: str, redirect_uri: str) -> dict:
        cliente, segredo = self._app_de(destino)
        dados = self._json(
            self.http.post(
                self.ENDERECO_DE_ACESSO,
                data={
                    "grant_type": "authorization_code",
                    "code": codigo,
                    "redirect_uri": redirect_uri,
                    "client_id": cliente,
                    "client_secret": segredo,
                },
            ),
            "LinkedIn (token)",
        )
        return {
            "access_token": dados["access_token"],
            "expira_em": self.expira(dados.get("expires_in")),
        }

    def _cabecalhos(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": versao(),
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def contas(self, destino) -> list[tuple[str, str]]:
        token = destino.ler_credenciais()["access_token"]
        if destino.autor != "organizacao":
            eu = self._json(
                self.http.get(f"{API}/v2/userinfo", headers={"Authorization": f"Bearer {token}"}),
                "LinkedIn (perfil)",
            )
            return [(f"urn:li:person:{eu['sub']}", eu.get("name") or eu["sub"])]
        acls = self._json(
            self.http.get(
                f"{API}/rest/organizationAcls",
                params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"},
                headers=self._cabecalhos(token),
            ),
            "LinkedIn (paginas)",
        )
        contas = []
        for item in acls.get("elements", []):
            urn = item.get("organization", "")
            numero = urn.rsplit(":", 1)[-1]
            nome = urn
            resposta = self.http.get(
                f"{API}/rest/organizations/{numero}", headers=self._cabecalhos(token)
            )
            if resposta.status_code < 400:
                nome = resposta.json().get("localizedName") or urn
            contas.append((urn, nome))
        return contas


class PublicadorLinkedIn(Publicador):
    def _cabecalhos(self) -> dict:
        token = acesso_valido(self.destino, None)["access_token"]
        return {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": versao(),
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def _enviar_imagem(self, imagem, cabecalhos: dict) -> str:
        from django.core.files.storage import default_storage

        inicio = self._conferir(
            self.http.post(
                f"{API}/rest/images",
                params={"action": "initializeUpload"},
                json={"initializeUploadRequest": {"owner": self.destino.conta_id}},
                headers=cabecalhos,
            ),
            "LinkedIn (imagem)",
        ).json()["value"]
        with default_storage.open(imagem.caminho, "rb") as arquivo:
            self._conferir(
                self.http.put(
                    inicio["uploadUrl"],
                    content=arquivo.read(),
                    headers={"Authorization": cabecalhos["Authorization"]},
                ),
                "LinkedIn (envio da imagem)",
            )
        return inicio["image"]

    def publicar(self, post, texto: str, imagens) -> Publicado:
        if any(i.video for i in imagens):
            raise SemSuporte("video no LinkedIn sai pelo 'copiar para postar'.")
        cabecalhos = self._cabecalhos()
        corpo = {
            "author": self.destino.conta_id,
            "commentary": texto_do_linkedin(texto),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if imagens:
            corpo["content"] = {
                "media": {
                    "id": self._enviar_imagem(imagens[0], cabecalhos),
                    "title": post.artigo_titulo[:200],
                }
            }
        resposta = self._conferir(
            self.http.post(f"{API}/rest/posts", json=corpo, headers=cabecalhos),
            "LinkedIn (post)",
        )
        urn = resposta.headers.get("x-restli-id", "")
        return Publicado(id_remoto=urn, url=f"https://www.linkedin.com/feed/update/{urn}/")

    def _comentarios(self, post) -> str:
        return f"{API}/rest/socialActions/{quote(post.id_remoto, safe='')}/comments"

    def comentar(self, post, texto: str) -> str:
        resposta = self._conferir(
            self.http.post(
                self._comentarios(post),
                json={
                    "actor": self.destino.conta_id,
                    "object": post.id_remoto,
                    "message": {"text": texto},
                },
                headers=self._cabecalhos(),
            ),
            "LinkedIn (comentario)",
        )
        return resposta.headers.get("x-restli-id") or (resposta.json() or {}).get("id", "")

    def ler_comentarios(self, post) -> list[ComentarioLido]:
        if self.destino.autor != "organizacao":
            raise SemSuporte("o LinkedIn nao libera a leitura de comentarios do perfil pessoal.")
        dados = self._conferir(
            self.http.get(self._comentarios(post), headers=self._cabecalhos()),
            "LinkedIn (ler comentarios)",
        ).json()
        saida = []
        for item in dados.get("elements", []):
            from datetime import UTC, datetime

            criado = (item.get("created") or {}).get("time")
            saida.append(
                ComentarioLido(
                    id_remoto=item.get("$URN") or item.get("commentUrn") or str(item.get("id")),
                    texto=(item.get("message") or {}).get("text", ""),
                    autor="",
                    escrito_em=datetime.fromtimestamp(criado / 1000, tz=UTC).isoformat()
                    if criado
                    else "",
                    do_dono=item.get("actor") == self.destino.conta_id,
                )
            )
        return saida

    def responder(self, post, id_do_comentario: str, texto: str) -> str:
        resposta = self._conferir(
            self.http.post(
                self._comentarios(post),
                json={
                    "actor": self.destino.conta_id,
                    "object": post.id_remoto,
                    "parentComment": id_do_comentario,
                    "message": {"text": texto},
                },
                headers=self._cabecalhos(),
            ),
            "LinkedIn (resposta)",
        )
        return resposta.headers.get("x-restli-id", "")

    def seguidores(self) -> int | None:
        """So da pagina: o LinkedIn nao informa seguidores de perfil pessoal pela API."""
        if self.destino.autor != "organizacao":
            return None
        dados = self._conferir(
            self.http.get(
                f"{API}/rest/networkSizes/{quote(self.destino.conta_id, safe='')}",
                params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"},
                headers=self._cabecalhos(),
            ),
            "LinkedIn (seguidores)",
        ).json()
        return dados.get("firstDegreeSize")

    # -- Passado e resultado (so da pagina: o perfil pessoal nao tem leitura) --
    @classmethod
    def entrega_historico(cls, destino) -> bool:
        return destino.autor == "organizacao"

    def historico(self, limite: int = 500) -> list[dict]:
        """Os posts da pagina (Posts API, finder por autor), mais novos primeiro."""
        if self.destino.autor != "organizacao":
            raise SemSuporte("o LinkedIn nao entrega os posts do perfil pessoal pela API.")
        autor = quote(self.destino.conta_id, safe="")
        saida: list[dict] = []
        inicio, lote = 0, 50
        while len(saida) < limite:
            dados = self._conferir(
                self.http.get(
                    f"{API}/rest/posts?q=author&author={autor}&count={lote}&start={inicio}"
                    "&sortBy=CREATED",
                    headers=self._cabecalhos(),
                ),
                "LinkedIn (posts antigos)",
            ).json()
            elementos = dados.get("elements", [])
            for item in elementos:
                if item.get("lifecycleState", "PUBLISHED") != "PUBLISHED":
                    continue
                urn = item.get("id", "")
                quando = item.get("publishedAt") or item.get("createdAt")
                saida.append(
                    {
                        "id_remoto": urn,
                        "legenda": _sem_escape(item.get("commentary", "")),
                        "formato": _formato(item.get("content") or {}),
                        "link": f"https://www.linkedin.com/feed/update/{urn}/",
                        "publicado_em": datetime.fromtimestamp(quando / 1000, tz=UTC).isoformat()
                        if quando
                        else "",
                    }
                )
            if len(elementos) < lote:
                break
            inicio += lote
        return saida[:limite]

    def _estatisticas(self, post) -> dict:
        """Alcance, impressoes, cliques e compartilhamentos do post da pagina."""
        tipo = "ugcPosts" if ":ugcPost:" in post.id_remoto else "shares"
        dados = self._conferir(
            self.http.get(
                f"{API}/rest/organizationalEntityShareStatistics?q=organizationalEntity"
                f"&organizationalEntity={quote(self.destino.conta_id, safe='')}"
                f"&{tipo}=List({quote(post.id_remoto, safe='')})",
                headers=self._cabecalhos(),
            ),
            "LinkedIn (estatisticas)",
        ).json()
        elementos = dados.get("elements") or [{}]
        total = elementos[0].get("totalShareStatistics") or {}
        nomes = {
            "uniqueImpressionsCount": "alcance",
            "impressionCount": "impressoes",
            "likeCount": "curtidas",
            "commentCount": "comentarios",
            "shareCount": "compartilhamentos",
            "clickCount": "cliques_na_rede",
        }
        return {nosso: total[deles] for deles, nosso in nomes.items() if deles in total}

    def metricas(self, post) -> dict:
        if self.destino.autor == "organizacao":
            try:
                estatisticas = self._estatisticas(post)
                if estatisticas:
                    return estatisticas
            except ErroDaRede:
                pass  # sem a permissao de administrador: ao menos reacoes e comentarios
        dados = self._conferir(
            self.http.get(
                f"{API}/rest/socialActions/{quote(post.id_remoto, safe='')}",
                headers=self._cabecalhos(),
            ),
            "LinkedIn (resultado)",
        ).json()
        return {
            "curtidas": (dados.get("likesSummary") or {}).get("totalLikes", 0),
            "comentarios": (dados.get("commentsSummary") or {}).get("aggregatedTotalComments", 0),
        }


def _formato(conteudo: dict) -> str:
    """O tipo do post, no codigo que o diagnostico usa."""
    if "multiImage" in conteudo:
        return "CAROUSEL_ALBUM"
    if "article" in conteudo:
        return "ARTICLE"
    media = (conteudo.get("media") or {}).get("id", "")
    if ":video:" in media:
        return "VIDEO"
    if ":document:" in media:
        return "DOCUMENT"
    if ":image:" in media:
        return "IMAGE"
    return "TEXT" if not conteudo else ""


def _sem_escape(texto: str) -> str:
    """O texto do post sem as barras e marcacoes do 'little text'."""
    texto = re.sub(r"\{hashtag\|\\?#\|([^}]*)\}", r"#\1", texto or "")
    texto = re.sub(r"@\[([^\]]*)\]\([^)]*\)", r"\1", texto)
    return re.sub(r"\\([\\|{}@\[\]()<>#*_~])", r"\1", texto)


REDE = Rede(
    codigo="linkedin",
    nome="LinkedIn",
    formato=Formato(
        max_caracteres=3000, max_hashtags=3, link="comentario", imagem="capa", dobra=210
    ),
    estilo=(
        "Publico profissional, com mais escolaridade, que le no intervalo do trabalho e "
        "espera conteudo de quem pratica: dado com fonte, opiniao fundamentada, "
        "bastidor da profissao. Texto mais completo que nas outras redes (1.000 a 1.500 "
        "caracteres), em paragrafos de 1 a 3 linhas com linha em branco entre eles. A "
        "PRIMEIRA LINHA e tudo: e o que aparece antes do 'ver mais' (~200 caracteres) e "
        "precisa fazer a pessoa clicar. Termine com uma pergunta que convide comentario "
        "de quem trabalha com isso. Sem emoji em excesso (no maximo 2). O link vai no "
        "primeiro comentario, nao no texto."
    ),
    publico_padrao="Profissionais e gestores da area, que querem dado e experiencia pratica.",
    tom_padrao="profissional, direto, com opiniao de quem pratica",
    autores=(("pessoa", "Perfil pessoal"), ("organizacao", "Pagina da empresa")),
    preparo=(
        "Criar um app em linkedin.com/developers (ligado a uma pagina da empresa).",
        "No app, pedir o produto 'Share on LinkedIn' (perfil pessoal, liberacao imediata) "
        "e o 'Sign In with LinkedIn using OpenID Connect'.",
        "Para a pagina da empresa: pedir a 'Community Management API' (revisao do "
        "LinkedIn, alguns dias).",
        "Em Auth > Authorized redirect URLs, cadastrar o endereco que a tela de conexao mostra.",
        "Copiar Client ID e Client Secret para SOCIAL_LINKEDIN_CLIENT_ID e "
        "SOCIAL_LINKEDIN_CLIENT_SECRET no .env do servidor.",
    ),
    publicador=PublicadorLinkedIn,
    oauth=OAuthLinkedIn,
)
