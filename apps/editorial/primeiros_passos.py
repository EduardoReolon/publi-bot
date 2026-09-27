"""Primeiros passos do Negocio, com um modelo grande de fora.

Quem abre o PubliBot pela primeira vez raramente sabe escrever "tema",
"publico" e "dores" do jeito que o radar precisa. O caminho:

1. a pessoa escreve do jeito dela o que vende e para quem;
2. copia o **pedido 1** e cola num modelo grande: ele devolve tema, publico,
   oferta na lingua do cliente, dores e frentes, num formato fixo;
3. cola a resposta aqui: os campos se preenchem, e ela revisa e salva;
4. copia o **pedido 2**: pilares de conteudo e palavras-semente, que viram
   *Sementes sugeridas* no Radar, para aceitar ou recusar uma a uma.

O modelo local nao faz isso bem, e nao precisa: e um passo de uma vez, feito
com a pessoa olhando. Nada e enviado pelo PubliBot. A estrutura dos pedidos
parte de praticas testadas de conteudo organico (analise de intencao,
sub-nichos, clusters e silos), trocando o "gere ideias" generico por respostas
que cabem em campos.
"""

from __future__ import annotations

from core.resposta_ia import MARCADOR_DE_ITEM, itens, ler_blocos

ROTULOS_DO_NEGOCIO = ["TEMA", "PUBLICO", "OFERTA", "DORES", "FRENTES"]
ROTULOS_DAS_SEMENTES = ["PILARES", "SEMENTES"]


def _contexto_do_site() -> str:
    from apps.integrations.models import Site

    site = Site.objects.first()
    if site is None:
        return ""
    partes = [f"Site: {site.base_url}"]
    if site.home_content_text:
        partes.append(
            "Texto da pagina inicial (como o site devolveu):\n" + site.home_content_text[:3000]
        )
    return "\n".join(partes)


def pedido_do_negocio(perfil, config) -> str:
    regioes = [nome for _codigo, nome in config.locais() if nome]
    return f"""\
Voce e um estrategista de negocio e de conteudo organico. Vou descrever meu
negocio do meu jeito. Transforme isso na referencia de um sistema que escolhe
temas de artigos para o meu site e mede se cada tema esta perto do que eu vendo.

O que eu vendo (minhas palavras): {perfil.oferta or "(escreva aqui)"}
Para quem: {perfil.publico or "(escreva aqui)"}
Sobre o que o site fala hoje: {perfil.tema or "(nao sei dizer)"}
Onde atendo: {", ".join(regioes) or "o pais inteiro"}
{_contexto_do_site()}

Regras:
- OFERTA na lingua de quem COMPRA, sem jargao: o cliente nao busca o nome da
  tecnica, busca o problema que ela resolve.
- DORES: o que a pessoa digitaria no Google ANTES de saber que precisa de mim.
- TEMA amplo o bastante para caber artigos de topo de funil; OFERTA e a porta de
  entrada mais facil de vender.
- Nao invente numeros de busca.

Responda EXATAMENTE neste formato, sem nada antes, e termine com a linha FIM:

TEMA: uma frase sobre o assunto do site (o assunto, nao o produto)
PUBLICO: uma frase: quem le e quem compra, e o quanto entende do assunto
OFERTA: uma ou duas frases, como o cliente descreveria o que eu vendo
DORES:
- de 5 a 10 problemas, cada um como a pessoa diria
FRENTES:
- de 3 a 5 areas vizinhas que o negocio atende ou poderia atender, curtas
FIM
"""


def pedido_das_sementes(perfil, config) -> str:
    frentes = [f.strip() for f in perfil.frentes.splitlines() if f.strip()]
    return f"""\
Voce e um especialista em SEO e estrategia de conteudo. Monte os pilares de
conteudo (clusters) do meu site e as palavras-semente que um radar vai buscar no
Google para descobrir o que as pessoas perguntam.

Tema do site: {perfil.tema or "(nao preenchido)"}
Publico: {perfil.publico or "(nao preenchido)"}
Oferta (porta de entrada): {perfil.oferta or "(nao preenchida)"}
Outras frentes: {"; ".join(frentes) or "(nenhuma)"}
Dores do publico: {"; ".join(config.lista_de_dores) or "(nenhuma)"}
Sementes que ja uso: {"; ".join(config.lista_de_sementes) or "(nenhuma)"}

Regras:
- 3 a 5 PILARES: um assunto grande cada, com os subtemas que viram artigos.
  Pelo menos um pilar deve levar naturalmente a oferta.
- 15 a 20 SEMENTES: de 1 a 4 palavras, como alguem digitaria no Google.
  Misture a lingua de quem sente o problema com os termos tecnicos que o
  PUBLICO conhece. Nada de marca de concorrente. Nao repita as que ja uso.
- Marque com * no inicio as 5 sementes para testar primeiro.
- Nao invente volume de busca.

Responda EXATAMENTE neste formato, sem nada antes, e termine com a linha FIM:

PILARES:
- nome do pilar: subtema; subtema; subtema
SEMENTES:
- semente
FIM
"""


def ler_negocio(resposta: str) -> dict:
    """Os campos do formulario do Negocio a partir da resposta colada."""
    blocos = ler_blocos(resposta, ROTULOS_DO_NEGOCIO)
    saida = {}
    for campo, rotulo in (("tema", "TEMA"), ("publico", "PUBLICO"), ("oferta", "OFERTA")):
        texto = " ".join(blocos.get(rotulo, "").split())
        if texto:
            saida[campo] = texto
    for campo, rotulo in (("dores", "DORES"), ("frentes", "FRENTES")):
        lista = itens(blocos.get(rotulo, ""))
        if lista:
            saida[campo] = lista
    return saida


def ler_sementes(resposta: str) -> list[tuple[str, bool, str]]:
    """[(semente, prioritaria, pilar)] — dos pilares entra o nome, das sementes todas."""
    blocos = ler_blocos(resposta, ROTULOS_DAS_SEMENTES)
    saida: list[tuple[str, bool, str]] = []
    vistas: set[str] = set()
    for bruta in blocos.get("SEMENTES", "").splitlines():
        # A marca de prioridade e um * colado na semente ("- *clientes
        # inativos"): lida antes de `itens` limpar os enfeites.
        sem_marcador = MARCADOR_DE_ITEM.sub("", bruta).strip()
        prioritaria = sem_marcador.startswith("*") and not sem_marcador.startswith("**")
        semente = next(iter(itens(sem_marcador.lstrip("*"))), "")
        if semente and semente.lower() not in vistas:
            vistas.add(semente.lower())
            saida.append((semente[:200], prioritaria, ""))
    for linha in itens(blocos.get("PILARES", "")):
        pilar = linha.split(":", 1)[0].strip()
        if pilar and pilar.lower() not in vistas and len(pilar.split()) <= 5:
            vistas.add(pilar.lower())
            saida.append((pilar[:200], False, linha[:300]))
    return saida


def unir(atuais: str, novos: list[str]) -> str:
    """As linhas atuais mais as novas, sem repetir (sem caixa)."""
    linhas = [linha.strip() for linha in atuais.splitlines() if linha.strip()]
    vistas = {linha.lower() for linha in linhas}
    for novo in novos:
        if novo.lower() not in vistas:
            vistas.add(novo.lower())
            linhas.append(novo)
    return "\n".join(linhas)


def sugerir_sementes(resposta: str) -> int:
    """Cria Sementes sugeridas (origem "outra IA") para a pessoa decidir no Radar."""
    from apps.radar.models import SementeSugerida
    from apps.radar.sugestoes import _registrar

    criadas = 0
    for semente, prioritaria, pilar in ler_sementes(resposta):
        evidencia = {"prioritaria": prioritaria}
        if pilar:
            evidencia["pilar"] = pilar
        if _registrar(
            semente, SementeSugerida.Tipo.SEMENTE, SementeSugerida.Origem.OUTRA_IA, **evidencia
        ):
            criadas += 1
    return criadas


# ---------------------------------------------------------------------------
# Guia editorial
# ---------------------------------------------------------------------------
ROTULOS_DO_GUIA = [
    "HUMOR",
    "FORMALIDADE",
    "RESPEITO",
    "ENTUSIASMO",
    "PESSOA",
    "REGRA DE OURO",
    "SOMOS",
    "TERMOS",
    "CONVITE",
    "EXEMPLOS",
]


def pedido_do_guia(perfil_do_negocio, config) -> str:
    """Voz, vocabulario e exemplos a partir do Negocio e da pagina do site."""
    negocio = perfil_do_negocio
    return f"""\
Voce e um editor-chefe que monta guias de estilo para sites de empresas. Monte
o guia de voz do meu site, para um sistema que escreve artigos com revisao
humana. O guia e sobre COMO o texto fala, nao sobre o que ele diz.

Tema do site: {negocio.tema or "(nao preenchido)"}
Publico: {negocio.publico or "(nao preenchido)"}
Oferta: {negocio.oferta or "(nao preenchida)"}
Dores do publico: {"; ".join(config.lista_de_dores) or "(nenhuma)"}
{_contexto_do_site()}

Regras:
- As quatro notas de tom vao de 1 a 5 (as dimensoes de tom da Nielsen Norman
  Group): HUMOR 1 serio a 5 engracado; FORMALIDADE 1 formal a 5 casual;
  RESPEITO 1 respeitoso a 5 irreverente; ENTUSIASMO 1 objetivo a 5 entusiasmado.
  Escolha pelo publico e pelo assunto, nao pelo que soa moderno.
- TERMOS: palavras que este site NAO deve usar (jargao que afasta o publico,
  promessa que o negocio nao pode fazer, termos que posicionam errado), com a
  troca sugerida e o motivo. Nada de palavrao obvio.
- EXEMPLOS: dois paragrafos curtos no tom certo, sobre o tema do site, sem
  numeros inventados.

Responda EXATAMENTE neste formato, sem nada antes, e termine com a linha FIM:

HUMOR: numero de 1 a 5
FORMALIDADE: numero de 1 a 5
RESPEITO: numero de 1 a 5
ENTUSIASMO: numero de 1 a 5
PESSOA: como falar com o leitor (voce, o senhor, a gente...)
REGRA DE OURO: uma frase que resume como o site escreve
SOMOS:
- somos X | nao somos Y
TERMOS:
- termo | troca sugerida | motivo
CONVITE: como o fim do artigo convida para a oferta, sem pressao
EXEMPLOS:
paragrafo 1

paragrafo 2
FIM
"""


def _nota(texto: str) -> int | None:
    import re

    achado = re.search(r"[1-5]", texto or "")
    return int(achado.group()) if achado else None


def ler_guia(resposta: str) -> dict:
    """Os campos do formulario do Guia editorial a partir da resposta colada."""
    blocos = ler_blocos(resposta, ROTULOS_DO_GUIA)
    saida: dict = {}
    for campo, rotulo in (
        ("tom_humor", "HUMOR"),
        ("tom_formalidade", "FORMALIDADE"),
        ("tom_respeito", "RESPEITO"),
        ("tom_entusiasmo", "ENTUSIASMO"),
    ):
        nota = _nota(blocos.get(rotulo, ""))
        if nota is not None:
            saida[campo] = nota
    for campo, rotulo, limite in (
        ("pessoa", "PESSOA", 40),
        ("regra_de_ouro", "REGRA DE OURO", 300),
        ("convite", "CONVITE", 2000),
    ):
        texto = " ".join(blocos.get(rotulo, "").split())
        if texto:
            saida[campo] = texto[:limite]
    somos = [
        linha.replace("nao somos", "").replace("não somos", "").replace("somos", "", 1).strip()
        for linha in itens(blocos.get("SOMOS", ""))
    ]
    if somos:
        saida["somos_texto"] = "\n".join(
            " | ".join(parte.strip() for parte in linha.split("|")) for linha in somos
        )
    termos = itens(blocos.get("TERMOS", ""))
    if termos:
        saida["termos_texto"] = "\n".join(termos)
    exemplos = blocos.get("EXEMPLOS", "").strip()
    if exemplos:
        saida["exemplos"] = exemplos
    return saida
