"""O post inteiro escrito por outra IA (Claude, ChatGPT, Gemini), com tudo o
que a nossa recebe: o material do artigo, o publico, a abordagem, o foco, a
isca, o formato, as regras, a clareza e o estilo das imagens. A resposta colada
passa pela MESMA conferencia da nossa (numeros que o artigo nao tem, termos
proibidos, clareza) e vira o post, com as laminas e a cena de cada uma.

E o caminho quando o modelo local nao escreve bem um assunto, ou quando se
quer a outra IA gerando tambem as imagens (o pedido ja pede as cenas no estilo
da conta).
"""

from __future__ import annotations

import logging
import re

from apps.social import redacao
from apps.social.models import Post
from apps.social.prompts import REGRA_DOS_NUMEROS, REGRA_DOS_PERSONAGENS
from apps.social.redes import rede
from core.resposta_ia import itens, ler_blocos

logger = logging.getLogger("publibot.social")

ROTULOS = [
    "FORMATO",
    "ESTILO",
    "IDEIA",
    "PRINCIPIOS",
    "GANCHO",
    "LEGENDA",
    "HASHTAGS",
    "LAMINAS",
    "CENAS",
    "FOTO",
    "ENQUETE",
    "PRIMEIRO COMENTARIO",
    "COMENTARIOS",
]

PEDIDO = """\
Voce vai escrever UM post para o {rede} de um negocio, a partir de um artigo ja
publicado (com fontes). O post leva a pessoa ao artigo; ele nao substitui o
artigo.

{instrucoes}
COMO A REDE FUNCIONA: {estilo}
FORMATO: {limites}.
IMAGENS DA CONTA: {imagens}

REGRAS:
- Use SO o que esta no MATERIAL abaixo: nenhum numero, estudo, caso ou promessa
  que nao esteja la. Numero, so copiado exatamente como esta.
{regras_do_material}- Nada de link ou endereco no texto: o sistema poe o link no lugar certo.
- Nao comece com 'Voce sabia' nem repetindo o titulo do artigo.
- Respeite as REGRAS QUE NUNCA SE QUEBRAM e as instrucoes do negocio.
- Deixe claro, numa frase simples (a penultima lamina ou o fim da legenda), o
  que o negocio faz por essa dor (a OFERTA abaixo), como quem explica e nao como
  anuncio: sem preco, sem promessa de resultado.
- Se o negocio atende numa REGIAO, o leitor tem de reconhecer o lugar logo no
  comeco, de UM jeito so: (a) se a capa ou a 1a cena mostra em destaque um dos
  LUGARES reais da conta (os com foto), NAO escreva o nome da cidade no texto,
  porque a imagem ja diz; (b) senao, traga o lugar no gancho, na capa ou na
  segunda lamina ("aqui em <a cidade>"). Nos dois casos, pode haver uma hashtag
  do lugar. Se nada disso aparecer, o sistema poe um selo com o lugar.

Responda JA com o post pronto, nos blocos abaixo, terminando com FIM: escolha
o formato que melhor serve a ideia e mande o material completo dele. Depois do
FIM, em duas ou tres linhas, diga por que escolheu e ofereca mudar (outro
formato, outra abordagem, outras cenas). Vamos conversar ate ficar bom: a CADA
resposta, mande de novo o post INTEIRO nos blocos — eu copio a ultima e colo no
sistema, que a le sozinho.

IDEIA: a ideia do post em uma frase simples
PRINCIPIOS: as chaves dos principios de persuasao que voce usou, separadas por virgula
GANCHO: a primeira linha da legenda (ate {dobra} caracteres)
LEGENDA: o resto da legenda
HASHTAGS: ate {hashtags}, separadas por virgula, sem #
{laminas}{enquete}{comentario}COMENTARIOS: o que voce mudaria na estrategia (opcional)
FIM
{imagens_depois}
## O artigo
Titulo: {titulo}
Resumo: {resumo}
Secoes: {secoes}

MATERIAL — frases do artigo proximas das dores do publico (identificacao):
{identificacao}

MATERIAL — achados com numero:
{achados}

DORES do publico que o artigo toca (a dor — falas reais do publico, entre aspas;
abra o post pela dor, com o jeito de dizer delas, sem copiar nome nem detalhe de
ninguem, e sem alarmismo: a dor mostrada ganha o caminho que o artigo da):
{dores}

## A conta
PUBLICO: {publico}
TOM: {tom}
ABORDAGEM: {abordagem}
OFERTA do negocio (o que ele faz): {oferta}
REGIAO onde atende: {regiao}
Instrucoes do negocio: {instrucoes_do_negocio}
REGRAS QUE NUNCA SE QUEBRAM: {regras}
Idioma: {idioma}
"""

LAMINAS = (
    "LAMINAS:\n- titulo (ate 8 palavras) | texto (ate 20 palavras) | visual (a cena da imagem,"
    " sem texto)\n  (de {min} a {max} linhas; a primeira e a capa)\n"
    "ESTILO: a chave do estilo das imagens que voce escolheu | por que, em uma frase\n"
)
FORMATO_E_CENAS = (
    "FORMATO: carrossel ou video | por que, em uma frase (as LAMINAS vao sempre: o "
    "carrossel e o plano B)\n"
    "CENAS: (quando o formato for video, ou o pedido disser que vai tambem como video; uma "
    "linha por cena)\n"
    "- segundos | fala (voz em off) | imagem inicial (o primeiro quadro) | movimento (o "
    "gesto e a camera) | frase na tela (texto EXTRA, alem das legendas; ou vazio)\n"
)
FOTO = (
    "FOTO: a cena da imagem do post (concreta, sem texto; personagens e lugares fixos pelo nome)\n"
)
ENQUETE = "ENQUETE: pergunta | opcao 1 | opcao 2 (de 2 a 4 opcoes, ate 30 caracteres cada)\n"
COMENTARIO = "PRIMEIRO COMENTARIO: a frase que chama para o artigo (o link vai depois dela)\n"
IMAGENS_DEPOIS = (
    "\nDepois dos blocos, se voce gera imagens, faca tambem uma imagem por lamina, na ordem, "
    "4:5 vertical, no estilo da conta, SEM nenhum texto na imagem (o sistema escreve o "
    "texto por cima) — eu envio cada uma na lamina dela.\n"
)

MATERIAL_VAZIO = redacao.MATERIAL_VAZIO


def _artigo_e_material(post: Post, *, na_tela: bool = False):
    """O material, como a nossa IA recebe; sem os vetores agora (placa
    desligada), so o resumo e as secoes — o pedido sai do mesmo jeito.

    `na_tela`: dentro de uma requisicao. Post ainda sem material guardado sai
    pelo caminho rapido (sem vetorizar o artigo, que passava do prazo e dava
    502), e o material completo e preparado em segundo plano para a proxima
    vez."""
    try:
        if na_tela and not post.material:
            from django.core.cache import cache

            from apps.social import fontes
            from apps.social.tasks import preparar_material

            # Abrir a tela de novo enquanto prepara nao enfileira outra vez.
            if cache.add(f"social:material:{post.pk}", 1, timeout=600):
                fontes.na_fila(
                    preparar_material, str(post.pk), rotulo="Material do post", alvo=post.pk
                )
        return redacao.artigo_e_material(post, guardado=True, sem_vetores=na_tela)
    except Exception as exc:
        logger.info("Material do post %s sem vetores: %s", post.pk, exc)
        from apps.social import fontes, proprio

        entrada = post.entrada if post.entrada_id else None
        artigo = proprio.como_artigo(entrada) if entrada else fontes.artigo(post.artigo_id)
        return artigo, dict(MATERIAL_VAZIO)


def pedido(post: Post, *, na_tela: bool = False) -> str:
    from apps.social.outra_ia import _estilo_visual

    artigo, mat = _artigo_e_material(post, na_tela=na_tela)
    if artigo is None:
        return ""
    r = rede(post.destino.rede)
    f = r.formato
    v = redacao._variaveis(post, artigo, mat, ajuste="")
    laminas = redacao.com_laminas(post)
    return PEDIDO.format(
        rede=r.nome,
        instrucoes=redacao.instrucoes(post, artigo, mat),
        estilo=r.estilo,
        limites=v["limites"],
        imagens=_estilo_visual(post.destino) if laminas else "uma imagem (a capa do artigo)",
        dobra=f.dobra or 200,
        hashtags=f.max_hashtags,
        laminas=_formatos(post, f) if laminas else ("" if redacao.com_fotos(post) else FOTO),
        enquete=ENQUETE if redacao.e_enquete(post) else "",
        comentario=COMENTARIO if f.link == "comentario" else "",
        imagens_depois=IMAGENS_DEPOIS if laminas else "",
        titulo=v["titulo"],
        resumo=v["resumo"],
        secoes=v["secoes"],
        identificacao=v["identificacao"],
        achados=v["achados"],
        dores=v["dores"],
        publico=v["publico"],
        tom=v["tom"],
        abordagem=v["abordagem"],
        instrucoes_do_negocio=v["instrucoes"],
        regras=v["regras"],
        idioma=v["idioma"],
        oferta=v["oferta"],
        regiao=v["regiao"],
        regras_do_material=REGRA_DOS_NUMEROS + REGRA_DOS_PERSONAGENS,
    )


def _formatos(post: Post, f) -> str:
    """O bloco das laminas e, se a conta faz video, o das cenas com as regras."""
    from apps.social import video_ia

    texto = LAMINAS.format(min=f.min_laminas, max=f.max_laminas)
    if post.destino.video == post.destino.Video.NUNCA:
        return texto
    return texto + FORMATO_E_CENAS + video_ia.regras_das_cenas(post.destino)


def _partes(linha: str) -> list[str]:
    return [p.strip().strip('`*_[]"“”') for p in linha.split("|")]


def ler(resposta: str) -> tuple[dict, str]:
    """(dados no formato do JSON da nossa IA, comentarios) da resposta colada."""
    blocos = ler_blocos(resposta, ROTULOS)
    dados = {
        "ideia": blocos.get("IDEIA", "").strip(),
        "principios": [p for p in re.split(r"[,\s]+", blocos.get("PRINCIPIOS", "")) if p],
        "gancho": " ".join(blocos.get("GANCHO", "").split()),
        "texto": blocos.get("LEGENDA", "").strip(),
        "hashtags": [h for h in re.split(r"[,\s#]+", blocos.get("HASHTAGS", "")) if h],
        "primeiro_comentario": blocos.get("PRIMEIRO COMENTARIO", "").strip(),
        "laminas": [],
    }
    for linha in itens(blocos.get("LAMINAS", "")):
        partes = _partes(linha)
        if len(partes) < 2 or not (partes[0] or partes[1]):
            continue  # continuacao de linha
        dados["laminas"].append(
            {
                "titulo": partes[0],
                "texto": partes[1],
                "visual": partes[2] if len(partes) > 2 else "",
                "movimento": partes[3] if len(partes) > 4 else "",
                # a fala e a ultima coluna (a resposta antiga, sem movimento, tambem le)
                "fala": " | ".join(partes[4:] if len(partes) > 4 else partes[3:]).strip(),
            }
        )
    formato = _partes(" ".join(blocos.get("FORMATO", "").split()))
    if formato and formato[0]:
        dados["formato_sugerido"] = formato[0].lower()
        dados["por_que_formato"] = " | ".join(formato[1:])
    estilo = _partes(" ".join(blocos.get("ESTILO", "").split()))
    if estilo and estilo[0]:
        dados["estilo"] = estilo[0].lower()
        dados["por_que_estilo"] = " | ".join(estilo[1:])
    cenas = []
    for linha in itens(blocos.get("CENAS", "")):
        partes = _partes(linha)
        if len(partes) >= 3 and partes[2]:
            cenas.append(
                {
                    "segundos": partes[0],
                    "fala": partes[1],
                    "imagem": partes[2],
                    "movimento": partes[3] if len(partes) > 3 else "",
                    "texto": partes[4] if len(partes) > 4 else "",
                }
            )
    if cenas:
        dados["video"] = {"cenas": cenas}
    if blocos.get("FOTO", "").strip():
        dados["foto"] = " ".join(blocos["FOTO"].split())
    enquete = _partes(" ".join(blocos.get("ENQUETE", "").split()))
    if len(enquete) >= 3:
        dados["enquete"] = {"pergunta": enquete[0], "opcoes": enquete[1:]}
    return dados, blocos.get("COMENTARIOS", "").strip()


def aplicar(post: Post, resposta: str, *, na_tela: bool = False) -> tuple[bool, list[str], str]:
    """Le a resposta e grava como o texto do post (rascunho, para revisar).
    (aplicou, avisos da conferencia, comentarios da outra IA)."""
    dados, comentarios = ler(resposta)
    if not (dados["gancho"] or dados["texto"]):
        return False, [], comentarios
    artigo, mat = _artigo_e_material(post, na_tela=na_tela)
    if artigo is None:
        return False, ["o artigo nao existe mais"], comentarios
    texto, extras, avisos = redacao.montar_e_conferir(post, dados, artigo)
    extras["escrito_por"] = "outra_ia"
    redacao.guardar(post, texto, extras, avisos, artigo, mat)
    return True, avisos, comentarios
