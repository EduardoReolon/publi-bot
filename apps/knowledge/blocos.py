"""Divide o Markdown convertido em blocos, e blocos em paragrafos.

Duas divisoes com propositos diferentes.

**Blocos** sao para a pessoa. Um bloco e um titulo e o texto ate o proximo
titulo — a estrutura que o Docling reconhece na pagina. E o que a curadoria
marca: "use a discussao e a conclusao, ignore os metodos". Nao ha lista fixa de
titulos aceitos: o titulo e o que o documento disser, em qualquer idioma.

**Paragrafos** sao para o indice. Cada paragrafo marcado vira um vetor, e nao o
bloco inteiro. O motivo e mecanico: um embedding e UM vetor de tamanho fixo, e
quanto mais longo e tematicamente misto o texto, mais esse vetor vira uma media
que nao fica perto de nada em particular. Alem disso o `multilingual-e5-large`
tem janela de 512 tokens e **trunca em silencio** acima disso — vetorizar uma
secao inteira seria vetorizar a primeira metade dela achando que foi tudo.

O preco conhecido de dividir por paragrafo e a perda de contexto: "esse efeito",
"o grupo", sem dizer de que estudo. Por isso cada paragrafo e vetorizado com um
prefixo de contexto — titulo do documento e titulo do bloco — que custa alguns
tokens e nenhuma inferencia.

Nao ha resumo por modelo aqui, de proposito. O trecho recuperado nao e so chave
de busca: e a evidencia que o revisor confere e que vai para o modelo que
escreve. Um resumo gerado tornaria o artigo fundamentado numa parafrase, e o
revisor estaria conferindo contra um texto que o documento nunca teve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.conf import settings

# Titulo ATX (`## Discussao`). O Docling exporta assim; o extrator local nao
# exporta titulo nenhum, e por isso um PDF lido sem analise de layout aparece
# na tela como um unico bloco disforme — o que e a verdade sobre ele.
PADRAO_DE_TITULO = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

# Paragrafos separados por linha em branco.
PADRAO_DE_PARAGRAFO = re.compile(r"\n\s*\n")

# Abaixo disso um "paragrafo" e uma legenda solta, um numero de pagina ou o
# resto de uma quebra — vetorizar isso so povoa o indice com ruido que compete
# com trecho de verdade.
MINIMO_DE_CARACTERES = 120

# Fim de frase seguido de espaco e maiuscula. Grosseiro de proposito: serve
# apenas para achar um ponto de corte razoavel num paragrafo longo demais, e
# nao para segmentar texto com precisao.
PADRAO_DE_FRASE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u00c0-\u00dc])")


@dataclass
class Paragrafo:
    texto: str
    tokens: int = 0
    # Verdadeiro quando o paragrafo original era grande demais e precisou ser
    # cortado em frases. Nao e erro — e informacao para a curadoria saber que
    # aquele trecho chega ao indice partido.
    partido: bool = False


@dataclass
class Bloco:
    ordem: int
    nivel: int
    titulo: str
    conteudo: str
    paragrafos: list[Paragrafo] = field(default_factory=list)

    @property
    def caracteres(self) -> int:
        return len(self.conteudo)

    @property
    def tem_trecho_partido(self) -> bool:
        return any(p.partido for p in self.paragrafos)

    @property
    def total_de_trechos(self) -> int:
        return len(self.paragrafos)


def dividir_em_blocos(markdown: str) -> list[Bloco]:
    """Quebra o Markdown nos titulos, preservando a ordem do documento.

    O texto antes do primeiro titulo vira um bloco sem titulo — e onde costuma
    ficar o cabecalho do artigo, com autores e filiacao.
    """
    if not markdown or not markdown.strip():
        return []

    blocos: list[Bloco] = []
    titulo_atual = ""
    nivel_atual = 0
    linhas_atuais: list[str] = []

    def fechar() -> None:
        conteudo = "\n".join(linhas_atuais).strip()
        if conteudo or titulo_atual:
            blocos.append(
                Bloco(
                    ordem=len(blocos),
                    nivel=nivel_atual,
                    titulo=titulo_atual,
                    conteudo=conteudo,
                )
            )

    for linha in markdown.splitlines():
        achado = PADRAO_DE_TITULO.match(linha)
        if achado is None:
            linhas_atuais.append(linha)
            continue
        fechar()
        nivel_atual = len(achado.group(1))
        titulo_atual = achado.group(2).strip()
        linhas_atuais = []

    fechar()
    return blocos


def dividir_em_paragrafos(conteudo: str) -> list[str]:
    """Separa por linha em branco, juntando os pedacos curtos demais.

    Juntar com o seguinte, e nao descartar, porque um paragrafo curto costuma
    ser a abertura do proximo — descarta-lo perderia a frase que da o assunto.
    """
    brutos = [p.strip() for p in PADRAO_DE_PARAGRAFO.split(conteudo or "")]
    brutos = [p for p in brutos if p]

    juntados: list[str] = []
    pendente = ""
    for paragrafo in brutos:
        candidato = f"{pendente}\n\n{paragrafo}" if pendente else paragrafo
        if len(candidato) < MINIMO_DE_CARACTERES:
            pendente = candidato
            continue
        juntados.append(candidato)
        pendente = ""

    if pendente:
        # Sobrou um pedaco curto no fim: cola no ultimo, ou vai sozinho se for
        # o unico que existe.
        if juntados:
            juntados[-1] = f"{juntados[-1]}\n\n{pendente}"
        else:
            juntados.append(pendente)

    return juntados


def prefixo_de_contexto(titulo_do_documento: str, titulo_do_bloco: str) -> str:
    """O cabecalho que acompanha cada paragrafo na vetorizacao.

    Sem ele, "esse efeito foi observado em 240 adultos" nao diz de que estudo
    nem de que parte dele — e um paragrafo recuperado sozinho e exatamente
    assim que chega.
    """
    partes = [p.strip() for p in (titulo_do_documento, titulo_do_bloco) if p and p.strip()]
    return " — ".join(partes)


def montar_texto_vetorizavel(
    paragrafo: str, *, titulo_do_documento: str, titulo_do_bloco: str
) -> str:
    prefixo = prefixo_de_contexto(titulo_do_documento, titulo_do_bloco)
    return f"{prefixo}\n\n{paragrafo}" if prefixo else paragrafo


def partir_no_limite(paragrafo: str, *, prefixo: str, cliente, limite: int) -> list[str]:
    """Corta em frases ate cada pedaco caber no limite do modelo.

    So acontece com paragrafo longo sem quebra de linha. A alternativa seria
    deixar o modelo truncar, e ele trunca **sem avisar**: metade do texto
    entraria no indice como se fosse o todo, e ninguem saberia qual metade.

    O corte e por frase, e nunca por resumo. O trecho recuperado e a evidencia
    que o revisor confere; um resumo gerado faria ele conferir contra um texto
    que o documento nunca teve.
    """
    partes: list[str] = []
    atual = ""

    for frase in PADRAO_DE_FRASE.split(paragrafo):
        candidato = f"{atual} {frase}".strip() if atual else frase
        completo = f"{prefixo}\n\n{candidato}" if prefixo else candidato
        if cliente.contar_tokens(completo) > limite and atual:
            partes.append(atual)
            atual = frase
        else:
            atual = candidato

    if atual:
        partes.append(atual)

    return partes or [paragrafo]


def preparar_blocos(document) -> list[Bloco]:
    """Blocos do documento, com os trechos que cada um produziria no indice.

    A contagem usa o tokenizador REAL do modelo e inclui o prefixo de contexto:
    e o texto inteiro que sera vetorizado que precisa caber, nao so o paragrafo.
    """
    from apps.knowledge.embeddings import get_embedding_client

    cliente = get_embedding_client()
    limite = settings.EMBEDDING_MAX_TOKENS

    blocos = dividir_em_blocos(document.markdown_full or "")
    for bloco in blocos:
        prefixo = prefixo_de_contexto(document.title or "", bloco.titulo)
        for texto in dividir_em_paragrafos(bloco.conteudo):
            completo = f"{prefixo}\n\n{texto}" if prefixo else texto
            tokens = cliente.contar_tokens(completo)

            if tokens <= limite:
                bloco.paragrafos.append(Paragrafo(texto=texto, tokens=tokens))
                continue

            for pedaco in partir_no_limite(texto, prefixo=prefixo, cliente=cliente, limite=limite):
                inteiro = f"{prefixo}\n\n{pedaco}" if prefixo else pedaco
                bloco.paragrafos.append(
                    Paragrafo(
                        texto=pedaco,
                        tokens=cliente.contar_tokens(inteiro),
                        partido=True,
                    )
                )

    return blocos
