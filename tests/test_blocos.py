"""Testes da divisao em blocos e da vetorizacao por paragrafo.

A decisao que estes testes protegem: **cada paragrafo vira um vetor, nunca o
bloco inteiro**. Um embedding e um vetor unico de tamanho fixo; quanto mais
longo e tematicamente misto o texto, mais esse vetor vira uma media que nao fica
perto de nenhuma pergunta. E o modelo tem janela de 512 tokens e trunca em
silencio acima disso — vetorizar uma secao seria vetorizar a primeira metade
dela achando que foi tudo.

Nao ha resumo por modelo em lugar nenhum daqui, de proposito: o trecho
recuperado e a evidencia que o revisor confere e que alimenta a redacao. Um
resumo gerado faria o revisor conferir contra um texto que o documento nunca
teve.
"""

from __future__ import annotations

import hashlib

import pytest
from django.core.files.base import ContentFile
from django_tenants.utils import schema_context

from apps.knowledge.blocos import (
    dividir_em_blocos,
    dividir_em_paragrafos,
    prefixo_de_contexto,
    preparar_blocos,
)
from apps.knowledge.models import Document, DocumentCategory
from apps.knowledge.services import blocos_marcados, indexar_blocos

ARTIGO = """# Exercicio e metabolismo

Souza, M., Lima, R.

## Resumo

Estudo de coorte acompanhou duzentos e quarenta adultos durante dezoito meses,
comparando um grupo com pratica regular a um grupo controle pareado por idade.

Os desfechos primarios foram glicemia de jejum, perfil lipidico e circunferencia
abdominal, medidos no inicio, aos seis meses e ao final do acompanhamento.

## Metodos

Os participantes foram alocados por sorteio simples, estratificado por sexo e
faixa etaria, com avaliacao cega dos desfechos laboratoriais em todas as visitas.

## Conclusao

A pratica regular associou-se a melhora consistente dos marcadores avaliados ao
longo de todo o periodo de acompanhamento observado no estudo de coorte.
"""


@pytest.fixture(autouse=True)
def embedding_falso(settings):
    settings.EMBEDDING_CLIENT = "apps.knowledge.embeddings.FakeEmbeddingClient"
    from apps.knowledge.embeddings import get_embedding_client

    get_embedding_client.cache_clear()
    yield
    get_embedding_client.cache_clear()


@pytest.fixture
def documento(tenant_factory):
    tenant = tenant_factory("blocos")
    with schema_context(tenant.schema_name):
        categoria = DocumentCategory.objects.create(name="Artigo", slug="artigo")
        yield Document.objects.create(
            category=categoria,
            title="Exercicio e metabolismo",
            authors="Souza, M.",
            year=2024,
            source_url="https://revista.exemplo.org/estudo",
            file_sha256=hashlib.sha256(b"estudo").hexdigest(),
            original_file=ContentFile(b"pdf", name="estudo.pdf"),
            license="cc_by",
            markdown_full=ARTIGO,
            status=Document.Status.PENDING_CURATION,
        )


# ---------------------------------------------------------------------------
# Blocos
# ---------------------------------------------------------------------------
def test_divide_nos_titulos_preservando_a_ordem():
    blocos = dividir_em_blocos(ARTIGO)

    titulos = [b.titulo for b in blocos]
    assert titulos == ["Exercicio e metabolismo", "Resumo", "Metodos", "Conclusao"]
    assert [b.nivel for b in blocos] == [1, 2, 2, 2]
    assert "duzentos e quarenta adultos" in blocos[1].conteudo


def test_titulo_pode_ser_em_qualquer_idioma():
    """Nao ha lista fixa de secoes aceitas.

    O titulo e o que o documento disser. Exigir "Abstract" ou "Resumo"
    excluiria artigo em qualquer outro idioma — e o produto e multilingue de
    proposito (ADR-0005).
    """
    blocos = dividir_em_blocos("## 结论\n\nO texto da conclusao em chines.\n")

    assert blocos[0].titulo == "结论"


def test_texto_sem_titulo_vira_um_bloco_unico():
    """E o que um PDF lido sem analise de layout produz.

    A tela mostra isso como um bloco disforme, e essa forma diz a verdade sobre
    a extracao melhor que qualquer selo.
    """
    blocos = dividir_em_blocos("Um texto corrido, sem estrutura nenhuma, como o pypdf devolve.")

    assert len(blocos) == 1
    assert blocos[0].titulo == ""


def test_markdown_vazio_nao_produz_bloco():
    assert dividir_em_blocos("") == []
    assert dividir_em_blocos("   \n\n  ") == []


# ---------------------------------------------------------------------------
# Paragrafos
# ---------------------------------------------------------------------------
def test_paragrafo_curto_e_juntado_ao_seguinte():
    """Um pedaco de duas palavras nao e um trecho.

    Vetorizado sozinho, so povoa o indice com ruido que compete com trecho de
    verdade. Juntar e melhor que descartar: o pedaco curto costuma ser a
    abertura do proximo.
    """
    texto = (
        "Introducao\n\n"
        "O primeiro paragrafo de verdade tem tamanho suficiente para valer um vetor proprio "
        "e trata de um assunto so, como se espera de um paragrafo bem escrito.\n\n"
        "O segundo paragrafo tambem tem corpo bastante para sustentar um vetor por conta "
        "propria sem depender de nenhum vizinho para fazer sentido."
    )

    partes = dividir_em_paragrafos(texto)

    assert len(partes) == 2
    assert partes[0].startswith("Introducao")


def test_um_paragrafo_por_vetor():
    blocos = dividir_em_blocos(ARTIGO)
    resumo = next(b for b in blocos if b.titulo == "Resumo")

    assert len(dividir_em_paragrafos(resumo.conteudo)) == 2


# ---------------------------------------------------------------------------
# Prefixo de contexto
# ---------------------------------------------------------------------------
def test_prefixo_junta_documento_e_bloco():
    """Um paragrafo sozinho nao diz de que estudo veio.

    "esse efeito foi observado em 240 adultos" e exatamente o que chega quando
    o trecho e recuperado isolado. O prefixo custa alguns tokens e nenhuma
    inferencia.
    """
    assert prefixo_de_contexto("Exercicio e metabolismo", "Conclusao") == (
        "Exercicio e metabolismo — Conclusao"
    )


def test_prefixo_ignora_parte_ausente():
    assert prefixo_de_contexto("Titulo", "") == "Titulo"
    assert prefixo_de_contexto("", "") == ""


@pytest.mark.django_db
def test_a_contagem_de_tokens_inclui_o_prefixo(documento):
    """E o texto INTEIRO que sera vetorizado que precisa caber, nao so o
    paragrafo — senao o prefixo empurraria o conteudo para fora da janela."""
    from apps.knowledge.blocos import montar_texto_vetorizavel
    from apps.knowledge.embeddings import get_embedding_client

    blocos = preparar_blocos(documento)
    bloco = next(b for b in blocos if b.titulo == "Conclusao")
    paragrafo = bloco.paragrafos[0]

    cliente = get_embedding_client()
    so_o_texto = cliente.contar_tokens(paragrafo.texto)
    com_prefixo = cliente.contar_tokens(
        montar_texto_vetorizavel(
            paragrafo.texto,
            titulo_do_documento=documento.title,
            titulo_do_bloco=bloco.titulo,
        )
    )

    assert paragrafo.tokens == com_prefixo
    assert com_prefixo > so_o_texto


@pytest.mark.django_db
def test_paragrafo_maior_que_a_janela_e_cortado_em_frases(documento, settings):
    """Deixar o modelo truncar seria pior: ele trunca SEM avisar.

    Metade do texto entraria no indice como se fosse o todo, e ninguem saberia
    qual metade.
    """
    settings.EMBEDDING_MAX_TOKENS = 30
    frases = " ".join(
        f"Esta e a frase numero {n} do paragrafo longo, com corpo suficiente." for n in range(12)
    )
    documento.markdown_full = f"## Discussao\n\n{frases}\n"
    documento.save()

    bloco = preparar_blocos(documento)[0]

    assert len(bloco.paragrafos) > 1
    assert bloco.tem_trecho_partido is True
    assert all(p.tokens <= 30 for p in bloco.paragrafos)


# ---------------------------------------------------------------------------
# Indexacao
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_indexa_apenas_os_blocos_marcados(documento):
    blocos = preparar_blocos(documento)
    conclusao = next(b for b in blocos if b.titulo == "Conclusao")

    total = indexar_blocos(document=documento, blocos_marcados={conclusao.ordem})

    assert total == len(conclusao.paragrafos)
    assert set(documento.chunks.values_list("heading", flat=True)) == {"Conclusao"}


@pytest.mark.django_db
def test_salvar_substitui_o_indice_inteiro(documento):
    """O conjunto marcado na tela e a verdade sobre o documento.

    Acrescentar deixaria no indice trecho de bloco que a pessoa acabou de
    desmarcar, e ela nao teria como saber.
    """
    blocos = preparar_blocos(documento)
    resumo = next(b for b in blocos if b.titulo == "Resumo")
    metodos = next(b for b in blocos if b.titulo == "Metodos")

    indexar_blocos(document=documento, blocos_marcados={resumo.ordem, metodos.ordem})
    assert blocos_marcados(documento) == {resumo.ordem, metodos.ordem}

    indexar_blocos(document=documento, blocos_marcados={resumo.ordem})

    assert blocos_marcados(documento) == {resumo.ordem}
    assert not documento.chunks.filter(heading="Metodos").exists()


@pytest.mark.django_db
def test_o_conteudo_guardado_e_o_paragrafo_puro(documento):
    """O `content` e o que o revisor le na tela de revisao do artigo.

    O prefixo entra na vetorizacao, mas nao no texto exibido: mostrar
    "Titulo — Conclusao" colado no comeco de toda citacao seria ruido.
    """
    blocos = preparar_blocos(documento)
    conclusao = next(b for b in blocos if b.titulo == "Conclusao")

    indexar_blocos(document=documento, blocos_marcados={conclusao.ordem})

    chunk = documento.chunks.first()
    assert not chunk.content.startswith(documento.title)
    assert "pratica regular" in chunk.content


@pytest.mark.django_db
def test_trecho_carrega_a_procedencia_para_a_citacao(documento):
    """A citacao sobrevive a edicoes posteriores do documento porque os campos
    sao copiados na gravacao."""
    blocos = preparar_blocos(documento)
    indexar_blocos(document=documento, blocos_marcados={blocos[1].ordem})

    chunk = documento.chunks.first()
    assert chunk.source_url == "https://revista.exemplo.org/estudo"
    assert chunk.source_authors == "Souza, M."
    assert chunk.source_year == 2024
    assert chunk.heading == "Resumo"


@pytest.mark.django_db
def test_desmarcar_tudo_esvazia_o_indice(documento):
    blocos = preparar_blocos(documento)
    indexar_blocos(document=documento, blocos_marcados={blocos[1].ordem})
    assert documento.chunks.exists()

    assert indexar_blocos(document=documento, blocos_marcados=set()) == 0
    assert not documento.chunks.exists()


@pytest.mark.django_db
def test_citacao_publicada_sobrevive_a_reindexacao(documento):
    """Substituir os chunks nao pode apagar a referencia do que ja foi ao ar.

    A citacao aponta com SET_NULL e guarda titulo e URL copiados — e por isso
    que "substituir tudo ao salvar" e seguro.
    """
    from apps.content.models import Article

    blocos = preparar_blocos(documento)
    indexar_blocos(document=documento, blocos_marcados={blocos[1].ordem})
    chunk = documento.chunks.first()

    artigo = Article.objects.create(title="Publicado", status=Article.Status.PUBLISHED)
    citacao = artigo.citations.create(
        super_chunk=chunk,
        rank=1,
        distance=0.05,
        used_as_primary=True,
        source_title=documento.title,
        source_url=documento.source_url,
    )

    indexar_blocos(document=documento, blocos_marcados=set())

    citacao.refresh_from_db()
    assert citacao.super_chunk is None
    assert citacao.source_url == "https://revista.exemplo.org/estudo"


@pytest.mark.django_db
def test_a_tela_avisa_antes_de_apagar_o_texto_integral(documento, client):
    """A licenca padrao e "Desconhecido", e concluir com ela e irreversivel.

    Sem o aviso, o caminho mais provavel — aceitar o default e clicar em
    concluir — apagaria o texto integral sem a pessoa saber que perdeu a
    possibilidade de remarcar blocos.
    """
    from django.conf import settings as configuracao
    from django.urls import reverse

    from apps.accounts.models import Tenant, TenantMembership, User

    Document.objects.filter(pk=documento.pk).update(license=Document.License.UNKNOWN)
    documento.refresh_from_db()

    tenant = Tenant.objects.get(schema_name="blocos")
    usuario = User.objects.create_user(
        email="curador@exemplo.com", password="uma-senha-longa-de-teste", full_name="Curador"
    )
    TenantMembership.objects.create(tenant=tenant, user=usuario, is_active=True)

    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{configuracao.ROOT_DOMAIN}"
    corpo = client.get(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants")
    ).content.decode()

    assert "apagar o texto integral" in corpo


@pytest.mark.django_db
def test_licenca_que_permite_guardar_nao_mostra_o_aviso(documento, client):
    """O aviso e sobre perda irreversivel; onde nao ha perda, ele so assusta."""
    from django.conf import settings as configuracao
    from django.urls import reverse

    from apps.accounts.models import Tenant, TenantMembership, User

    assert documento.pode_guardar_texto_integral is True

    tenant = Tenant.objects.get(schema_name="blocos")
    usuario = User.objects.create_user(
        email="outro@exemplo.com", password="uma-senha-longa-de-teste", full_name="Outro"
    )
    TenantMembership.objects.create(tenant=tenant, user=usuario, is_active=True)

    client.force_login(usuario)
    client.defaults["HTTP_HOST"] = f"{tenant.slug}.{configuracao.ROOT_DOMAIN}"
    corpo = client.get(
        reverse("knowledge:curar", args=[documento.pk], urlconf="core.urls_tenants")
    ).content.decode()

    assert "apagar o texto integral" not in corpo


# ---------------------------------------------------------------------------
# Texto sem analise de layout
# ---------------------------------------------------------------------------
def test_texto_puro_nao_e_lido_como_markdown():
    """O caso que motivou a separacao, tirado de um artigo real da Springer.

    O pypdf decodificou o simbolo de copyright como `#`, e a linha de direitos
    autorais virou titulo de secao: o documento foi partido ali e o nome da
    editora foi eleito titulo da obra. Nada disso levantou erro — o texto
    parecia estruturado sem estar.
    """
    texto = (
        "Fail-safe and safe-to-fail adaptation\n"
        "Yeowon Kim & Daniel A. Eisenberg\n"
        "# Springer Science+Business Media B.V . 2017\n"
        "Abstract As climate change affects precipitation patterns, urban\n"
        "infrastructure may become more vulnerable to flooding.\n"
    )

    como_markdown = dividir_em_blocos(texto)
    assert any("Springer" in b.titulo for b in como_markdown), "o defeito antigo sumiu do teste"

    blocos = dividir_em_blocos(texto, e_markdown=False)
    assert not any("Springer" in b.titulo for b in blocos)


def test_secoes_numeradas_saem_do_texto_corrido():
    texto = (
        "Titulo do artigo\n"
        "Autor Um & Autor Dois\n"
        "1 Introduction\n"
        "As cidades crescem e a infraestrutura envelhece junto com elas, o que\n"
        "muda o risco de alagamento em toda a malha viaria da regiao estudada.\n"
        "1.1 Urban growth\n"
        "A populacao urbana dobrou no periodo analisado, segundo os dados do\n"
        "censo, e a area impermeavel acompanhou esse crescimento de perto.\n"
        "2 Methodology\n"
        "Foram simulados eventos de precipitacao com o modelo hidrologico, para\n"
        "cada um dos cenarios climaticos considerados neste trabalho.\n"
    )

    titulos = [b.titulo for b in dividir_em_blocos(texto, e_markdown=False)]
    assert "1.1 Urban growth" in titulos
    assert "2 Methodology" in titulos


def test_cabecalho_de_pagina_nao_vira_secao():
    """ "400 Climatic Change (2017) 145:397-412" tem a forma de uma secao: linha
    curta comecando por numero. O que a denuncia e repetir em toda pagina."""
    corpo = (
        "Texto corrido da secao, com tamanho suficiente para nao ser confundido\n"
        "com um titulo solto de tabela ou legenda de figura qualquer.\n"
    )
    texto = "Titulo\n1 Introduction\n" + corpo
    for _ in range(5):
        texto += "400 Climatic Change (2017) 145:397-412\n" + corpo

    titulos = [b.titulo for b in dividir_em_blocos(texto, e_markdown=False)]
    assert titulos == ["", "1 Introduction"]


def test_afiliacao_de_autor_nao_vira_secao():
    """ "1 School of Sustainability, Arizona State University, Tempe, AZ, USA"
    comeca com 1 e e curta. Virgula em serie e o que a separa de um titulo."""
    texto = (
        "Titulo do artigo\n"
        "1 School of Sustainability, Arizona State University, Tempe, AZ, USA\n"
        "2 Civil Engineering, Arizona State University, Tempe, AZ, USA\n"
        "1 Introduction\n"
        "O texto da introducao vem aqui, com comprimento suficiente para passar\n"
        "pela checagem de prosa que separa secao de celula de tabela.\n"
    )

    titulos = [b.titulo for b in dividir_em_blocos(texto, e_markdown=False)]
    assert "1 Introduction" in titulos
    assert not any("School of Sustainability" in t for t in titulos)


def test_celula_de_tabela_nao_vira_secao():
    """ "5 Discouraging" e "5 Conclusion" tem a mesma forma. So o que vem depois
    as distingue: uma e seguida de fragmento curto, a outra de paragrafo."""
    texto = (
        "Titulo\n"
        "1 Adaptation strategy decision-making\n"
        "A combinacao de revisao de literatura e avaliacao de vulnerabilidade\n"
        "mostra como as recomendacoes mudam conforme a perspectiva adotada.\n"
        "2 Discouraging\n"
        "subsidence\n"
        "RWIS Discouraging\n"
        "subsidence\n"
        "2 Conclusion\n"
        "Dada a vulnerabilidade especifica de cada infraestrutura, e possivel\n"
        "priorizar recomendacoes espacialmente explicitas para a cidade toda.\n"
    )

    titulos = [b.titulo for b in dividir_em_blocos(texto, e_markdown=False)]
    assert "2 Conclusion" in titulos
    assert "2 Discouraging" not in titulos


def test_abstract_sai_do_cabecalho():
    """O resumo e o trecho de maior valor do artigo e vem colado ao rotulo, na
    mesma linha. Sem separa-lo ele fica no meio de autores e filiacao."""
    texto = (
        "Titulo do artigo sobre alagamento urbano\n"
        "Autor Um & Autor Dois\n"
        "Abstract As mudancas climaticas afetam os padroes de precipitacao e a\n"
        "infraestrutura urbana pode ficar mais vulneravel a alagamentos.\n"
        "1 Introduction\n"
        "As cidades crescem e a infraestrutura envelhece junto com elas, o que\n"
        "muda o risco de alagamento em toda a malha viaria estudada.\n"
    )

    blocos = {b.titulo: b.conteudo for b in dividir_em_blocos(texto, e_markdown=False)}
    assert "Abstract" in blocos
    assert blocos["Abstract"].startswith("As mudancas climaticas")
    assert "Autor Um" not in blocos["Abstract"]


def test_referencias_ficam_num_bloco_proprio():
    """Sao o maior bloco do artigo e o de menor valor para o indice. Juntas na
    conclusao, quem cura marca as duas de uma vez."""
    texto = (
        "Titulo\n"
        "1 Introduction\n"
        "O texto da introducao vem aqui com comprimento suficiente para passar\n"
        "pela checagem de prosa e ser reconhecido como secao de verdade.\n"
        "References\n"
        "Ahern J (2011) From fail-safe to safe-to-fail. Landsc Urban Plan\n"
        "IPCC (2014) Climate change 2014: impacts, adaptation and vulnerability\n"
    )

    titulos = [b.titulo for b in dividir_em_blocos(texto, e_markdown=False)]
    assert "References" in titulos


def test_sem_secao_reconhecivel_vira_um_bloco_so():
    """Nao inventar divisao onde nao ha estrutura: um bloco disforme e a
    verdade sobre o documento, e quem cura precisa ver isso."""
    texto = "Um paragrafo qualquer.\n\nOutro paragrafo qualquer, tambem sem numeracao."

    blocos = dividir_em_blocos(texto, e_markdown=False)
    assert len(blocos) == 1
    assert blocos[0].titulo == ""
