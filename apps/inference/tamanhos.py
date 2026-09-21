"""O tamanho pedido ao gerador de imagem, conferido contra o que ele aceita.

Tres limites independentes, e a distincao importa porque cada um pede uma
correcao diferente:

- **multiplo de 8** — o espaco latente da difusao e 8x menor que a imagem. Um
  lado que nao e multiplo de 8 seria arredondado por dentro, e a imagem
  voltaria de tamanho diferente do pedido sem aviso;
- **lado maximo** — o que a placa daquela maquina comporta;
- **area maxima** — o que o custo daquela maquina comporta. Precisa existir
  separado: um teto so de lado deixaria passar 1536x1536, que e o dobro da
  area de treino.

E uma quarta conferencia, que nao e limite e sim qualidade: a **grade**. O
SDXL foi treinado numa lista de proporcoes a area quase constante (~1,05 MP).
Fora dela ele nao recusa — ele duplica o assunto e torce a geometria, sem
erro nenhum. O worker avisa; aqui se avisa antes, na configuracao, que e onde
da para consertar sem gastar placa.

**A grade vem do `/health/` do worker, nao daqui.** Uma copia no cliente e
exatamente o mecanismo que deixou `busy` virar `ocupada` sem ninguem notar: a
lista envelhece em silencio, e o sintoma aparece como imagem feia.
"""

from __future__ import annotations


def _medidas(tamanho: str) -> tuple[int, int] | None:
    try:
        largura, altura = (int(parte) for parte in str(tamanho).lower().split("x", 1))
    except ValueError:
        return None
    return (largura, altura) if largura > 0 and altura > 0 else None


def conferir(tamanho: str, estado_da_imagem: dict) -> list[str]:
    """Avisos sobre `tamanho`, a partir do que o worker publica em `/health/`.

    Devolve uma lista de frases prontas, da mais grave para a menos. Lista
    vazia quer dizer que o pedido passa e esta na grade.

    Avisos, e nao excecoes: um provedor pago tem outras regras, e um modelo
    afinado pode ter sido retreinado noutra grade. Quem sabe disso e quem
    configurou; isto so garante que ninguem descubra pela imagem.
    """
    medidas = _medidas(tamanho)
    if medidas is None:
        return [f"IMAGEM_TAMANHO={tamanho!r} nao tem a forma LARGURAxALTURA."]

    largura, altura = medidas
    avisos = []

    for nome, medida in (("largura", largura), ("altura", altura)):
        if medida % 8:
            avisos.append(
                f"{largura}x{altura}: a {nome} nao e multiplo de 8. O worker recusa com "
                f"422 — o modelo arredondaria por dentro e devolveria outro tamanho sem "
                f"avisar. (1200x630, o alvo das redes, cai aqui: 630 nao e multiplo de 8.)"
            )

    lado_maximo = estado_da_imagem.get("lado_maximo")
    if isinstance(lado_maximo, int) and max(largura, altura) > lado_maximo:
        avisos.append(
            f"{largura}x{altura}: passa de {lado_maximo}px por lado, o teto desta maquina. "
            f"O worker recusa com 422. Ajuste IMAGEM_LADO_MAXIMO no .env do worker se a "
            f"placa comportar mais."
        )

    area_maxima = estado_da_imagem.get("area_maxima_mp")
    megapixels = largura * altura / 1_000_000
    if isinstance(area_maxima, (int, float)) and megapixels > area_maxima:
        avisos.append(
            f"{largura}x{altura}: {megapixels:.2f} MP passa do teto de {area_maxima} MP "
            f"desta maquina. O worker recusa com 422. Ajuste IMAGEM_AREA_MAXIMA_MP no .env "
            f"do worker se ela comportar mais."
        )

    grade = estado_da_imagem.get("grade")
    if isinstance(grade, list) and grade and f"{largura}x{altura}" not in grade:
        avisos.append(
            f"{largura}x{altura} nao esta na grade de treino publicada pelo worker. Ele "
            f"NAO vai recusar — vai entregar imagens piores, com assunto duplicado e "
            f"geometria torta, sem nada no log deste lado.\n"
            f"  Proximos na grade: {', '.join(_vizinhos(largura, altura, grade))}."
        )

    return avisos


def _vizinhos(largura: int, altura: int, grade: list[str], quantos: int = 3) -> list[str]:
    """Os tamanhos da grade com a proporcao mais parecida.

    Por PROPORCAO e nao por area: a grade inteira tem area quase igual, entao
    ordenar por area devolveria uma lista arbitraria. Quem pede 1344x704 quer
    um formato, e e o formato que precisa ser preservado na sugestao.
    """
    alvo = largura / altura

    def distancia(item: str) -> float:
        medidas = _medidas(item)
        if medidas is None:
            return float("inf")
        return abs(medidas[0] / medidas[1] - alvo)

    return sorted(grade, key=distancia)[:quantos]
