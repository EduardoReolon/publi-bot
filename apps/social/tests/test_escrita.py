"""Do artigo ao post: material por algoritmo, escrita pelo modelo, conferencia
por algoritmo (numero que o artigo nao tem volta para o modelo corrigir)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.social import fontes, material, redacao
from apps.social.abordagens import garantir_abordagens
from apps.social.models import Abordagem, Destino, Post

ARTIGO = fontes.ArtigoParaRedes(
    id="11111111-1111-1111-1111-111111111111",
    titulo="Cansaco que nao passa: quando investigar",
    url="https://site.exemplo.org/cansaco/",
    resumo="Quando o cansaco merece exame.",
    palavra_chave="cansaco",
    secoes=[("3 sinais de alerta", "..."), ("O que fazer", "...")],
    frases=[
        "Voce acorda cansado mesmo depois de dormir oito horas por noite.",
        "Em um estudo com 2.300 adultos, 38% relataram cansaco persistente.",
        "O exame de sangue simples esclarece a maior parte dos casos.",
        "A anemia responde por 12,5% dos casos de cansaco em mulheres.",
    ],
    texto=(
        "Voce acorda cansado mesmo depois de dormir oito horas por noite. Em um estudo com "
        "2.300 adultos, 38% relataram cansaco persistente. A anemia responde por 12,5% dos "
        "casos de cansaco em mulheres."
    ),
    publicado_em=timezone.now() - timedelta(days=2),
    remote_id="r1",
)


class VetoresFalsos:
    """Proximidade por palavras em comum: o bastante para a ordem fazer sentido."""

    def __call__(self, textos, consulta=False):
        vocabulario = ["cansado", "cansaco", "dormir", "estudo", "anemia", "exame", "%"]
        return [[float(p in t.lower()) + 0.01 for p in vocabulario] for t in textos]


@pytest.fixture
def modulo(monkeypatch):
    """O modulo de redes com o nucleo simulado na porta (fontes.py)."""
    chamadas = []
    respostas = []
    monkeypatch.setattr(fontes, "artigo", lambda pk: ARTIGO)
    monkeypatch.setattr(
        fontes,
        "negocio",
        lambda: {"dores": ["acordar cansado todo dia"], "regioes": ["Curitiba"], "idioma": "pt-BR"},
    )
    monkeypatch.setattr(fontes, "vetores", VetoresFalsos())
    monkeypatch.setattr(
        fontes, "termos_a_evitar", lambda texto: ['"cura"'] if "cura" in texto else []
    )
    monkeypatch.setattr(
        fontes, "endereco_publico", lambda caminho: f"https://painel.exemplo.org{caminho}"
    )

    def executar(chave, variaveis, json_schema=None):
        chamadas.append((chave, variaveis))
        return json.dumps(respostas.pop(0))

    monkeypatch.setattr(fontes, "executar", executar)
    return chamadas, respostas


def _destino(rede="linkedin", **campos):
    return Destino.objects.create(rede=rede, nome=f"Conta {rede}", **campos)


def test_material_traz_identificacao_e_achados_do_artigo(modulo):
    mat = material.montar(ARTIGO, publico="adultos cansados", negocio=fontes.negocio())
    assert mat["identificacao"][0].startswith("Voce acorda cansado")
    assert any("38%" in f for f in mat["achados"])
    assert mat["dores"] == ["acordar cansado todo dia"]
    assert mat["secoes"] == ["3 sinais de alerta", "O que fazer"]


def test_numero_que_o_artigo_nao_tem_e_apontado():
    texto = ARTIGO.texto
    assert material.numeros_fora_do_artigo("38% dos adultos e 12,5% das mulheres", texto) == []
    assert material.numeros_fora_do_artigo("2300 adultos", texto) == []  # mesmo numero, sem ponto
    assert material.numeros_fora_do_artigo("45% das pessoas, 3 sinais", texto) == ["45"]


@pytest.mark.django_db
def test_linkedin_link_no_primeiro_comentario_e_numero_errado_volta_ao_modelo(ambiente, modulo):
    chamadas, respostas = modulo
    garantir_abordagens()
    abordagem = Abordagem.objects.get(nome="Nossa, que incrivel")
    post = Post.objects.create(
        destino=_destino(hashtags_fixas="#saude"),
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        artigo_url=ARTIGO.url,
        abordagem=abordagem,
        motivo=Post.Motivo.NOVO,
    )
    respostas += [
        {"gancho": "45% dos adultos vivem cansados.", "texto": "Veja.", "hashtags": ["cansaco"]},
        {
            "gancho": "38% dos adultos vivem cansados, e quase ninguem investiga.",
            "texto": "O exame simples resolve a maior parte. https://x.com/nao",
            "hashtags": ["#cansaco", "medicina", "saude"],
            "primeiro_comentario": "Os sinais, no artigo:",
        },
    ]

    redacao.escrever(post)

    post.refresh_from_db()
    assert len(chamadas) == 2
    assert "Nossa, que incrivel" in chamadas[0][1]["abordagem"]
    assert "numero que o artigo nao tem: 45" in chamadas[1][1]["ajuste"]
    assert post.situacao == Post.Situacao.RASCUNHO and post.avisos == []
    assert post.texto.startswith("38% dos adultos")
    assert "https://x.com" not in post.texto
    assert post.texto.rstrip().endswith("#cansaco #medicina #saude")
    assert post.extras["primeiro_comentario"] == "Os sinais, no artigo:"
    assert post.extras["link"] == f"https://painel.exemplo.org/redes/r/{post.chave_publica}/"
    assert post.material["achados"]


@pytest.mark.django_db
def test_instagram_laminas_e_link_na_bio(ambiente, modulo, settings, tmp_path):
    _chamadas, respostas = modulo
    settings.MEDIA_ROOT = tmp_path
    post = Post.objects.create(
        destino=_destino("instagram", chamada_final="Leia tudo — link na bio"),
        artigo_id=ARTIGO.id,
        artigo_titulo=ARTIGO.titulo,
        artigo_url=ARTIGO.url,
        motivo=Post.Motivo.NOVO,
    )
    respostas.append(
        {
            "gancho": "Acorda cansado mesmo dormindo bem?",
            "texto": "Tem explicacao.",
            "hashtags": ["a", "b", "c", "d", "e", "f"],
            "laminas": [
                {"titulo": "Acorda cansado?", "texto": ""},
                {"titulo": "38% dos adultos", "texto": "relatam cansaco que nao passa"},
                {"titulo": "Anemia", "texto": "12,5% dos casos em mulheres"},
                {"titulo": "Exame simples", "texto": "esclarece a maior parte"},
                {"titulo": "Link na bio", "texto": ""},
            ],
        }
    )

    redacao.escrever(post)

    post.refresh_from_db()
    assert "Link na bio." in post.texto
    assert post.extras["hashtags"] == ["a", "b", "c", "d", "e"]  # o maximo do Instagram
    assert len(post.extras["laminas"]) == 5
    # A ultima lamina vira a chamada da conta; uma imagem por lamina.
    assert len(post.imagens) == 5
    assert post.imagens[0]["url"].endswith(f"/redes/m/{post.chave_publica}/1.png")


@pytest.mark.django_db
def test_conta_automatica_aprova_sozinho_quando_nada_falta(ambiente, modulo):
    _chamadas, respostas = modulo
    destino = _destino("gmn", aprovacao=Destino.Aprovacao.AUTOMATICO)
    post = Post.objects.create(
        destino=destino,
        artigo_id=ARTIGO.id,
        artigo_titulo="x",
        artigo_url=ARTIGO.url,
        motivo=Post.Motivo.NOVO,
    )
    respostas.append({"gancho": "Cansaco que nao passa em Curitiba?", "texto": "Saiba mais."})

    redacao.escrever(post)

    post.refresh_from_db()
    assert post.situacao == Post.Situacao.APROVADO and post.agendado_para is not None
    assert post.extras["link"].endswith(f"/redes/r/{post.chave_publica}/")


def test_link_rastreado_leva_a_origem_marcada():
    post = Post(
        destino=Destino(rede="linkedin", nome="x"),
        artigo_url="https://site.exemplo.org/cansaco/?ref=1",
        artigo_titulo="Cansaço que não passa",
        abordagem=Abordagem(nome="E o meu caso"),
    )
    assert redacao.destino_do_clique(post) == (
        "https://site.exemplo.org/cansaco/?ref=1&utm_source=linkedin&utm_medium=social"
        "&utm_campaign=cansaco-que-nao-passa&utm_content=e-o-meu-caso"
    )
