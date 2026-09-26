"""Videos como fonte: a legenda vira documento, e o audio e o plano B.

Dois caminhos para o texto de um video:

1. **A legenda do YouTube** (manual ou automatica), pela biblioteca
   `youtube-transcript-api`. Nao e API oficial — a oficial so entrega a legenda
   dos videos do proprio dono do canal —, e o YouTube costuma bloquear pedidos
   vindos de servidores em nuvem. Quando bloqueia, ou quando o video nao tem
   legenda, o candidato fica AGUARDANDO O AUDIO.
2. **O audio enviado pela pessoa**, transcrito no worker da placa (Whisper,
   rota `/v1/audio/transcriptions`) quando a GPU estiver livre.

Nos dois casos o texto vira Markdown com uma secao por trecho de tempo
(`## 03:15`): e o que a curadoria usa para marcar blocos, e o que permite a
citacao apontar o minuto certo.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import re
from urllib.parse import parse_qs, urlparse

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.knowledge.models import CandidatoDeFonte, Document, DocumentCategory

logger = logging.getLogger("publibot.knowledge")

# Uma secao a cada tanto de fala. Curto demais picota o raciocinio; longo
# demais faz um bloco so cobrir tres assuntos.
SEGUNDOS_POR_SECAO = 90

EXTENSOES_DE_AUDIO = (".mp3", ".m4a", ".wav", ".ogg", ".oga", ".opus", ".webm", ".mp4", ".flac")


class LegendaIndisponivel(RuntimeError):
    """Sem legenda, ou o YouTube recusou este servidor."""


def id_do_video(url: str) -> str:
    partes = urlparse(url)
    anfitriao = (partes.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if anfitriao == "youtu.be":
        return partes.path.strip("/").split("/")[0]
    if anfitriao.endswith("youtube.com"):
        if partes.path == "/watch":
            return (parse_qs(partes.query).get("v") or [""])[0]
        achado = re.match(r"^/(?:shorts|embed|live)/([\w-]+)", partes.path)
        if achado:
            return achado.group(1)
    return ""


def url_do_video(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _tempo(segundos: float) -> str:
    total = int(segundos)
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{segs:02d}" if horas else f"{minutos:02d}:{segs:02d}"


def markdown_da_transcricao(titulo: str, trechos: list[tuple[float, str]]) -> str:
    """`trechos` = [(inicio em segundos, texto)], na ordem. Uma secao por janela."""
    partes = [f"# {titulo}"] if titulo else []
    janela_inicio, janela = None, []
    for inicio, texto in trechos:
        texto = " ".join((texto or "").split())
        if not texto:
            continue
        if janela_inicio is None:
            janela_inicio = inicio
        if inicio - janela_inicio >= SEGUNDOS_POR_SECAO and janela:
            partes.append(f"## {_tempo(janela_inicio)}\n\n{' '.join(janela)}")
            janela_inicio, janela = inicio, []
        janela.append(texto)
    if janela:
        partes.append(f"## {_tempo(janela_inicio or 0)}\n\n{' '.join(janela)}")
    return "\n\n".join(partes)


def buscar_legenda(video_id: str, idiomas=("pt", "pt-BR", "en")) -> list[tuple[float, str]]:
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        YouTubeTranscriptApiException,
    )

    try:
        legenda = YouTubeTranscriptApi().fetch(video_id, languages=list(idiomas))
    except YouTubeTranscriptApiException as exc:
        nome = type(exc).__name__
        if nome in {"RequestBlocked", "IpBlocked", "PoTokenRequired"}:
            raise LegendaIndisponivel(
                "o YouTube recusou a leitura da legenda a partir deste servidor."
            ) from exc
        raise LegendaIndisponivel(f"o video nao tem legenda disponivel ({nome}).") from exc
    return [(trecho.start, trecho.text) for trecho in legenda]


def _categoria_de_video() -> DocumentCategory:
    from apps.knowledge.perfis import categoria_da_natureza

    return categoria_da_natureza("video")


def aprovar_video(
    candidato: CandidatoDeFonte,
    *,
    categoria: DocumentCategory | None = None,
    por=None,
    automatico: bool = False,
) -> CandidatoDeFonte:
    """Legenda -> documento. Sem legenda, o candidato espera o audio."""
    from apps.knowledge.fontes_web import curar_automaticamente

    categoria = categoria or _categoria_de_video()
    video_id = id_do_video(candidato.url)
    try:
        trechos = buscar_legenda(video_id)
    except LegendaIndisponivel as exc:
        candidato.situacao = CandidatoDeFonte.Situacao.AGUARDANDO_AUDIO
        candidato.motivo = (
            f"{exc} Baixe o audio do video (por exemplo com o yt-dlp, ou um site de "
            f"download de audio do YouTube) e envie aqui: a transcricao roda no "
            f"worker quando a placa estiver livre."
        )
        candidato.decidido_por = por
        candidato.decidido_em = timezone.now()
        candidato.save()
        return candidato

    markdown = markdown_da_transcricao(candidato.titulo, trechos)
    bruto = markdown.encode("utf-8")
    sha = hashlib.sha256(bruto).hexdigest()
    documento = Document.objects.filter(file_sha256=sha).first() or Document.objects.create(
        category=categoria,
        original_file=ContentFile(bruto, name=f"video-{video_id}.md"),
        file_sha256=sha,
        file_size_bytes=len(bruto),
        title=candidato.titulo[:500],
        authors=candidato.canal_nome[:300],
        year=candidato.publicado_em.year if candidato.publicado_em else None,
        published_on=candidato.publicado_em,
        fetched_at=timezone.now(),
        source_url=candidato.url,
        origin=Document.Origin.YOUTUBE,
        markdown_full=markdown,
        extraction_method=Document.ExtractionMethod.SUBTITLES,
        uploaded_by=por,
        status=Document.Status.PENDING_CURATION,
    )
    if automatico and documento.status != Document.Status.CURATED:
        curar_automaticamente(documento)

    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.motivo = "aprovado por canal confiavel" if automatico else ""
    candidato.save()
    return candidato


def receber_audio(candidato: CandidatoDeFonte, arquivo, *, por=None) -> Document:
    """O audio que a pessoa baixou: vira documento e vai para a transcricao."""
    from apps.knowledge.services import ingerir_documento
    from apps.knowledge.tasks import iniciar_ingestao

    resultado = ingerir_documento(arquivo=arquivo, category=_categoria_de_video(), uploaded_by=por)
    documento = resultado.document
    if not resultado.ja_existia:
        documento.title = candidato.titulo[:500]
        documento.authors = candidato.canal_nome[:300]
        documento.source_url = candidato.url
        documento.published_on = candidato.publicado_em
        documento.year = candidato.publicado_em.year if candidato.publicado_em else None
        documento.origin = Document.Origin.YOUTUBE
        documento.save()
        iniciar_ingestao(documento)

    candidato.situacao = CandidatoDeFonte.Situacao.APROVADO
    candidato.documento = documento
    candidato.decidido_por = por
    candidato.decidido_em = timezone.now()
    candidato.save()
    return documento


def data_do_youtube(valor: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat((valor or "")[:10])
    except ValueError:
        return None
