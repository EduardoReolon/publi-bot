"""Divide o texto convertido em blocos, e blocos em paragrafos.

Duas divisoes com propositos diferentes.

**Blocos** sao para a pessoa. Um bloco e um titulo e o texto ate o proximo
titulo — a estrutura que o Docling reconhece na pagina. E o que a curadoria
marca: "use a discussao e a conclusao, ignore os metodos". Nao ha lista fixa de
titulos aceitos: o titulo e o que o documento disser, em qualquer idioma.

Ha **dois caminhos de divisao**, e confundi-los sai caro. Do Docling vem
Markdown, com cabecalho `#`. Do extrator local vem texto puro, onde `#` nao
significa nada — num artigo da Springer o simbolo (c) chegou como `#` e, lido
como Markdown, partiu o documento na linha de copyright e elegeu a editora como
titulo da obra, sem erro nenhum no caminho. Para o texto puro a estrutura se
recupera da numeracao das secoes (`1 Introduction`, `2.2.1 ...`), que faz parte
do texto e sobrevive a extracao; quando nao ha numeracao, o documento vira um
bloco so — que e a verdade sobre ele.

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

# Titulo ATX (`## Discussao`). So o Docling exporta Markdown; o extrator local
# devolve texto puro, e aplicar este padrao nele e um erro caro — ver
# `dividir_em_blocos`.
PADRAO_DE_TITULO = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

# Secao numerada de artigo: "1 Introduction", "2.2.1 Roadway vulnerability".
# E o unico sinal de estrutura que sobrevive a extracao sem analise de layout,
# porque o numero faz parte do texto e nao da formatacao.
PADRAO_DE_SECAO = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(\S.*)$")

# Um titulo de secao e uma linha curta. Acima disto e paragrafo que por acaso
# comeca com numero.
MAXIMO_DE_TITULO_DE_SECAO = 90

# Cabecalho de pagina ("400 Climatic Change (2017) 145:397-412") tambem comeca
# com numero e tambem e curto. O que o distingue e repetir: um titulo de secao
# aparece uma vez, o cabecalho aparece em toda pagina.
MAXIMO_DE_REPETICOES = 2

# Um titulo de secao e seguido de prosa. Celula de tabela ("4 Flood storage")
# e seguida de outro fragmento curto — foi assim que se separou uma da outra
# num artigo real, e nao por suposicao.
MINIMO_DE_PROSA_SEGUINTE = 60

# Lista de afiliacoes ("1 School of Sustainability, Arizona State University,
# Tempe, AZ, USA") tem a mesma forma de uma secao numerada. Virgula em serie e
# o que a denuncia: titulo de secao quase nunca tem duas.
MAXIMO_DE_VIRGULAS = 1

# Secoes finais sem numero. Nao entram por completude: separar as referencias
# importa porque elas sao o maior bloco de texto do artigo e o de menor valor
# para o indice — misturadas na conclusao, quem cura marca as duas juntas.
# Secoes de abertura que aparecem GRUDADAS ao proprio texto ("Abstract As
# climate change affects..."), e nao numa linha propria. Separa-las importa
# porque o resumo e o trecho de maior valor do artigo e sem isto ele fica
# misturado com titulo, autores, filiacao e cabecalho da revista.
PADRAO_DE_ABERTURA = re.compile(
    r"^(Abstract|Resumo|Summary|Keywords|Key words|Palavras-chave)\b[\s:.\u2013\u2014-]*(.*)$",
    re.IGNORECASE,
)

# So no comeco do documento: "Abstract" tambem aparece dentro das referencias.
# O piso em linhas existe porque a fracao sozinha nao cobre documento curto —
# num texto de oito linhas, 25% para antes do resumo.
FRACAO_INICIAL_DO_DOCUMENTO = 0.25
MINIMO_DE_LINHAS_INICIAIS = 40

SECOES_FINAIS = {
    "references",
    "bibliography",
    "acknowledgements",
    "acknowledgments",
    "referencias",
    "referências",
    "agradecimentos",
    "bibliografia",
    "appendix",
    "anexo",
    "apendice",
    "apêndice",
}

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


def _numero_da_secao(rotulo: str) -> tuple[int, ...]:
    return tuple(int(parte) for parte in rotulo.split("."))


def _sequencia_faz_sentido(anterior: tuple[int, ...], atual: tuple[int, ...]) -> bool:
    """A numeracao progride como a de um artigo, e nao por acaso.

    E esta regra que separa "2 Methodology" de "400 Climatic Change (2017)":
    ambos sao linha curta comecando por numero, mas so um continua a contagem
    do anterior. Sem ela, todo cabecalho de pagina viraria uma secao.
    """
    if not anterior:
        # A primeira secao de um artigo e 1 (ou 1.x). Aceitar qualquer numero
        # aqui deixaria o numero de pagina abrir a contagem.
        return atual[0] == 1

    # Mesmo nivel: continua ou avanca um.
    if len(atual) == len(anterior):
        return atual[:-1] == anterior[:-1] and atual[-1] in (anterior[-1], anterior[-1] + 1)

    # Desce um nivel: "2" -> "2.1". Nao se exige comecar em 1: se "2.1" foi
    # recusado por outro criterio, exigir isso derrubaria "2.2" tambem, e o
    # erro se propagaria ate o fim do documento. O prefixo igual ja e a
    # restricao forte.
    if len(atual) == len(anterior) + 1:
        return atual[:-1] == anterior

    # Sobe um ou mais niveis: "2.2.1" -> "3". O prefixo precisa continuar a
    # contagem daquele nivel.
    if len(atual) < len(anterior):
        prefixo = anterior[: len(atual)]
        return atual[:-1] == prefixo[:-1] and atual[-1] == prefixo[-1] + 1

    return False


def _vem_prosa_depois(linhas: list[str], indice: int) -> bool:
    """Se a proxima linha com conteudo e texto corrido, ou outro titulo.

    E o que separa "5 Conclusion", seguido de paragrafo, de "5 Discouraging",
    que e celula de tabela e vem seguida de "subsidence". As duas tem a mesma
    forma; so o que vem depois as distingue.
    """
    vistas = 0
    for seguinte in linhas[indice + 1 : indice + 8]:
        crua = seguinte.strip()
        if not crua:
            continue
        if len(crua) >= MINIMO_DE_PROSA_SEGUINTE:
            return True
        # Um titulo pode ser seguido direto do seu primeiro subtitulo.
        if PADRAO_DE_SECAO.match(crua):
            return True
        # Olhar mais de uma linha e necessario porque o proprio titulo quebra:
        # "2.1 ... and Phoenix case" / "study". Parar na primeira linha curta
        # descartaria toda secao de titulo longo.
        vistas += 1
        if vistas >= 3:
            return False
    return False


@dataclass
class Secao:
    """Um titulo achado em texto sem formatacao."""

    linha: int
    titulo: str
    nivel: int
    # O que sobrou na propria linha do titulo. Vazio quando o titulo ocupa a
    # linha inteira; preenchido em "Abstract As climate change afeta...", onde
    # o texto comeca colado ao rotulo.
    inicio: str = ""


def detectar_secoes(texto: str) -> list[Secao]:
    """Acha "Abstract", "1 Introduction", "2.2.1 ..." e "References".

    Conservador de proposito: um falso positivo parte o documento no lugar
    errado e a pessoa cura um bloco que nao existe, o que e pior que nao achar
    secao nenhuma.
    """
    linhas = texto.splitlines()

    # Quantas vezes cada linha curta aparece. Cabecalho de pagina se repete.
    repeticoes: dict[str, int] = {}
    for linha in linhas:
        chave = linha.strip()
        if chave:
            repeticoes[chave] = repeticoes.get(chave, 0) + 1

    achados: list[Secao] = []
    anterior: tuple[int, ...] = ()
    limite_inicial = max(int(len(linhas) * FRACAO_INICIAL_DO_DOCUMENTO), MINIMO_DE_LINHAS_INICIAIS)

    for indice, linha in enumerate(linhas):
        crua = linha.strip()
        if not crua:
            continue

        # O rotulo de abertura vem colado ao texto, entao a linha e longa e as
        # checagens de titulo curto nao se aplicam a ele.
        if indice <= limite_inicial:
            abertura = PADRAO_DE_ABERTURA.match(crua)
            if abertura is not None:
                achados.append(
                    Secao(
                        linha=indice,
                        titulo=abertura.group(1).title(),
                        nivel=1,
                        inicio=abertura.group(2).strip(),
                    )
                )
                continue

        if len(crua) > MAXIMO_DE_TITULO_DE_SECAO:
            continue
        if repeticoes.get(crua, 0) > MAXIMO_DE_REPETICOES:
            continue

        if crua.lower().rstrip(":") in SECOES_FINAIS:
            achados.append(Secao(linha=indice, titulo=crua.rstrip(":"), nivel=1))
            continue

        achado = PADRAO_DE_SECAO.match(crua)
        if achado is None:
            continue

        resto = achado.group(2).strip()
        # Um titulo de secao nao termina em ponto nem comeca em minuscula: as
        # duas coisas denunciam uma frase que so por acaso comeca com numero.
        if resto.endswith((".", ",", ";", ":")) or not resto[:1].isupper():
            continue
        if resto.count(",") > MAXIMO_DE_VIRGULAS:
            continue
        if not _vem_prosa_depois(linhas, indice):
            continue

        numero = _numero_da_secao(achado.group(1))
        if not _sequencia_faz_sentido(anterior, numero):
            continue

        anterior = numero
        achados.append(Secao(linha=indice, titulo=f"{achado.group(1)} {resto}", nivel=len(numero)))

    return achados


def dividir_texto_puro(texto: str) -> list[Bloco]:
    """Blocos de um texto que NAO e Markdown.

    Quando nao ha secao numerada reconhecivel o documento vira um bloco so — e
    essa e a verdade sobre ele, nao uma falha. Inventar divisao onde nao ha
    estrutura daria a quem cura a impressao de que o PDF foi entendido.
    """
    if not texto or not texto.strip():
        return []

    secoes = detectar_secoes(texto)
    if not secoes:
        return [Bloco(ordem=0, nivel=0, titulo="", conteudo=texto.strip())]

    linhas = texto.splitlines()
    blocos: list[Bloco] = []

    cabecalho = "\n".join(linhas[: secoes[0].linha]).strip()
    if cabecalho:
        blocos.append(Bloco(ordem=0, nivel=0, titulo="", conteudo=cabecalho))

    for posicao, secao in enumerate(secoes):
        fim = secoes[posicao + 1].linha if posicao + 1 < len(secoes) else len(linhas)
        corpo = "\n".join(linhas[secao.linha + 1 : fim]).strip()
        conteudo = f"{secao.inicio}\n{corpo}".strip() if secao.inicio else corpo
        titulo, nivel = secao.titulo, secao.nivel
        # Secao que so contem subsecoes nao entra: seria uma caixa de marcar
        # que nao leva trecho nenhum ao indice. A hierarquia continua legivel
        # pela propria numeracao dos filhos ("2.2.1").
        if not conteudo:
            continue
        blocos.append(Bloco(ordem=len(blocos), nivel=nivel, titulo=titulo, conteudo=conteudo))

    return blocos


def dividir_em_blocos(markdown: str, *, e_markdown: bool = True) -> list[Bloco]:
    """Quebra o texto convertido em blocos, preservando a ordem do documento.

    `e_markdown=False` para o que veio do extrator local, e a distincao nao e
    cosmetica. Texto puro nao tem cabecalho ATX, mas pode conter uma linha
    comecando por `#` — num artigo real da Springer o simbolo (c) foi decodificado
    como `#`, e a linha de copyright virou "titulo", partindo o documento ali e
    sendo eleita titulo da obra. O texto parecia estruturado sem estar, que e o
    modo de falhar mais caro que existe nesta tela.

    O texto antes do primeiro titulo vira um bloco sem titulo — e onde costuma
    ficar o cabecalho do artigo, com autores e filiacao.
    """
    if not e_markdown:
        return dividir_texto_puro(markdown)

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

    # Só o Docling exporta Markdown. O extrator local devolve texto puro, e
    # interpretá-lo como Markdown inventa estrutura a partir de acidentes de
    # decodificação.
    blocos = dividir_em_blocos(document.markdown_full or "", e_markdown=document.texto_e_markdown)
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
