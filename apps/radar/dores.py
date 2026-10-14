"""As dores do publico, nas palavras dele.

A copy que vende comeca pela dor de quem le, com a frase que ele mesmo usaria
("voz do cliente": a pesquisa de copy junta reclamacoes e relatos, agrupa por
tema e conta frequencia e emocao). Aqui isso vira dado:

* **Fala** — o que alguem disse: um comentario do YouTube que desabafa ou
  pergunta, uma avaliacao ruim de um concorrente no Google, um comentario num
  post da conta. Quem decide se a frase e dor e um filtro de palavras (sem
  modelo); a intensidade sai das mesmas palavras (marcas de sofrimento,
  frustracao, medo, mais os intensificadores).
* **Dor** — as falas que dizem o mesmo problema, juntas por embedding, do
  jeito do agrupamento dos sinais (incremental: uma dor que ja foi usada nao
  muda de nome). As dores digitadas na Configuracao do Radar entram como
  sementes: funcionam no primeiro dia, antes de existir fala colhida, e as
  falas parecidas se juntam a elas.

Quem usa: o post de cada rede prefere as falas da propria rede (o Instagram
fala diferente do Google), e cai nas outras enquanto a rede ainda nao tem
falas suficientes; o artigo recebe as dores ligadas a pauta.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import unicodedata

import numpy as np
from django.conf import settings

from apps.radar.models import ConfiguracaoDoRadar, DorDoPublico, FalaDoPublico

logger = logging.getLogger("publibot.radar")

MINIMO, MAXIMO = 15, 600
# Mais perto que isto do centroide de uma dor, a fala entra nela.
DISTANCIA_DA_DOR = 0.13
# Uma dor so vale para um texto (post, pauta) se estiver ao menos tao perto.
DISTANCIA_PARA_USAR = 0.22

# Sofrimento, frustracao, medo, perda, esforco que nao deu certo. Sem acento.
_DOR = re.compile(
    r"\b(nao aguento|nao aguentava|cansad\w*|exaust\w*|sofr\w*|dor(es)?\b|doi\b|doendo|"
    r"medo|receio|preocupad\w*|angusti\w*|ansios\w*|desesper\w*|frustrad\w*|"
    r"vergonha|constrang\w*|dificil|dificuldade|nao consigo|nao conseguia|"
    r"nao sei mais|ja tentei|tentei de tudo|nada (funciona|resolve|adianta)|"
    r"desisti|piorou|pior|horrivel|pessim\w*|odeio|triste|sozinh\w*|"
    r"perdi|perdendo|gastei|gastando|prejuizo|caro demais|demor\w*|"
    r"esperei|esperando ha|ninguem (me )?(explica|ajuda|resolve)|socorro|"
    r"problema|incomod\w*|atrapalh\w*|insonia|nao durmo|estress\w*|"
    r"sem tempo|atrasad\w*|confus\w*|perdid[oa]s?|travad[oa]s?)\b"
)
# Reclamacao de servico (o que a avaliacao ruim de um concorrente diz).
_RECLAMACAO = re.compile(
    r"\b(pessim\w*|horrivel|absurdo|descaso|demor\w*|atras\w*|nao atende\w*|"
    r"nao retorn\w*|mal atendid\w*|grosseir\w*|caro demais|cobra\w* a mais|"
    r"enganad\w*|nao recomendo|nunca mais|falta de respeito|ninguem resolve|"
    r"nao explic\w*|nao resolveu|nao gostei|reclama\w*)\b"
)
# Primeira pessoa: e o relato de quem sente, e nao opiniao sobre os outros.
_EU = re.compile(
    r"\b(eu|meu|minha|meus|minhas|comigo|mim|tenho|tinha|estou|to|tou|sinto|"
    r"senti|fico|fiquei|minha mae|meu pai|meu filho|minha filha|meu marido|"
    r"minha esposa|aqui em casa|na minha)\b"
)
_INTENSO = re.compile(
    r"\b(muito|demais|sempre|nunca|todo dia|todos os dias|toda vez|toda noite|"
    r"ha anos|ha meses|insuportavel|extremamente|super|mais nada)\b"
)
_INTERROGATIVA = re.compile(
    r"^(como|quanto|quantos|quantas|qual|quais|quando|onde|por que|porque|pq|"
    r"pode|posso|devo|e normal|tem como|da pra|da para|vale a pena|alguem sabe|"
    r"o que|existe|serve)\b"
)


def normalizar(texto: str) -> str:
    sem = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return " ".join(sem.lower().split())


def intensidade(texto: str) -> float:
    """0 a 1: quantas marcas de dor e de intensidade a frase tem."""
    limpo = normalizar(texto)
    marcas = len(_DOR.findall(limpo)) + len(_RECLAMACAO.findall(limpo))
    if not marcas:
        return 0.0
    reforco = len(_INTENSO.findall(limpo)) + min(texto.count("!"), 3) * 0.5
    maiusculas = sum(1 for p in texto.split() if len(p) > 3 and p.isupper())
    return round(min(1.0, (marcas + 0.5 * reforco + 0.5 * min(maiusculas, 2)) / 3), 3)


def classificar(texto: str) -> str:
    """O tipo da fala, ou "" se ela nao diz dor nenhuma."""
    limpo = normalizar(texto)
    if not MINIMO <= len(limpo) <= MAXIMO or "http" in limpo:
        return ""
    doi = bool(_DOR.search(limpo))
    if "?" in limpo or _INTERROGATIVA.search(limpo):
        # Pergunta so e dor quando carrega o problema ("como paro de acordar
        # cansado?"); a pergunta neutra ja e sinal de demanda do Radar.
        return FalaDoPublico.Tipo.PERGUNTA if doi else ""
    if _RECLAMACAO.search(limpo):
        return FalaDoPublico.Tipo.RECLAMACAO
    if doi and _EU.search(limpo):
        return FalaDoPublico.Tipo.DESABAFO
    return ""


def _chave(texto: str) -> str:
    return hashlib.sha256(normalizar(texto).encode()).hexdigest()


def registrar(texto: str, fonte: str, *, tipo: str = "", **extra) -> FalaDoPublico | None:
    """Guarda a fala, se ela for dor (ou se quem chamou ja sabe o tipo, como
    a avaliacao de nota baixa). A mesma frase nao entra duas vezes."""
    texto = re.sub(r"\s+", " ", texto or "").strip()
    tipo = classificar(texto) or tipo
    if not tipo or not MINIMO <= len(texto) <= MAXIMO:
        return None
    fala, criada = FalaDoPublico.objects.get_or_create(
        chave=_chave(texto),
        defaults={
            "texto": texto,
            "fonte": fonte,
            "tipo": tipo,
            "intensidade": intensidade(texto),
            "extra": extra,
        },
    )
    return fala if criada else None


def _vetores(textos: list[str]) -> np.ndarray:
    from apps.knowledge.embeddings import get_embedding_client

    v = np.asarray(get_embedding_client().embed_queries(textos), dtype=np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def semear_digitadas(*, com_vetores: bool = True) -> None:
    """As dores da Configuracao do Radar viram dores (sem fala ainda).

    `com_vetores=False` (a tela): so cria as linhas, na hora; o centroide vem
    depois, no agrupamento (que roda em segundo plano)."""
    digitadas = ConfiguracaoDoRadar.carregar().lista_de_dores
    na_config = {normalizar(d) for d in digitadas}
    existentes = set()
    for dor in DorDoPublico.objects.filter(digitada=True):
        if normalizar(dor.rotulo) in na_config:
            existentes.add(normalizar(dor.rotulo))
        elif dor.falas_total:  # tirada da lista, mas com falas: fica, como colhida
            dor.digitada = False
            dor.save(update_fields=["digitada", "atualizada_em"])
        else:
            dor.delete()
    for rotulo in digitadas:
        if normalizar(rotulo) not in existentes:
            existentes.add(normalizar(rotulo))
            DorDoPublico.objects.create(rotulo=rotulo[:300], digitada=True, nota=0.5)
    sem_vetor = list(DorDoPublico.objects.filter(digitada=True, centroide__isnull=True))
    if not com_vetores or not sem_vetor:
        return
    for dor, vetor in zip(sem_vetor, _vetores([d.rotulo for d in sem_vetor]), strict=True):
        dor.centroide = vetor.tolist()
        dor.save(update_fields=["centroide", "atualizada_em"])


def agrupar() -> int:
    """Poe cada fala sem dor numa dor (ou abre uma). Devolve quantas agrupou.
    Sem vetores agora: fica para a proxima vez."""
    limiar = float(getattr(settings, "RADAR_DISTANCIA_DA_DOR", DISTANCIA_DA_DOR))
    soltas = list(FalaDoPublico.objects.filter(dor__isnull=True).order_by("-intensidade")[:500])
    try:
        semear_digitadas()
        if not soltas:
            return 0
        vetores = _vetores([f.texto for f in soltas])
    except Exception as exc:
        logger.info("Dores nao agrupadas agora (sem vetores): %s", exc)
        return 0
    dores = list(DorDoPublico.objects.filter(descartada=False).exclude(centroide__isnull=True))
    centroides = [np.asarray(d.centroide, dtype=np.float32) for d in dores]
    tocadas = {}
    for fala, vetor in zip(soltas, vetores, strict=True):
        melhor, distancia = None, 2.0
        for i, c in enumerate(centroides):
            d = 1.0 - float(vetor @ c / (np.linalg.norm(c) + 1e-9))
            if d < distancia:
                melhor, distancia = i, d
        if melhor is not None and distancia <= limiar:
            dor = dores[melhor]
            # A digitada conta como uma fala: a semente pesa no centroide.
            n = max(1, dor.falas_total + (1 if dor.digitada else 0))
            centroides[melhor] = (centroides[melhor] * n + vetor) / (n + 1)
            dor.centroide = centroides[melhor].tolist()
        else:
            dor = DorDoPublico.objects.create(rotulo=fala.texto[:300], centroide=vetor.tolist())
            dores.append(dor)
            centroides.append(vetor)
        fala.dor = dor
        fala.save(update_fields=["dor"])
        dor.falas_total += 1
        tocadas[dor.pk] = dor
    for dor in tocadas.values():
        recalcular(dor)
    return len(soltas)


def nota(falas: int, media: float, fontes: int, *, digitada: bool) -> float:
    """Frequencia (log), emocao e quantos lugares dizem a mesma coisa. A
    digitada sem fala vale um pouco: e a aposta de quem conhece o publico."""
    if not falas:
        return 0.5 if digitada else 0.0
    return round(math.log1p(falas) * (0.5 + media) * (1 + 0.25 * max(0, fontes - 1)), 3)


def recalcular(dor: DorDoPublico) -> None:
    falas = list(dor.falas.values_list("fonte", "intensidade"))
    por_fonte: dict[str, int] = {}
    for fonte, _i in falas:
        por_fonte[fonte] = por_fonte.get(fonte, 0) + 1
    dor.falas_total = len(falas)
    dor.intensidade = round(sum(i for _f, i in falas) / len(falas), 3) if falas else 0.0
    dor.por_fonte = por_fonte
    dor.nota = nota(len(falas), dor.intensidade, len(por_fonte), digitada=dor.digitada)
    dor.save()


def _falas(dor: DorDoPublico, rede: str, quantas: int) -> list[str]:
    """As falas mais intensas, as da propria rede primeiro."""
    todas = list(dor.falas.all()[:30])
    todas.sort(key=lambda f: (f.fonte != rede, -f.intensidade))
    return [f.texto for f in todas[:quantas]]


def dores_para(texto: str, *, rede: str = "", quantas: int = 3, falas: int = 2) -> list[dict]:
    """As dores ligadas a um texto (o artigo, a pauta), com as falas.

    [{"dor": rotulo, "falas": [...], "nota": n}]. A proximidade escolhe; a nota
    desempata. Sem vetores ou sem dor perto: as digitadas (o meio-tempo de
    quem ainda nao colheu fala)."""
    limite = float(getattr(settings, "RADAR_DISTANCIA_PARA_USAR_DOR", DISTANCIA_PARA_USAR))
    dores = list(DorDoPublico.objects.filter(descartada=False).exclude(centroide__isnull=True))
    escolhidas = []
    if dores and texto.strip():
        try:
            from apps.knowledge.embeddings import get_embedding_client

            # Uma consulta so (prazo curto): tambem e chamada dentro de tela.
            alvo = np.asarray(get_embedding_client().embed_query(texto[:2000]), dtype=np.float32)
            alvo /= np.linalg.norm(alvo) + 1e-9
        except Exception as exc:
            logger.info("Dores sem vetores agora: %s", exc)
        else:
            c = np.asarray([d.centroide for d in dores], dtype=np.float32)
            c /= np.linalg.norm(c, axis=1, keepdims=True) + 1e-9
            distancias = 1.0 - c @ alvo
            perto = [
                (float(distancias[i]), d) for i, d in enumerate(dores) if distancias[i] <= limite
            ]
            # Mais perto primeiro; entre as parecidas (0,02), a de nota maior.
            perto.sort(key=lambda par: (round(par[0] / 0.02), -par[1].nota))
            escolhidas = [d for _dist, d in perto[:quantas]]
    if not escolhidas:
        return [
            {"dor": d, "falas": [], "nota": 0.5}
            for d in ConfiguracaoDoRadar.carregar().lista_de_dores[:quantas]
        ]
    return [{"dor": d.rotulo, "falas": _falas(d, rede, falas), "nota": d.nota} for d in escolhidas]


def em_linhas(dores: list[dict]) -> list[str]:
    """Para o prompt: "dor — "fala"; "fala"" (a fala vai entre aspas: e citacao)."""
    linhas = []
    for item in dores:
        falas = "; ".join(f"“{f[:220]}”" for f in item["falas"])
        linhas.append(f"{item['dor']}" + (f" — {falas}" if falas else ""))
    return linhas


def principais(quantas: int = 20) -> list[DorDoPublico]:
    """Para a tela: as dores com mais evidencia."""
    return list(
        DorDoPublico.objects.filter(descartada=False).order_by("-nota", "-falas_total")[:quantas]
    )
