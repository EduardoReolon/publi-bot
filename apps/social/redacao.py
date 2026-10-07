"""Escrever o post: o material (algoritmo) vai ao modelo, a resposta volta e
e conferida (algoritmo) antes de ir para a revisao.

A conferencia, sem IA:

* tamanho da rede e do gancho (o que aparece antes do "ver mais");
* nenhum numero que o artigo nao tenha;
* nenhum termo proibido do guia editorial;
* nenhum endereco no texto (o link vai no lugar que a rede pede);
* laminas dentro do minimo e do maximo (Instagram).

Achou problema: uma nova tentativa, dizendo ao modelo o que corrigir. Ainda
assim: o post vai para a revisao com os avisos, e nunca sai sozinho.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from django.urls import reverse
from django.utils.text import slugify

from apps.social import fontes, material
from apps.social.models import ConfiguracaoSocial, Post
from apps.social.redes import rede

logger = logging.getLogger("publibot.social")

_URL = re.compile(r"https?://\S+|www\.\S+")
_FRASE = re.compile(r"(?<=[.!?…])\s+")

ESQUEMA = {
    "type": "object",
    "properties": {
        "gancho": {"type": "string"},
        "texto": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "primeiro_comentario": {"type": "string"},
        "enquete": {
            "type": "object",
            "properties": {
                "pergunta": {"type": "string"},
                "opcoes": {"type": "array", "items": {"type": "string"}},
            },
        },
        "laminas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"titulo": {"type": "string"}, "texto": {"type": "string"}},
            },
        },
    },
    "required": ["gancho", "texto"],
}


# -- Link rastreado ------------------------------------------------------------------
def destino_do_clique(post: Post) -> str:
    """O endereco do artigo com a origem marcada (utm): o site e o Analytics
    sabem que a visita veio desta rede, deste artigo e desta abordagem."""
    partes = urlsplit(post.artigo_url)
    utm = {
        "utm_source": post.destino.rede,
        "utm_medium": "social",
        "utm_campaign": slugify(post.artigo_titulo)[:60] or "artigo",
        "utm_content": slugify(post.abordagem.nome if post.abordagem else "post")[:40],
    }
    consulta = "&".join(p for p in [partes.query, urlencode(utm)] if p)
    return urlunsplit((partes.scheme, partes.netloc, partes.path, consulta, partes.fragment))


def link_rastreado(post: Post) -> str:
    """O link que vai para a rede: passa pelo PubliBot (que conta o clique) e
    segue para o artigo com a origem marcada. E o que Buffer e Hootsuite fazem
    com os encurtadores deles."""
    return fontes.endereco_publico(reverse("social:clique", args=[post.chave_publica]))


# -- Escrever -------------------------------------------------------------------------
def _lista(itens) -> str:
    return "\n".join(f"- {i}" for i in itens) or "(nenhum)"


def _limites(r) -> str:
    f = r.formato
    partes = [f"ate {f.max_caracteres} caracteres no total"]
    if f.dobra:
        partes.append(f"gancho de ate {f.dobra} caracteres")
    partes.append(f"ate {f.max_hashtags} hashtags" if f.max_hashtags else "sem hashtag")
    if f.max_laminas:
        partes.append(f"de {f.min_laminas} a {f.max_laminas} laminas")
    return "; ".join(partes)


def _variaveis(post: Post, artigo, mat: dict, ajuste: str) -> dict:
    r = rede(post.destino.rede)
    config = ConfiguracaoSocial.carregar()
    negocio = fontes.negocio()
    destino = post.destino
    instrucoes = "\n".join(x for x in [config.instrucoes, destino.instrucoes] if x.strip())
    abordagem = (
        f"{post.abordagem.nome}: {post.abordagem.instrucao}"
        if post.abordagem
        else "a que fizer mais sentido para o material"
    )
    return {
        "titulo": artigo.titulo,
        "resumo": artigo.resumo or "(sem resumo)",
        "secoes": "; ".join(mat["secoes"]) or "(sem secoes)",
        "identificacao": _lista(mat["identificacao"]),
        "achados": _lista(mat["achados"]),
        "dores": _lista(mat["dores"]),
        "publico": destino.publico or r.publico_padrao,
        "tom": destino.tom or r.tom_padrao,
        "abordagem": abordagem,
        "instrucoes": instrucoes or "(nenhuma)",
        "regras": config.regras or "(nenhuma)",
        "limites": _limites(r),
        "idioma": negocio.get("idioma") or "pt-BR",
        "ajuste": ajuste,
    }


def _ler(texto: str) -> dict:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```\w*\n|```$", "", texto).strip()
    dados = json.loads(texto)
    if not isinstance(dados, dict):
        raise ValueError("o modelo nao devolveu um objeto JSON.")
    return dados


def _hashtags(lista, fixas: str, maximo: int) -> list[str]:
    todas = [*(lista or []), *re.findall(r"#?(\w+)", fixas or "")]
    limpas = [re.sub(r"\W", "", str(h).lstrip("#")) for h in todas]
    return list(dict.fromkeys(h for h in limpas if h))[:maximo]


def _cortar(texto: str, maximo: int) -> str:
    """Corta em frase inteira (nunca no meio de uma palavra)."""
    if len(texto) <= maximo:
        return texto
    saida = ""
    for frase in _FRASE.split(texto):
        if len(saida) + len(frase) + 1 > maximo:
            break
        saida = f"{saida} {frase}".strip()
    return saida or texto[:maximo].rsplit(" ", 1)[0]


def montar(post: Post, dados: dict) -> tuple[str, dict]:
    """O texto final e os extras, a partir do JSON do modelo."""
    r = rede(post.destino.rede)
    f = r.formato
    gancho = _URL.sub("", str(dados.get("gancho") or "")).strip()
    corpo = _URL.sub("", str(dados.get("texto") or "")).strip()
    if gancho and corpo.startswith(gancho):
        corpo = corpo[len(gancho) :].strip()
    hashtags = _hashtags(dados.get("hashtags"), post.destino.hashtags_fixas, f.max_hashtags)
    if f.link == "bio" and "bio" not in corpo.lower():
        corpo = f"{corpo}\n\nLink na bio.".strip()
    rodape = " ".join(f"#{h}" for h in hashtags)
    texto = "\n\n".join(p for p in [gancho, corpo] if p)
    texto = _cortar(texto, f.max_caracteres - (len(rodape) + 2 if rodape else 0))
    if rodape:
        texto = f"{texto}\n\n{rodape}"
    extras = {"gancho": gancho, "hashtags": hashtags}
    if e_enquete(post):
        extras["formato"] = "enquete"
        enquete = enquete_limpa(dados)
        if enquete is not None:
            extras["enquete"] = enquete
            if post.destino.rede != "linkedin":
                # Sem enquete na API desta rede: post comum, com a pergunta e as
                # opcoes para responder nos comentarios.
                letras = "ABCD"
                opcoes = "\n".join(f"{letras[i]}) {o}" for i, o in enumerate(enquete["opcoes"]))
                pergunta = f"{enquete['pergunta']}\n{opcoes}\nResponda nos comentarios com a letra."
                texto = _cortar(texto, f.max_caracteres - len(pergunta) - 2)
                texto = f"{texto}\n\n{pergunta}"
    if f.link == "comentario":
        chamada = _URL.sub("", str(dados.get("primeiro_comentario") or "")).strip()
        extras["primeiro_comentario"] = chamada or "O artigo completo, com as fontes:"
    if f.link in {"comentario", "botao"}:
        extras["link"] = link_rastreado(post)
    if f.max_laminas:
        laminas = []
        for item in (dados.get("laminas") or [])[: f.max_laminas]:
            if isinstance(item, dict) and (item.get("titulo") or item.get("texto")):
                laminas.append(
                    {
                        "titulo": str(item.get("titulo") or "").strip()[:120],
                        "texto": str(item.get("texto") or "").strip()[:220],
                    }
                )
        extras["laminas"] = laminas
    return texto, extras


# Enquete: no LinkedIn sai como enquete de verdade (Posts API, `content.poll`);
# nas outras redes, como post comum que termina na pergunta, com as opcoes para
# responder nos comentarios. Limites do LinkedIn: pergunta ate 140 caracteres,
# de 2 a 4 opcoes de ate 30.
PERGUNTA_MAXIMA = 140
OPCAO_MAXIMA = 30
INSTRUCAO_DA_ENQUETE = (
    "FORMATO ENQUETE: o post termina numa PERGUNTA ao leitor sobre o assunto, que "
    "ele responde escolhendo uma opcao. Devolva tambem 'enquete': {'pergunta': ate "
    f"{PERGUNTA_MAXIMA} caracteres, 'opcoes': de 2 a 4, cada uma com ate {OPCAO_MAXIMA} "
    "caracteres}. As opcoes sao respostas plausiveis e sem pegadinha; nenhuma e a "
    "'certa' pelo texto. O texto do post apresenta o assunto e leva a pergunta.\n"
)


def e_enquete(post: Post) -> bool:
    return (post.extras or {}).get("formato") == "enquete"


def enquete_limpa(dados: dict) -> dict | None:
    bruta = dados.get("enquete") if isinstance(dados.get("enquete"), dict) else {}
    pergunta = _URL.sub("", str(bruta.get("pergunta") or "")).strip()[:PERGUNTA_MAXIMA]
    opcoes = [
        _URL.sub("", str(o)).strip()[:OPCAO_MAXIMA]
        for o in bruta.get("opcoes") or []
        if str(o).strip()
    ][:4]
    if not pergunta or len(opcoes) < 2:
        return None
    return {"pergunta": pergunta, "opcoes": opcoes}


def _textos_das_laminas(extras: dict) -> list[str]:
    return [f"{l_['titulo']} {l_['texto']}" for l_ in extras.get("laminas", [])]


def conferir(post: Post, texto: str, extras: dict, artigo, *, com_fotos: bool = False) -> list[str]:
    r = rede(post.destino.rede)
    f = r.formato
    avisos = []
    tudo = " ".join(
        [texto, extras.get("primeiro_comentario", "")]
        + [f"{l_['titulo']} {l_['texto']}" for l_ in extras.get("laminas", [])]
    )
    fora = material.numeros_fora_do_artigo(tudo, artigo.texto)
    if fora:
        avisos.append(f"numero que o artigo nao tem: {', '.join(fora)}")
    termos = fontes.termos_a_evitar(tudo)
    if termos:
        avisos.append(f"termo que o guia editorial proibe: {', '.join(termos)}")
    cuidados = fontes.cuidados_de_linguagem(tudo)
    if cuidados:
        avisos.append(f"cuidado de linguagem: {'; '.join(cuidados)}")
    if f.dobra and len(extras.get("gancho", "")) > f.dobra:
        avisos.append(
            f"a primeira linha tem {len(extras['gancho'])} caracteres; so {f.dobra} aparecem "
            "antes do 'ver mais'"
        )
    if f.max_laminas and not com_fotos:
        n = len(extras.get("laminas", []))
        if n < f.min_laminas:
            avisos.append(
                f"so {n} lamina(s); o carrossel pede de {f.min_laminas} a {f.max_laminas}"
            )
        longas = [
            i + 1 for i, l_ in enumerate(extras.get("laminas", [])) if len(l_["texto"].split()) > 25
        ]
        if longas:
            avisos.append(f"lamina(s) {', '.join(map(str, longas))} com texto longo demais")
    return avisos


def _do_tema(tema, principal):
    """Post de tema: o material vem das frases do tema (de varios artigos); a
    conferencia dos numeros vale contra o texto de TODOS esses artigos; o link
    e a capa sao do artigo principal."""
    from dataclasses import replace

    from apps.social.temas import material_do_tema

    textos = [principal.texto]
    for artigo_id in tema.artigos:
        if str(artigo_id) != principal.id and (outro := fontes.artigo(artigo_id)):
            textos.append(outro.texto)
    titulos = list(dict.fromkeys(f["artigo_titulo"] for f in tema.frases))
    combinado = replace(
        principal,
        titulo=tema.titulo,
        resumo=f"Tema que aparece em {len(tema.artigos)} artigos: " + "; ".join(titulos),
        texto="\n".join(textos),
    )
    return combinado, material_do_tema(tema)


def escrever(post: Post) -> Post:
    """Escreve (ou reescreve) o post. Sem modelo agora: levanta, e quem chamou
    (a tarefa) tenta mais tarde."""
    from apps.social import laminas, proprio

    entrada = post.entrada if post.entrada_id else None
    artigo = proprio.como_artigo(entrada) if entrada else fontes.artigo(post.artigo_id)
    if artigo is None:
        post.situacao = Post.Situacao.FALHOU
        post.erro = "o artigo nao existe mais."
        post.save(update_fields=["situacao", "erro", "atualizado_em"])
        return post
    destino = post.destino
    r = rede(destino.rede)
    if post.tema_id:
        artigo, mat = _do_tema(post.tema, artigo)
    else:
        mat = material.montar(
            artigo, publico=destino.publico or r.publico_padrao, negocio=fontes.negocio()
        )
    post.situacao = Post.Situacao.GERANDO
    post.save(update_fields=["situacao", "atualizado_em"])

    # O angulo sugerido (outra IA, evento) vai como sugestao, nao como ordem:
    # o material continua sendo o limite do que se afirma.
    ideia = (post.extras or {}).get("ideia", "")
    recado = (post.extras or {}).get("recado") or {}
    base = f"IDEIA SUGERIDA (use se couber no material): {ideia}\n" if ideia else ""
    if recado.get("instrucao"):
        base += (
            "RECADO DE POSICIONAMENTO (passe esta ideia uma vez, numa frase curta e com "
            "as palavras do post, sem tom de propaganda nem ataque a ninguem; o resto do "
            f"post segue o material): {recado['instrucao']}\n"
        )
    if entrada is not None:
        base = proprio.instrucao_para_quem_escreve(entrada, artigo) + "\n" + base
    if e_enquete(post):
        base = INSTRUCAO_DA_ENQUETE + base
    base = (
        fontes.instrucoes_sensiveis(f"{artigo.titulo}\n{artigo.texto}")
        + "\nHashtags com cada palavra iniciando em maiuscula (#SaudeMental, nao "
        "#saudemental): o leitor de tela le palavra por palavra.\n" + base
    )
    com_fotos = entrada is not None and entrada.midias.exists()
    ajuste, avisos, texto, extras = base, [], "", {}
    for _tentativa in range(2):
        try:
            dados = _ler(
                fontes.executar(
                    r.prompt, _variaveis(post, artigo, mat, ajuste), json_schema=ESQUEMA
                )
            )
        except ValueError as exc:
            avisos = [f"o modelo devolveu algo que nao e JSON: {exc}"]
            ajuste = base + "A resposta anterior nao era um JSON valido. Responda SOMENTE o JSON."
            continue
        if com_fotos:
            dados["laminas"] = []  # as imagens sao as fotos reais
        texto, extras = montar(post, dados)
        avisos = conferir(post, texto, extras, artigo, com_fotos=com_fotos)
        if e_enquete(post) and "enquete" not in extras:
            avisos.append("a enquete veio sem pergunta ou com menos de 2 opcoes")
        if entrada is not None:
            avisos += [
                f"pode identificar alguem: {a}"
                for a in proprio.identificacao(" ".join([texto, *_textos_das_laminas(extras)]))
            ]
        if not avisos:
            break
        ajuste = base + (
            "A versao anterior teve estes problemas; corrija sem mudar o resto: "
            + "; ".join(avisos)
            + "."
        )

    post.texto = texto
    # O que veio da sugestao (ideia, recado em teste) continua no post.
    post.extras = {
        **extras,
        **({"formato": "enquete"} if e_enquete(post) else {}),
        **({"ideia": ideia} if ideia else {}),
        **({"recado": recado} if recado else {}),
    }
    post.avisos = avisos + (
        [f"link nao lido: {x}" for x in proprio.links_que_falharam(entrada)] if entrada else []
    )
    post.material = mat
    post.erro = ""
    post.situacao = Post.Situacao.RASCUNHO if texto else Post.Situacao.FALHOU
    if not texto:
        post.erro = "; ".join(avisos) or "o modelo nao escreveu nada."
    post.save()
    if texto:
        try:
            laminas.preparar_imagens(post, artigo)
        except Exception as exc:  # imagem e complemento: o texto continua valendo
            logger.exception("Imagens do post %s nao montadas.", post.pk)
            post.avisos = [*post.avisos, f"imagens nao montadas: {exc}"]
            post.save(update_fields=["avisos", "atualizado_em"])
    if texto and not avisos and destino.aprovacao == destino.Aprovacao.AUTOMATICO:
        from apps.social.publicacao import aprovar

        aprovar(post, por=None)
    return post
