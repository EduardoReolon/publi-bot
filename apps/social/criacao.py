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
from apps.social.redes import rede
from core.resposta_ia import itens, ler_blocos

logger = logging.getLogger("publibot.social")

ROTULOS = [
    "IDEIA",
    "GANCHO",
    "LEGENDA",
    "HASHTAGS",
    "LAMINAS",
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
- Nada de link ou endereco no texto: o sistema poe o link no lugar certo.
- Nao comece com 'Voce sabia' nem repetindo o titulo do artigo.
- Respeite as REGRAS QUE NUNCA SE QUEBRAM e as instrucoes do negocio.

Converse antes, se algo nao estiver claro. Quando eu pedir ("pode gerar"),
responda SO nos blocos abaixo e termine com FIM (a resposta volta para o
sistema, que a le sozinho):

IDEIA: a ideia do post em uma frase simples
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

Dores do publico que o artigo toca:
{dores}

## A conta
PUBLICO: {publico}
TOM: {tom}
ABORDAGEM: {abordagem}
Instrucoes do negocio: {instrucoes_do_negocio}
REGRAS QUE NUNCA SE QUEBRAM: {regras}
Idioma: {idioma}
"""

LAMINAS = (
    "LAMINAS:\n- titulo (ate 8 palavras) | texto (ate 20 palavras) | visual (a cena da imagem,"
    " sem texto)\n  (de {min} a {max} linhas; a primeira e a capa)\n"
)
ENQUETE = "ENQUETE: pergunta | opcao 1 | opcao 2 (de 2 a 4 opcoes, ate 30 caracteres cada)\n"
COMENTARIO = "PRIMEIRO COMENTARIO: a frase que chama para o artigo (o link vai depois dela)\n"
IMAGENS_DEPOIS = (
    "\nDepois dos blocos, se voce gera imagens, faca tambem uma imagem por lamina, na ordem, "
    "4:5 vertical, no estilo da conta, SEM nenhum texto na imagem (o sistema escreve o "
    "texto por cima) — eu envio cada uma na lamina dela.\n"
)

MATERIAL_VAZIO = {"secoes": [], "identificacao": [], "achados": [], "dores": []}


def _artigo_e_material(post: Post):
    """O material, como a nossa IA recebe; sem os vetores agora (placa
    desligada), so o resumo e as secoes — o pedido sai do mesmo jeito."""
    try:
        return redacao.artigo_e_material(post)
    except Exception as exc:
        logger.info("Material do post %s sem vetores: %s", post.pk, exc)
        from apps.social import fontes, proprio

        entrada = post.entrada if post.entrada_id else None
        artigo = proprio.como_artigo(entrada) if entrada else fontes.artigo(post.artigo_id)
        return artigo, dict(MATERIAL_VAZIO)


def pedido(post: Post) -> str:
    from apps.social.outra_ia import _estilo_visual

    artigo, mat = _artigo_e_material(post)
    if artigo is None:
        return ""
    r = rede(post.destino.rede)
    f = r.formato
    v = redacao._variaveis(post, artigo, mat, ajuste="")
    laminas = redacao.com_laminas(post)
    return PEDIDO.format(
        rede=r.nome,
        instrucoes=redacao.instrucoes(post, artigo),
        estilo=r.estilo,
        limites=v["limites"],
        imagens=_estilo_visual(post.destino) if laminas else "uma imagem (a capa do artigo)",
        dobra=f.dobra or 200,
        hashtags=f.max_hashtags,
        laminas=LAMINAS.format(min=f.min_laminas, max=f.max_laminas) if laminas else "",
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
    )


def _partes(linha: str) -> list[str]:
    return [p.strip().strip('`*_[]"“”') for p in linha.split("|")]


def ler(resposta: str) -> tuple[dict, str]:
    """(dados no formato do JSON da nossa IA, comentarios) da resposta colada."""
    blocos = ler_blocos(resposta, ROTULOS)
    dados = {
        "ideia": blocos.get("IDEIA", "").strip(),
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
                "visual": " | ".join(partes[2:]).strip(),
            }
        )
    enquete = _partes(" ".join(blocos.get("ENQUETE", "").split()))
    if len(enquete) >= 3:
        dados["enquete"] = {"pergunta": enquete[0], "opcoes": enquete[1:]}
    return dados, blocos.get("COMENTARIOS", "").strip()


def aplicar(post: Post, resposta: str) -> tuple[bool, list[str], str]:
    """Le a resposta e grava como o texto do post (rascunho, para revisar).
    (aplicou, avisos da conferencia, comentarios da outra IA)."""
    dados, comentarios = ler(resposta)
    if not (dados["gancho"] or dados["texto"]):
        return False, [], comentarios
    artigo, mat = _artigo_e_material(post)
    if artigo is None:
        return False, ["o artigo nao existe mais"], comentarios
    texto, extras, avisos = redacao.montar_e_conferir(post, dados, artigo)
    extras["escrito_por"] = "outra_ia"
    redacao.guardar(post, texto, extras, avisos, artigo, mat)
    return True, avisos, comentarios
