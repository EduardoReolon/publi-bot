"""Instagram: carrossel, pela Instagram Graph API (conta profissional ligada a
uma pagina do Facebook, app da Meta).

Publicar e em tres passos: cada imagem vira um "container" (a Meta busca a
imagem por URL publica — as laminas saem do endereco /redes/m/... do
PubliBot), o carrossel vira outro container, e o `media_publish` publica.

Instagram nao tem link clicavel na legenda: a chamada e para o "link na bio",
e a bio aponta para a pagina /redes/bio/<chave>/ do PubliBot, que lista os
artigos dos posts recentes com o link rastreado de cada um.
"""

from __future__ import annotations

import time
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings

from apps.social.redes.base import (
    ComentarioLido,
    ErroDaRede,
    Formato,
    Publicado,
    Publicador,
    Rede,
)
from apps.social.redes.oauth import OAuth, acesso_valido


def graph() -> str:
    return f"https://graph.facebook.com/{getattr(settings, 'SOCIAL_META_VERSAO', '') or 'v21.0'}"


class OAuthInstagram(OAuth):
    ID_DO_APP = "SOCIAL_META_APP_ID"
    SEGREDO_DO_APP = "SOCIAL_META_APP_SECRET"
    # O token longo da Meta so e trocado por outro enquanto vale: renova na
    # ultima semana (a medicao diaria passa por aqui).
    RENOVAR_ANTES = timedelta(days=7)
    ESCOPOS = (
        "instagram_basic,instagram_content_publish,instagram_manage_comments,"
        "instagram_manage_insights,pages_show_list,pages_read_engagement,business_management"
    )

    def url_de_autorizacao(
        self, destino, redirect_uri: str, state: str, *, escopos_extras: str = ""
    ) -> str:
        """`escopos_extras`: "ads_read" no "Conectar anuncios" (revisao propria
        da Meta; pedido a parte para nao travar a conexao da conta).

        App do tipo Empresa usa o "Login do Facebook para Empresas": as
        permissoes nao vao em `scope`, e sim numa CONFIGURACAO criada no painel
        do app, mandada pelo id (`config_id`). Com SOCIAL_META_CONFIG_ID no
        .env, vai o id (e SOCIAL_META_CONFIG_ID_ANUNCIOS no "Conectar
        anuncios"); sem, vale o `scope` do Login do Facebook classico."""
        cliente, _segredo = self.app()
        versao = graph().rsplit("/", 1)[-1]
        configuracao = getattr(settings, "SOCIAL_META_CONFIG_ID", "") or ""
        if escopos_extras:
            configuracao = getattr(settings, "SOCIAL_META_CONFIG_ID_ANUNCIOS", "") or configuracao
        parametros = {
            "client_id": cliente,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
        }
        if configuracao:
            parametros["config_id"] = configuracao
        else:
            parametros["scope"] = ",".join(x for x in [self.ESCOPOS, escopos_extras] if x)
        return f"https://www.facebook.com/{versao}/dialog/oauth?" + urlencode(parametros)

    def trocar_codigo(self, destino, codigo: str, redirect_uri: str) -> dict:
        cliente, segredo = self.app()
        curto = self._json(
            self.http.get(
                f"{graph()}/oauth/access_token",
                params={
                    "client_id": cliente,
                    "client_secret": segredo,
                    "redirect_uri": redirect_uri,
                    "code": codigo,
                },
            ),
            "Meta (token)",
        )
        longo = self._json(
            self.http.get(
                f"{graph()}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": cliente,
                    "client_secret": segredo,
                    "fb_exchange_token": curto["access_token"],
                },
            ),
            "Meta (token de longa duracao)",
        )
        # O id de quem conectou (por app): e o que a Meta manda no pedido de
        # exclusao de dados, para saber de quem apagar.
        eu = self.http.get(
            f"{graph()}/me", params={"fields": "id", "access_token": longo["access_token"]}
        )
        return {
            "access_token": longo["access_token"],
            "expira_em": self.expira(longo.get("expires_in") or 60 * 86400),
            "usuario_id": eu.json().get("id", "") if eu.status_code < 400 else "",
        }

    def contas(self, destino) -> list[tuple[str, str]]:
        token = destino.ler_credenciais()["access_token"]
        dados = self._json(
            self.http.get(
                f"{graph()}/me/accounts",
                params={
                    "fields": (
                        "name,instagram_business_account{id,username},"
                        "connected_instagram_account{id,username}"
                    ),
                    "access_token": token,
                },
            ),
            "Meta (paginas)",
        )
        contas = []
        paginas = []
        for pagina in dados.get("data", []):
            paginas.append(pagina.get("name", "") or "?")
            # O campo de sempre; e o que a Meta preenche em algumas Paginas da
            # experiencia nova, quando o primeiro vem vazio.
            conta = (
                pagina.get("instagram_business_account")
                or pagina.get("connected_instagram_account")
                or {}
            )
            if conta.get("id"):
                contas.append(
                    (conta["id"], f"@{conta.get('username', '')} ({pagina.get('name', '')})")
                )
        if not contas:
            # Para a tela dizer o que falta, e nao so "nenhuma conta". Primeiro
            # o mais certeiro: as permissoes que a Meta de fato concedeu.
            faltam = self._permissoes_que_faltam(token)
            if faltam:
                self.sem_contas = (
                    f"A Meta nao concedeu: {', '.join(faltam)}. Sem elas, o Instagram da "
                    "Pagina nao aparece. Acrescente na configuracao de login do app "
                    "(Login do Facebook para Empresas > Configuracoes > a configuracao do "
                    "SOCIAL_META_CONFIG_ID > Editar > Permissoes) e conecte de novo, em "
                    "'Editar acesso anterior'."
                )
                return contas
            self.sem_contas = (
                f"A Meta mostrou a(s) Pagina(s) {', '.join(paginas)}, mas sem nenhum "
                "Instagram dentro. Confira: (1) a conta do Instagram e PROFISSIONAL "
                "(Configuracoes > Tipo de conta e ferramentas); (2) ela esta ligada a "
                "Pagina (no Facebook, como a Pagina: Configuracoes > Contas vinculadas > "
                "Instagram); (3) na tela de login da Meta, em 'Editar acesso anterior', a "
                "conta do Instagram esta marcada (ou remova o app em Configuracoes > "
                "Integracoes comerciais e conecte do zero)."
                if paginas
                else "A Meta nao mostrou nenhuma Pagina: na tela de permissoes, marque a "
                "Pagina da empresa e a conta do Instagram ligada a ela."
            )
        return contas

    PERMISSOES_NECESSARIAS = (
        "instagram_basic",
        "pages_show_list",
        "pages_read_engagement",
        "instagram_content_publish",
    )

    def _permissoes_que_faltam(self, token: str) -> list[str]:
        resposta = self.http.get(f"{graph()}/me/permissions", params={"access_token": token})
        if resposta.status_code >= 400:
            return []
        concedidas = {
            p.get("permission")
            for p in resposta.json().get("data", [])
            if p.get("status") == "granted"
        }
        return [p for p in self.PERMISSOES_NECESSARIAS if p not in concedidas]

    def renovar(self, destino) -> bool:
        """O token longo da Meta pode ser trocado por outro longo antes de vencer."""
        cliente, segredo = self.app()
        atual = destino.ler_credenciais()
        resposta = self.http.get(
            f"{graph()}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": cliente,
                "client_secret": segredo,
                "fb_exchange_token": atual.get("access_token", ""),
            },
        )
        if resposta.status_code >= 400:
            return False
        dados = resposta.json()
        self.gravar(
            destino,
            {
                **atual,
                "access_token": dados["access_token"],
                "expira_em": self.expira(dados.get("expires_in") or 60 * 86400),
            },
        )
        return True


class PublicadorInstagram(Publicador):
    # Quantas vezes conferir se o carrossel ficou pronto, e quanto esperar.
    TENTATIVAS = 10
    ESPERA = 3.0

    def __init__(self, destino, *, http=None, esperar=time.sleep):
        super().__init__(destino, http=http)
        self.esperar = esperar

    def _token(self) -> str:
        return acesso_valido(self.destino, OAuthInstagram(http=self.http))["access_token"]

    def _post(self, caminho: str, contexto: str, **params) -> dict:
        return self._conferir(
            self.http.post(
                f"{graph()}/{caminho}", params={**params, "access_token": self._token()}
            ),
            contexto,
        ).json()

    def _get(self, caminho: str, contexto: str, **params) -> dict:
        return self._conferir(
            self.http.get(f"{graph()}/{caminho}", params={**params, "access_token": self._token()}),
            contexto,
        ).json()

    def publicar(self, post, texto: str, imagens) -> Publicado:
        if not imagens:
            raise ErroDaRede("Instagram precisa de pelo menos uma imagem.")
        conta = self.destino.conta_id
        tem_video = any(i.video for i in imagens)
        if len(imagens) == 1 and imagens[0].video:
            container = self._post(
                f"{conta}/media",
                "Instagram (reels)",
                media_type="REELS",
                video_url=imagens[0].url,
                caption=texto,
            )["id"]
        elif len(imagens) == 1:
            container = self._post(
                f"{conta}/media",
                "Instagram (imagem)",
                image_url=imagens[0].url,
                caption=texto,
                **_alt(imagens[0]),
            )["id"]
        else:
            filhos = [
                self._post(
                    f"{conta}/media",
                    "Instagram (lamina)",
                    **(
                        {"media_type": "VIDEO", "video_url": imagem.url}
                        if imagem.video
                        else {"image_url": imagem.url, **_alt(imagem)}
                    ),
                    is_carousel_item="true",
                )["id"]
                for imagem in imagens[:10]
            ]
            container = self._post(
                f"{conta}/media",
                "Instagram (carrossel)",
                media_type="CAROUSEL",
                children=",".join(filhos),
                caption=texto,
            )["id"]
        # Video demora a processar na Meta: espera mais.
        for _tentativa in range(self.TENTATIVAS * (6 if tem_video else 1)):
            estado = self._get(container, "Instagram (preparo)", fields="status_code")
            if estado.get("status_code") == "FINISHED":
                break
            if estado.get("status_code") == "ERROR":
                raise ErroDaRede(f"Instagram recusou as imagens: {estado}")
            self.esperar(self.ESPERA)
        publicado = self._post(
            f"{conta}/media_publish", "Instagram (publicar)", creation_id=container
        )["id"]
        link = self._get(publicado, "Instagram (link)", fields="permalink").get("permalink", "")
        return Publicado(id_remoto=publicado, url=link)

    def ler_comentarios(self, post) -> list[ComentarioLido]:
        dados = self._get(
            f"{post.id_remoto}/comments",
            "Instagram (comentarios)",
            fields="id,text,username,timestamp,replies{username}",
            limit=100,
        )
        dono = self.destino.conta_nome.split(" ", 1)[0].lstrip("@")
        return [
            ComentarioLido(
                id_remoto=item["id"],
                texto=item.get("text", ""),
                autor=item.get("username", ""),
                escrito_em=item.get("timestamp", ""),
                do_dono=bool(dono) and item.get("username") == dono,
                respondido=bool(dono)
                and any(
                    r.get("username") == dono for r in (item.get("replies") or {}).get("data", [])
                ),
            )
            for item in dados.get("data", [])
        ]

    CAMPOS_DO_HISTORICO = (
        "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
    )

    def historico(self, limite: int = 500) -> list[dict]:
        dados = self._get(
            f"{self.destino.conta_id}/media",
            "Instagram (posts antigos)",
            fields=self.CAMPOS_DO_HISTORICO,
            limit=50,
        )
        itens = list(dados.get("data", []))
        proxima = (dados.get("paging") or {}).get("next")
        while proxima and len(itens) < limite:
            # O endereco "next" ja traz o acesso: nao repetir o token.
            dados = self._conferir(self.http.get(proxima), "Instagram (posts antigos)").json()
            itens += dados.get("data", [])
            proxima = (dados.get("paging") or {}).get("next")
        saida = []
        for item in itens[:limite]:
            formato = item.get("media_type", "")
            if item.get("media_product_type") == "REELS":
                formato = "REELS"
            saida.append(
                {
                    "id_remoto": str(item["id"]),
                    "legenda": item.get("caption", "") or "",
                    "formato": formato,
                    "link": item.get("permalink", ""),
                    "publicado_em": item.get("timestamp", ""),
                    "curtidas": item.get("like_count"),
                    "comentarios": item.get("comments_count"),
                }
            )
        return saida

    def responder(self, post, id_do_comentario: str, texto: str) -> str:
        return self._post(f"{id_do_comentario}/replies", "Instagram (resposta)", message=texto).get(
            "id", ""
        )

    def seguidores(self) -> int | None:
        dados = self._get(self.destino.conta_id, "Instagram (seguidores)", fields="followers_count")
        return dados.get("followers_count")

    def referencias(self, hashtag: str, limite: int = 25) -> list[dict]:
        """Hashtag Search: os posts com mais engajamento da hashtag (de outras
        contas). A Meta limita a 30 hashtags diferentes por semana por conta."""
        conta = self.destino.conta_id
        achadas = self._get(
            "ig_hashtag_search", "Instagram (hashtag)", user_id=conta, q=hashtag.lstrip("#")
        ).get("data", [])
        if not achadas:
            return []
        dados = self._get(
            f"{achadas[0]['id']}/top_media",
            "Instagram (posts da hashtag)",
            user_id=conta,
            fields="id,caption,like_count,comments_count,media_type,permalink,timestamp",
            limit=limite,
        )
        return [
            {
                "id_remoto": item["id"],
                "legenda": item.get("caption", "") or "",
                "formato": item.get("media_type", ""),
                "curtidas": item.get("like_count") or 0,
                "comentarios": item.get("comments_count") or 0,
                "link": item.get("permalink", ""),
                "publicado_em": item.get("timestamp", ""),
            }
            for item in dados.get("data", [])
        ]

    NOMES = {
        "reach": "alcance",
        "likes": "curtidas",
        "comments": "comentarios",
        "shares": "compartilhamentos",
        "saved": "salvos",
    }

    def metricas(self, post) -> dict:
        dados = self._get(
            f"{post.id_remoto}/insights", "Instagram (resultado)", metric=",".join(self.NOMES)
        )
        saida = {}
        for item in dados.get("data", []):
            valores = item.get("values") or [{}]
            saida[self.NOMES.get(item.get("name"), item.get("name"))] = valores[0].get("value", 0)
        return saida


def _alt(imagem) -> dict:
    """Texto alternativo da imagem (leitor de tela), quando houver."""
    return {"alt_text": imagem.alt[:1000]} if imagem.alt else {}


REDE = Rede(
    codigo="instagram",
    nome="Instagram",
    formato=Formato(
        max_caracteres=2200,
        max_hashtags=5,
        link="bio",
        imagem="laminas",
        min_laminas=4,
        max_laminas=8,
        dobra=125,
    ),
    estilo=(
        "Publico amplo, rolando a tela rapido: o post tem 2 segundos para fazer a pessoa "
        "parar. Isso vem de identificacao ('e o meu caso': uma situacao concreta que a "
        "pessoa vive, nas palavras dela) ou de surpresa ('nossa, isso e incrivel': um "
        "achado contra a intuicao, com o numero). O carrossel conta UMA ideia por lamina, "
        "frases curtas (ate 20 palavras). A primeira lamina e o gancho; a SEGUNDA tambem "
        "precisa prender sozinha (quem nao arrasta, o Instagram mostra o carrossel de novo "
        "ja na segunda). Havendo achado com numero, o gancho abre com o mais surpreendente, "
        "copiado exato: espanto e curiosidade, nunca alarme, exagero ou promessa (em saude "
        "o sensacionalismo e vedado). A ultima convida a mandar o post para quem precisa "
        "ver isso e lembra o link na bio. Legenda curta (300 a 600 caracteres), primeira "
        "linha forte, linguagem do dia a dia, sem jargao. Sem link na legenda (nao e "
        "clicavel). Nada de 'siga para mais' ou 'este post vai sumir'."
    ),
    publico_padrao="Pessoas comuns que vivem o problema que o negocio resolve.",
    tom_padrao="proximo, simples, acolhedor",
    autores=(("perfil", "Conta profissional"),),
    preparo=(
        "A conta do Instagram precisa ser PROFISSIONAL (empresa ou criador) e estar "
        "ligada a uma pagina do Facebook.",
        "Criar um app em developers.facebook.com (tipo Empresa) com os produtos "
        "'Facebook Login' e 'Instagram Graph API'.",
        "Em Facebook Login > Configuracoes, cadastrar o endereco de retorno que a tela de "
        "conexao mostra.",
        "Para usar com contas que nao sao administradoras do app, pedir a revisao das "
        "permissoes instagram_content_publish e instagram_manage_comments.",
        "Copiar ID e chave secreta do app para SOCIAL_META_APP_ID e SOCIAL_META_APP_SECRET.",
        "Na bio do Instagram, pôr o endereco 'link na bio' que a tela do destino mostra.",
    ),
    publicador=PublicadorInstagram,
    oauth=OAuthInstagram,
)
