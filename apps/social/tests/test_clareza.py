"""Clareza do post: algoritmo (copia do artigo, titulo longo, legibilidade,
lamina sem cena), revisor sem o artigo, post inteiro pela outra IA,
abordagem/formato pedidos pela outra IA e imagens geradas aqui."""

from __future__ import annotations

import io
import json

import pytest
from django.urls import reverse
from PIL import Image

from apps.social import clareza, criacao, fontes, identidade, outra_ia, redacao
from apps.social.abordagens import garantir_abordagens
from apps.social.models import Abordagem, Post
from apps.social.tests.test_escrita import ARTIGO, _destino, modulo  # noqa: F401
from apps.social.tests.test_outra_ia import _tema
from apps.social.tests.test_telas import _artigo_publicado

U = "core.urls_tenants"
COPIADA = "Voce acorda cansado mesmo depois de dormir oito horas por noite"
LAMINAS_CLARAS = [
    {"titulo": "Acorda cansado todo dia?", "texto": "", "visual": "pessoa bocejando na cama"},
    {"titulo": "38% dos adultos", "texto": "sentem isso", "visual": "fila de pessoas com sono"},
    {"titulo": "Pode ser anemia", "texto": "12,5% dos casos", "visual": "tubo de exame"},
    {"titulo": "Peca um exame", "texto": "e simples", "visual": "medico conversando"},
]


def test_algoritmo_da_clareza():
    assert clareza.copia_do_artigo(f"Sabia? {COPIADA}!", ARTIGO.texto)
    assert not clareza.copia_do_artigo("Acorda cansado mesmo dormindo bem?", ARTIGO.texto)
    facil = "O sono ruim cansa. Durma cedo. Beba agua. Ande um pouco. Fale com o medico hoje."
    dificil = (
        "A operacionalizacao inadequada da infraestrutura organizacional compromete "
        "substancialmente a sustentabilidade institucional e a competitividade estrategica."
    )
    assert clareza.legibilidade(facil) > clareza.legibilidade(dificil)
    avisos = clareza.avisos(
        {
            "gancho": "x",
            "laminas": [
                {"titulo": COPIADA, "texto": ""},
                {"titulo": "um dois tres quatro cinco seis sete oito nove dez", "texto": ""},
            ],
        },
        ARTIGO.texto,
        laminas=True,
    )
    texto = " ".join(avisos)
    assert "copia frase do artigo (1)" in texto and "titulo longo" in texto
    assert "sem 'visual'" in texto


@pytest.mark.django_db
def test_revisor_sem_o_artigo_manda_reescrever(ambiente, modulo, settings, tmp_path, monkeypatch):  # noqa: F811
    settings.MEDIA_ROOT = tmp_path
    chamadas = []
    respostas = [
        # 1) copia frase do artigo e nao tem cena: o algoritmo devolve
        {"gancho": "Cansaco?", "texto": "Leia.", "laminas": [{"titulo": COPIADA, "texto": ""}] * 4},
        # 2) passa no algoritmo; o revisor da 4 e diz o que nao entendeu
        {"gancho": "Cansado?", "texto": "Leia.", "laminas": LAMINAS_CLARAS},
        # 3) a versao final
        {
            "ideia": "cansaco pode ser anemia",
            "gancho": "Cansado?",
            "texto": "Leia.",
            "laminas": LAMINAS_CLARAS,
        },
    ]
    notas = [4, 9]

    def executar(chave, variaveis, json_schema=None):
        chamadas.append((chave, variaveis))
        if chave == "social_revisor":
            nota = notas.pop(0)
            return json.dumps({"entendi": "algo sobre sono", "nota": nota, "problemas": ["vago"]})
        return json.dumps(respostas.pop(0))

    monkeypatch.setattr(fontes, "executar", executar)
    post = Post.objects.create(
        destino=_destino("instagram"), artigo_id=ARTIGO.id, motivo=Post.Motivo.NOVO
    )
    redacao.escrever(post)
    post.refresh_from_db()
    escritas = [v for c, v in chamadas if c != "social_revisor"]
    assert len(escritas) == 3
    assert "CLAREZA" in escritas[0]["ajuste"] and "visual" in escritas[0]["ajuste"]
    assert "copia frase do artigo" in escritas[1]["ajuste"]
    assert "nota 4/10" in escritas[2]["ajuste"] and "vago" in escritas[2]["ajuste"]
    assert post.avisos == [] and post.extras["clareza"]["nota"] == 9
    assert post.extras["ideia_central"] == "cansaco pode ser anemia"
    assert post.extras["laminas"][0]["visual"] == "pessoa bocejando na cama"
    assert "pessoa bocejando na cama" in identidade.pedido(post)


RESPOSTA_DO_POST = """Aqui esta.

IDEIA: cansaco que nao passa pode ser anemia
GANCHO: Acorda cansado mesmo dormindo bem?
LEGENDA: Pode ser so falta de ferro. O exame e simples.
HASHTAGS: Cansaco, #SaudeDaMulher, Anemia
LAMINAS:
- Acorda cansado? | Mesmo dormindo 8 horas | mulher olhando o despertador, exausta
- 45% das mulheres | tem anemia | grafico simples de pessoas
- Peca o exame | de sangue, e simples | tubo de ensaio na mao do medico
- Mande para alguem | que vive cansada | duas amigas no sofa
COMENTARIOS:
Teste com a abordagem de pergunta.
FIM
"""


@pytest.mark.django_db
def test_post_inteiro_pela_outra_ia(ambiente, modulo, settings, tmp_path):  # noqa: F811
    settings.MEDIA_ROOT = tmp_path
    _, _, client = ambiente
    post = Post.objects.create(
        destino=_destino("instagram", estilo_das_imagens="quadrinhos"),
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.RASCUNHO,
        extras={"foco": "alcance"},
    )
    url = reverse("social:post_pela_outra_ia", args=[post.pk], urlconf=U)
    pagina = client.get(url).content.decode()
    assert "CLAREZA" in pagina and "LAMINAS:" in pagina and "quadrinhos" in pagina
    assert "Em um estudo com 2.300 adultos" in pagina  # o material do artigo
    assert "FOCO DO POST: ALCANCE" in pagina

    client.post(url, {"resposta": RESPOSTA_DO_POST})
    post.refresh_from_db()
    assert post.texto.startswith("Acorda cansado mesmo dormindo bem?")
    assert post.extras["escrito_por"] == "outra_ia" and post.extras["foco"] == "alcance"
    assert post.extras["hashtags"][:3] == ["Cansaco", "SaudeDaMulher", "Anemia"]
    assert post.extras["laminas"][0]["visual"].startswith("mulher olhando")
    assert any("numero que o artigo nao tem: 45" in a for a in post.avisos)
    assert len(post.imagens) == 5  # 4 laminas + a chamada final

    dados, _ = criacao.ler("nada aqui")
    assert not dados["gancho"] and not criacao.aplicar(post, "nada aqui")[0]


@pytest.mark.django_db
def test_proposta_pede_abordagem_e_formato(ambiente, monkeypatch):
    garantir_abordagens()
    escritos = []
    from apps.social import tasks

    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk, **_kw: escritos.append(pk))
    artigo = _artigo_publicado()
    tema = _tema(artigo)
    destino = _destino("instagram")
    texto = outra_ia.pedido(destino)
    assert "CLAREZA acima de tudo" in texto and "ideia que liga 1 artigos" in texto
    assert "abordagem | formato" in texto
    resposta = f"""ABORDAGENS:
- Antes e depois do processo: mostre a rotina baguncada e a rotina organizada, sem prometer \
resultado; uma lamina por passo.
PROPOSTAS:
- {outra_ia.codigo_do_tema(tema)} | Relatorio que demora dias? | uma ideia | engajamento | \
| Antes e depois do processo | enquete
FIM
"""
    leitura = outra_ia.ler(resposta)
    assert leitura.propostas[0].abordagem == "Antes e depois do processo"
    assert leitura.propostas[0].formato == "enquete"
    outra_ia.aplicar(leitura, destino, propostas={0}, abordagens={0}, eventos=set())
    post = Post.objects.get()
    assert post.abordagem == Abordagem.objects.get(nome="Antes e depois do processo")
    assert post.extras["formato"] == "enquete"


@pytest.mark.django_db
def test_proposta_em_video_com_estilo(ambiente, monkeypatch):
    from apps.social import redacao, tasks, video

    monkeypatch.setattr(tasks.escrever_post, "delay", lambda pk, **_kw: None)
    artigo = _artigo_publicado()
    tema = _tema(artigo)
    destino = _destino("instagram", estilos_permitidos=["quadrinhos", "apresentacao"])
    texto = outra_ia.pedido(destino)
    assert "carrossel, video ou enquete" in texto and "| estilo" in texto
    assert "'quadrinhos'" in texto and "'foto'" not in texto
    assert "devolva em 'estilo'" not in texto
    # o exemplo de evento segue o tema: clinica ve campanha de saude; o resto, data do setor
    consultoria = {"tema": "Processos, dados e medicao de resultados", "publico": "empresarios"}
    clinica = {"tema": "Clínica de dermatologia", "publico": "pacientes adultos"}
    assert "feira" in outra_ia.exemplos_de_eventos(consultoria, destino)
    assert "Outubro Rosa" in outra_ia.exemplos_de_eventos(clinica, destino)
    leitura = outra_ia.ler(
        f"PROPOSTAS:\n- {outra_ia.codigo_do_tema(tema)} | Cresceu e o lucro nao? | uma ideia "
        "| alcance | | | video | apresentacao\nFIM"
    )
    proposta = leitura.propostas[0]
    assert (proposta.formato, proposta.estilo) == ("video", "apresentacao")
    outra_ia.aplicar(leitura, destino, propostas={0}, abordagens=set(), eventos=set())
    post = Post.objects.get()
    assert post.extras["formato_pedido"] == "video"
    assert post.extras["estilo_pedido"] == "apresentacao"
    assert "vai como VIDEO" in video.instrucao(post)
    # quem escreve segue o pedido, mesmo sugerindo outra coisa
    _, extras = redacao.montar(
        post,
        {
            "gancho": "Oi",
            "texto": "Ola",
            "formato_sugerido": "carrossel",
            "estilo": "quadrinhos",
            "laminas": [{"titulo": "a", "texto": "b"}],
        },
    )
    assert extras["formato_sugerido"] == "video" and extras["estilo"] == "apresentacao"


@pytest.mark.django_db
def test_gerar_as_imagens_aqui(ambiente, modulo, settings, tmp_path, monkeypatch):  # noqa: F811
    settings.MEDIA_ROOT = tmp_path
    pedidos = []

    def executar(chave, variaveis, json_schema=None):
        pedidos.append((chave, variaveis))
        return json.dumps({"prompt": f"scene: {variaveis['cena']}"})

    def gerar(prompt, tamanho="896x1152"):
        buffer = io.BytesIO()
        Image.new("RGB", (896, 1152), "red").save(buffer, format="PNG")
        return buffer.getvalue()

    monkeypatch.setattr(fontes, "executar", executar)
    monkeypatch.setattr(fontes, "gerar_imagem", gerar)
    post = Post.objects.create(
        destino=_destino("instagram", estilo_das_imagens="ilustracao", chamada_final="Fim"),
        artigo_id=ARTIGO.id,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.RASCUNHO,
        extras={"laminas": LAMINAS_CLARAS[:2]},
    )
    assert identidade.gerar_imagens(post) == 3  # 2 laminas + a final
    assert pedidos[0][0] == "social_cena" and "pessoa bocejando" in pedidos[0][1]["cena"]
    assert "ilustracao" in pedidos[0][1]["estilo"]
    post.refresh_from_db()
    assert post.extras["imagem_propria"].endswith("propria.jpg")
    assert set(post.extras["fundos"]) == {"2", "3"}
    assert identidade.gerar_imagens(post) == 0  # ja tem tudo


@pytest.mark.django_db
def test_pedido_da_outra_ia_usa_o_material_ja_guardado(ambiente, modulo, monkeypatch):  # noqa: F811
    """Post ja escrito: a tela nao vetoriza o artigo de novo (numa maquina
    pequena, 200 frases dentro da requisicao davam 502)."""
    _, _, client = ambiente

    def sem_vetores(*_a, **_k):
        raise AssertionError("vetorizou dentro da requisicao")

    monkeypatch.setattr(fontes, "vetores", sem_vetores)
    post = Post.objects.create(
        destino=_destino("instagram"),
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.RASCUNHO,
        material={"achados": ["A anemia responde por 12,5% dos casos."], "secoes": ["O que fazer"]},
    )
    url = reverse("social:post_pela_outra_ia", args=[post.pk], urlconf=U)
    resposta = client.get(url)
    assert resposta.status_code == 200
    assert "A anemia responde por 12,5% dos casos." in resposta.content.decode()


@pytest.mark.django_db
def test_post_sem_material_abre_a_tela_sem_vetorizar(ambiente, modulo, monkeypatch):  # noqa: F811
    """Post ainda nao escrito: a tela sai pelo caminho rapido, e o material
    completo vai para a fila (uma vez so)."""
    from apps.social import tasks

    _, _, client = ambiente

    def sem_vetores(*_a, **_k):
        raise AssertionError("vetorizou dentro da requisicao")

    fila = []
    monkeypatch.setattr(fontes, "vetores", sem_vetores)
    monkeypatch.setattr(tasks.preparar_material, "delay", lambda pk, **_kw: fila.append(pk))
    post = Post.objects.create(
        destino=_destino("instagram"),
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.SUGERIDO,
    )
    url = reverse("social:post_pela_outra_ia", args=[post.pk], urlconf=U)
    assert client.get(url).status_code == 200
    assert "Em um estudo com 2.300 adultos" in client.get(url).content.decode()
    assert fila == [str(post.pk)]

    # Na fila (fora da requisicao), com vetores: o material completo fica no post.
    monkeypatch.setattr(fontes, "vetores", lambda t, consulta=False: [[1.0, 0.0] for _ in t])
    tasks.preparar_material(str(post.pk))
    post.refresh_from_db()
    assert post.material["achados"]


@pytest.mark.django_db
def test_pedido_leva_as_dores_com_as_falas_da_rede(ambiente, modulo, monkeypatch):  # noqa: F811
    pedidos = []

    def dores_para(texto, *, rede="", quantas=3):
        pedidos.append(rede)
        return ["acordar cansado — “Eu acordo cansada todo dia”"]

    monkeypatch.setattr(fontes, "dores_para", dores_para)
    post = Post.objects.create(
        destino=_destino("instagram"),
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        motivo=Post.Motivo.NOVO,
        situacao=Post.Situacao.SUGERIDO,
    )
    texto = criacao.pedido(post)
    assert "“Eu acordo cansada todo dia”" in texto and "abra o post pela dor" in texto
    assert pedidos == ["instagram"]
