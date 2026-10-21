"""A identidade visual da conta escrita com uma IA de fora (Claude, ChatGPT,
Gemini): a pessoa diz a ideia em poucas palavras, copia o pedido (com o que o
PubliBot ja sabe do negocio e da conta), cola a resposta e ve a previa antes
de aplicar.

A resposta vem em rotulos fixos (como os outros pedidos para a outra IA) e e
lida por algoritmo: campo que nao veio fica como esta.
"""

from __future__ import annotations

import re

from apps.social import fontes, identidade
from core.resposta_ia import itens, ler_blocos

ROTULOS = [
    "ESTILO PREFERIDO",
    "ESTILOS PERMITIDOS",
    "ORIENTACOES GERAIS",
    "SO IMAGENS",
    "SO VIDEOS",
    "FOTO REALISTA QUANDO",
    "TEXTO NO VIDEO",
    "CHAMADA DO VIDEO",
    "ULTIMA LAMINA",
    "CORES",
    "PERSONAGENS",
    "COMENTARIOS",
]

# Rotulo -> campo da conta (os de texto).
CAMPOS = {
    "ORIENTACOES GERAIS": "descricao_do_estilo",
    "SO IMAGENS": "orientacao_imagens",
    "SO VIDEOS": "orientacao_videos",
    "FOTO REALISTA QUANDO": "cenarios_de_foto",
    "CHAMADA DO VIDEO": "chamada_do_video",
    "ULTIMA LAMINA": "chamada_final",
}
TAMANHOS = {"chamada_do_video": 200, "chamada_final": 160}
_COR = re.compile(r"#[0-9a-fA-F]{6}\b")

PEDIDO = """\
Me ajude a definir a IDENTIDADE VISUAL de uma conta de {rede} de um negocio:
o padrao que todas as imagens e videos feitos por IA vao seguir, para o perfil
ter uma linha reconhecivel.

A minha ideia, em poucas palavras: {ideia}

## O negocio
Tema: {tema}
Publico: {publico}
Oferta: {oferta}
Dores do publico: {dores}
Onde atende: {regioes}
Regras que nunca se quebram: {regras}

## A conta
Publico desta conta: {publico_da_conta}
Tom: {tom}
{atual}
## Como funciona (para voce acertar o que cada campo faz)
- As imagens e videos saem de IAs de imagem e de video, SEM texto escrito na
  imagem: o sistema escreve o texto das laminas por cima, com as letras certas.
- Um personagem fixo precisa de uma descricao que nunca muda (especie/idade,
  roupa, cores, acessorios, traco), para IAs diferentes desenharem igual.
  Traco simples sai mais parecido de um post para outro.
- Nada de pessoas reais reconheciveis, marcas de terceiros ou imagem chocante.
- O video sempre prende nos 2 primeiros segundos e termina numa chamada.

Responda EXATAMENTE com os rotulos abaixo, um por linha (pode quebrar o texto
dentro de um rotulo), sem mais nada antes:

ESTILO PREFERIDO: uma chave: foto, ilustracao, quadrinhos, apresentacao ou misto
ESTILOS PERMITIDOS: as chaves que a IA pode escolher post a post, por virgula ({escolhiveis})
ORIENTACOES GERAIS: traco, cores, luz, clima, o que evitar (vale para imagem e video)
SO IMAGENS: composicao das laminas (onde fica o espaco livre para o texto, enquadramento)
SO VIDEOS: ritmo, movimento, camera, som, clima dos videos
FOTO REALISTA QUANDO: (so se o preferido for misto) em que cenarios usar foto; senao "nenhum"
TEXTO NO VIDEO: nenhum, legendas ou frases
CHAMADA DO VIDEO: como os videos terminam (a chamada padrao)
ULTIMA LAMINA: a frase da ultima lamina do carrossel (ate 160 caracteres)
CORES: fundo #rrggbb, texto #rrggbb, destaque #rrggbb
PERSONAGENS:
- nome | personagem, cenario ou objeto | descricao fixa e detalhada
(ou "nenhum")
COMENTARIOS: o que mais voce sugere (opcional)
"""


def pedido(destino, ideia: str) -> str:
    from apps.social.models import ConfiguracaoSocial
    from apps.social.redes import rede

    negocio = fontes.negocio()
    r = rede(destino.rede)
    atual = identidade.resumo_dos_estilos(destino)
    if identidade.elementos(destino):
        atual += "\nPersonagens ja existentes:\n" + identidade.ficha(destino)
    return PEDIDO.format(
        rede=r.nome,
        ideia=ideia.strip() or "(nenhuma: proponha voce, a partir do negocio)",
        tema=negocio.get("tema") or "(nao informado)",
        publico=negocio.get("publico") or "(nao informado)",
        oferta=negocio.get("oferta") or "(nao informada)",
        dores="; ".join(negocio.get("dores") or []) or "(nenhuma)",
        regioes=", ".join(negocio.get("regioes") or []) or "(nao informado)",
        regras=ConfiguracaoSocial.carregar().regras.strip() or "(nenhuma)",
        publico_da_conta=destino.publico or r.publico_padrao,
        tom=destino.tom or r.tom_padrao,
        atual=f"O que a conta tem hoje (pode melhorar): {atual}\n",
        escolhiveis=", ".join(identidade.ESCOLHIVEIS),
    )


def _uma_linha(texto: str) -> str:
    return " ".join((texto or "").split())


def ler(resposta: str) -> dict:
    """{campo: valor} so do que veio e e valido; "elementos": [{nome, tipo,
    descricao}]; "comentarios"."""
    from apps.social.models import ElementoVisual

    blocos = ler_blocos(resposta, ROTULOS)
    dados: dict = {}
    preferido = _uma_linha(blocos.get("ESTILO PREFERIDO", "")).lower().strip(" .")
    if preferido in (*identidade.ESCOLHIVEIS, "misto"):
        dados["estilo_das_imagens"] = preferido
    permitidos = [
        e
        for e in re.split(r"[,;/\s]+", blocos.get("ESTILOS PERMITIDOS", "").lower())
        if e in identidade.ESCOLHIVEIS
    ]
    if permitidos:
        dados["estilos_permitidos"] = list(dict.fromkeys(permitidos))
    for rotulo, campo in CAMPOS.items():
        valor = blocos.get(rotulo, "").strip()
        if valor and _uma_linha(valor).lower().strip(" .") not in ("nenhum", "nenhuma", "-"):
            dados[campo] = valor[: TAMANHOS.get(campo, 2000)]
    texto = _uma_linha(blocos.get("TEXTO NO VIDEO", "")).lower()
    for chave in ("legendas", "frases", "nenhum"):
        if texto.startswith(chave):
            dados["texto_no_video"] = "" if chave == "nenhum" else chave
    cores = _COR.findall(blocos.get("CORES", ""))
    if len(cores) >= 3:
        dados["cores"] = {"fundo": cores[0], "texto": cores[1], "destaque": cores[2]}
    elementos = []
    tipos = {t.label.lower(): t.value for t in ElementoVisual.Tipo} | {
        t.value: t.value for t in ElementoVisual.Tipo
    }
    for linha in itens(blocos.get("PERSONAGENS", "")):
        partes = [p.strip() for p in linha.split("|")]
        if len(partes) < 3 or not partes[0] or not partes[2]:
            continue
        tipo = tipos.get(partes[1].lower().strip(" ."), ElementoVisual.Tipo.PERSONAGEM)
        elementos.append({"nome": partes[0][:60], "tipo": tipo, "descricao": partes[2]})
    if elementos:
        dados["elementos"] = elementos
    dados["comentarios"] = blocos.get("COMENTARIOS", "").strip()
    return dados


ROTULOS_DA_PREVIA = {
    "estilo_das_imagens": "Estilo preferido",
    "estilos_permitidos": "Estilos permitidos",
    "descricao_do_estilo": "Orientacoes gerais",
    "orientacao_imagens": "So para as imagens",
    "orientacao_videos": "So para os videos",
    "cenarios_de_foto": "Foto realista quando",
    "texto_no_video": "Texto no video",
    "chamada_do_video": "Como o video termina",
    "chamada_final": "Ultima lamina",
    "cores": "Cores",
}


def previa(destino, dados: dict) -> list[tuple[str, str, str]]:
    """[(rotulo, como esta, como fica)] do que muda."""
    saida = []
    for campo, rotulo in ROTULOS_DA_PREVIA.items():
        if campo not in dados:
            continue
        antes, depois = getattr(destino, campo), dados[campo]
        if campo == "cores":
            antes = ", ".join(f"{k} {v}" for k, v in (antes or {}).items() if v)
            depois = ", ".join(f"{k} {v}" for k, v in depois.items())
        elif isinstance(depois, list):
            antes, depois = ", ".join(antes or []), ", ".join(depois)
        if str(antes or "") != str(depois or ""):
            saida.append((rotulo, str(antes or "—"), str(depois or "—")))
    for e in dados.get("elementos") or []:
        saida.append((f"Personagem/cenario: {e['nome']}", "", e["descricao"]))
    return saida


def aplicar(destino, dados: dict) -> int:
    """Grava o que veio na conta (e os personagens). Devolve quantos campos mudaram."""
    from apps.social.models import ElementoVisual

    mudados = []
    for campo in ROTULOS_DA_PREVIA:
        if campo in dados:
            valor = dados[campo]
            if campo == "cores":
                valor = {**(destino.cores or {}), **valor}
            if getattr(destino, campo) != valor:
                setattr(destino, campo, valor)
                mudados.append(campo)
    if mudados:
        destino.save(update_fields=mudados)
    for e in dados.get("elementos") or []:
        ElementoVisual.objects.update_or_create(
            destino=destino,
            nome=e["nome"],
            defaults={"tipo": e["tipo"], "descricao": e["descricao"], "ativo": True},
        )
    return len(mudados) + len(dados.get("elementos") or [])
