"""As APIs das redes, sobre respostas simuladas (sem rede): o que o PubliBot
manda e o que ele entende da volta. Formatos das documentacoes oficiais
(LinkedIn Posts API, Instagram Graph API, Business Profile API). Contra a
rede de verdade, so com a conta conectada: ver docs/REDES_SOCIAIS.md.
"""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.social import comentarios, fontes, publicacao
from apps.social.models import Comentario, Destino, Post
from apps.social.redes.base import ErroDaRede, ImagemPublica, NaoConectado
from apps.social.redes.instagram import OAuthInstagram, PublicadorInstagram
from apps.social.redes.linkedin import OAuthLinkedIn, texto_do_linkedin


class Rede:
    """Um servidor falso: registra os pedidos e responde pela rota."""

    def __init__(self, rotas):
        self.rotas = rotas
        self.pedidos = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        chave = f"{pedido.method} {pedido.url.host}{pedido.url.path}"
        for prefixo, resposta in self.rotas.items():
            if chave.startswith(prefixo):
                return resposta(pedido) if callable(resposta) else resposta
        return httpx.Response(404, json={"erro": chave})

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _conectado(rede: str, conta: str, **campos) -> Destino:
    destino = Destino.objects.create(rede=rede, nome=rede, conta_id=conta, **campos)
    destino.gravar_credenciais({"access_token": "tok", "refresh_token": "ref"})
    destino.expira_em = timezone.now() + timedelta(days=30)
    destino.save()
    return destino


def _post(destino, **campos) -> Post:
    return Post.objects.create(
        **{
            "destino": destino,
            "artigo_titulo": "Cansaco que nao passa",
            "artigo_url": "https://site.exemplo.org/cansaco/",
            "motivo": "novo",
            "situacao": Post.Situacao.APROVADO,
            "agendado_para": timezone.now() - timedelta(minutes=1),
            **campos,
        }
    )


def test_texto_do_linkedin_escapa_e_faz_hashtag():
    assert texto_do_linkedin("Veja (agora) #saude") == "Veja \\(agora\\) {hashtag|\\#|saude}"


@pytest.mark.django_db
def test_linkedin_publica_com_imagem_e_comenta_o_link(ambiente, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    default_storage.save("capas/capa.png", ContentFile(b"png"))
    rede = Rede(
        {
            "POST api.linkedin.com/rest/images": httpx.Response(
                200,
                json={
                    "value": {"uploadUrl": "https://up.linkedin.com/u/1", "image": "urn:li:image:9"}
                },
            ),
            "PUT up.linkedin.com/u/1": httpx.Response(201),
            "POST api.linkedin.com/rest/posts": httpx.Response(
                201, headers={"x-restli-id": "urn:li:share:77"}
            ),
            "POST api.linkedin.com/rest/socialActions": httpx.Response(
                201, headers={"x-restli-id": "urn:li:comment:(urn:li:share:77,5)"}
            ),
        }
    )
    destino = _conectado("linkedin", "urn:li:person:abc")
    post = _post(
        destino,
        texto="38% dos adultos (em estudo) #saude",
        extras={"primeiro_comentario": "O artigo:", "link": "https://painel/redes/r/x/"},
        imagens=[{"caminho": "capas/capa.png", "url": "https://painel/capa"}],
    )

    publicacao.publicar_vencidos(http=rede.cliente())

    post.refresh_from_db()
    assert post.situacao == Post.Situacao.PUBLICADO
    assert post.id_remoto == "urn:li:share:77"
    assert post.url_remota == "https://www.linkedin.com/feed/update/urn:li:share:77/"
    corpo = json.loads(rede.pedidos[2].content)
    assert corpo["author"] == "urn:li:person:abc"
    assert corpo["content"]["media"]["id"] == "urn:li:image:9"
    assert corpo["commentary"] == "38% dos adultos \\(em estudo\\) {hashtag|\\#|saude}"
    assert rede.pedidos[2].headers["LinkedIn-Version"] == settings.SOCIAL_LINKEDIN_VERSAO
    comentario = json.loads(rede.pedidos[3].content)
    assert comentario["message"]["text"] == "O artigo: https://painel/redes/r/x/"
    assert comentario["object"] == "urn:li:share:77"


@pytest.mark.django_db
def test_falha_da_rede_tenta_de_novo_ate_o_limite(ambiente):
    rede = Rede(
        {"POST api.linkedin.com/rest/posts": httpx.Response(422, json={"message": "duplicate"})}
    )
    destino = _conectado("linkedin", "urn:li:person:abc")
    post = _post(destino, texto="oi")

    for _ in range(3):
        publicacao.publicar_vencidos(http=rede.cliente())
        post.refresh_from_db()

    assert post.situacao == Post.Situacao.FALHOU and post.tentativas == 3
    assert "duplicate" in post.erro


@pytest.mark.django_db
def test_conta_sem_api_fica_para_copiar(ambiente):
    destino = Destino.objects.create(rede="gmn", nome="Google")
    post = _post(destino, texto="oi")
    assert publicacao.publicar_vencidos() == 0
    post.refresh_from_db()
    assert post.situacao == Post.Situacao.APROVADO
    with pytest.raises(NaoConectado):
        publicacao.publicar(post)


@pytest.mark.django_db
def test_instagram_carrossel_em_tres_passos(ambiente):
    filhos = iter(["c1", "c2", "c3"])
    estados = iter(["IN_PROGRESS", "FINISHED"])

    def criar(pedido):
        params = parse_qs(urlsplit(str(pedido.url)).query)
        if params.get("media_type") == ["CAROUSEL"]:
            assert params["children"] == ["c1,c2,c3"] and params["caption"] == ["Legenda"]
            return httpx.Response(200, json={"id": "carrossel"})
        assert params["is_carousel_item"] == ["true"]
        return httpx.Response(200, json={"id": next(filhos)})

    def ler(pedido):
        if pedido.url.path.endswith("/carrossel"):
            return httpx.Response(200, json={"status_code": next(estados)})
        return httpx.Response(200, json={"permalink": "https://instagram.com/p/X"})

    rede = Rede(
        {
            "POST graph.facebook.com/v21.0/17841/media_publish": httpx.Response(
                200, json={"id": "M1"}
            ),
            "POST graph.facebook.com/v21.0/17841/media": criar,
            "GET graph.facebook.com": ler,
        }
    )
    destino = _conectado("instagram", "17841")
    post = _post(destino, texto="Legenda")
    publicador = PublicadorInstagram(destino, http=rede.cliente(), esperar=lambda s: None)
    imagens = [ImagemPublica(url=f"https://painel/m/{n}.png", caminho="") for n in (1, 2, 3)]

    publicado = publicador.publicar(post, "Legenda", imagens)

    assert (publicado.id_remoto, publicado.url) == ("M1", "https://instagram.com/p/X")


@pytest.mark.django_db
def test_google_renova_o_acesso_e_publica_com_botao(ambiente, settings):
    settings.SOCIAL_GOOGLE_CLIENT_ID, settings.SOCIAL_GOOGLE_CLIENT_SECRET = "cid", "seg"
    rede = Rede(
        {
            "POST oauth2.googleapis.com/token": httpx.Response(
                200, json={"access_token": "novo", "expires_in": 3600}
            ),
            "POST mybusiness.googleapis.com/v4/accounts/1/locations/2/localPosts": httpx.Response(
                200,
                json={"name": "accounts/1/locations/2/localPosts/9", "searchUrl": "https://g/9"},
            ),
        }
    )
    destino = _conectado("gmn", "accounts/1/locations/2")
    destino.expira_em = timezone.now() - timedelta(minutes=1)  # vencido: renova
    destino.save()
    post = _post(
        destino,
        texto="Texto",
        extras={"link": "https://painel/r/x/"},
        imagens=[{"caminho": "", "url": "https://painel/capa.png"}],
    )

    publicacao.publicar_vencidos(http=rede.cliente())

    post.refresh_from_db()
    assert post.situacao == Post.Situacao.PUBLICADO and post.url_remota == "https://g/9"
    destino.refresh_from_db()
    assert destino.ler_credenciais()["access_token"] == "novo"
    corpo = json.loads(rede.pedidos[-1].content)
    assert corpo["callToAction"] == {"actionType": "LEARN_MORE", "url": "https://painel/r/x/"}
    assert corpo["media"][0]["sourceUrl"] == "https://painel/capa.png"
    assert rede.pedidos[-1].headers["Authorization"] == "Bearer novo"


@pytest.mark.django_db
def test_oauth_sem_app_configurado_avisa_o_que_falta(ambiente, settings):
    settings.SOCIAL_LINKEDIN_CLIENT_ID = ""
    destino = Destino.objects.create(rede="linkedin", nome="Perfil")
    with pytest.raises(NaoConectado, match="SOCIAL_LINKEDIN_CLIENT_ID"):
        OAuthLinkedIn().url_de_autorizacao(destino, "https://painel/retorno/", "estado")


@pytest.mark.django_db
def test_oauth_troca_o_codigo_e_lista_as_contas(ambiente, settings):
    settings.SOCIAL_META_APP_ID, settings.SOCIAL_META_APP_SECRET = "app", "seg"
    rede = Rede(
        {
            "GET graph.facebook.com/v21.0/oauth/access_token": lambda p: httpx.Response(
                200,
                json={
                    "access_token": "longo" if "fb_exchange_token" in str(p.url) else "curto",
                    "expires_in": 5184000,
                },
            ),
            "GET graph.facebook.com/v21.0/me/accounts": httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "name": "Clinica",
                            "instagram_business_account": {"id": "178", "username": "clinica"},
                        },
                        {"name": "Sem insta"},
                    ]
                },
            ),
        }
    )
    destino = Destino.objects.create(rede="instagram", nome="Insta")
    oauth = OAuthInstagram(http=rede.cliente())
    url = oauth.url_de_autorizacao(destino, "https://painel/retorno/", "estado")
    assert "client_id=app" in url and "instagram_content_publish" in url

    oauth.gravar(destino, oauth.trocar_codigo(destino, "codigo", "https://painel/retorno/"))

    assert destino.ler_credenciais()["access_token"] == "longo"
    assert destino.expira_em > timezone.now() + timedelta(days=50)
    assert oauth.contas(destino) == [("178", "@clinica (Clinica)")]


@pytest.mark.django_db
def test_pergunta_no_comentario_vira_pergunta_e_a_resposta_volta(ambiente, monkeypatch):
    criadas, respostas = [], {}
    monkeypatch.setattr(
        fontes,
        "criar_pergunta",
        lambda texto, ref: criadas.append(ref) or "8d0f0b0e-0000-0000-0000-000000000001",
    )
    monkeypatch.setattr(fontes, "respostas", lambda ids: respostas)
    enviadas = []

    def responder(pedido):
        enviadas.append(parse_qs(urlsplit(str(pedido.url)).query)["message"][0])
        return httpx.Response(200, json={"id": "R1"})

    rede = Rede(
        {
            "GET graph.facebook.com/v21.0/M1/comments": httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "C1",
                            "text": "Isso vale para quem tem 60 anos?",
                            "username": "maria",
                        },
                        {"id": "C2", "text": "Amei, obrigada!", "username": "joao"},
                        {"id": "C3", "text": "@ana", "username": "bia"},
                        {"id": "C4", "text": "Obrigada pela pergunta!", "username": "clinica"},
                    ]
                },
            ),
            "POST graph.facebook.com/v21.0/C1/replies": responder,
        }
    )
    destino = _conectado("instagram", "17841", conta_nome="@clinica (Clinica)")
    post = _post(destino, situacao=Post.Situacao.PUBLICADO, id_remoto="M1")

    assert comentarios.ler(post, http=rede.cliente()) == 3  # a do dono fica de fora
    tipos = dict(Comentario.objects.values_list("id_remoto", "tipo"))
    assert tipos == {"C1": "pergunta", "C2": "elogio", "C3": "outro"}
    assert criadas == ["rede:instagram:C1"]
    assert comentarios.ler(post, http=rede.cliente()) == 0  # nao duplica

    assert comentarios.responder_aprovadas(http=rede.cliente()) == 0  # ainda sem resposta
    respostas["8d0f0b0e-0000-0000-0000-000000000001"] = {
        "texto": "Sim. A partir dos 60 anos o exame e anual. Converse com seu medico.",
        "url": "https://site.exemplo.org/perguntas/1/",
    }
    assert comentarios.responder_aprovadas(http=rede.cliente()) == 1
    assert enviadas[0].endswith("https://site.exemplo.org/perguntas/1/")
    assert Comentario.objects.get(id_remoto="C1").respondido_em is not None


def test_classificar_sem_modelo():
    assert comentarios.classificar("como faco para marcar consulta") == "pergunta"
    assert comentarios.classificar("E normal sentir isso depois?") == "pergunta"
    assert comentarios.classificar("Que absurdo essa demora") == "reclamacao"
    assert comentarios.classificar("Comigo aconteceu igual") == "relato"
    assert comentarios.classificar("ok?") == "outro"  # curto demais para ser pergunta


def test_erro_da_rede_mostra_a_mensagem_dela():
    from apps.social.redes.base import Publicador

    with pytest.raises(ErroDaRede, match="HTTP 401"):
        Publicador._conferir(None, httpx.Response(401, json={"error": "token"}), "LinkedIn")
