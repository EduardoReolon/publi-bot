"""A ideia em segundo plano: o clique so grava e dispara."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("publibot.ideias")


@shared_task(bind=True, max_retries=24)
def processar_ideia(self, ideia_id: str) -> str:
    """Transcreve o audio (se houver; placa ocupada: tenta de novo em 10
    minutos), le, cria a pauta e busca. Despachada de dentro do tenant."""
    from apps.ideias.investigacao import processar
    from apps.ideias.models import Ideia
    from apps.knowledge.extraction import ConversorOcupado, ExtracaoIndisponivel, transcrever_audio

    ideia = Ideia.objects.filter(pk=ideia_id).first()
    if ideia is None:
        return "nada a fazer"
    if ideia.audio and not ideia.transcricao:
        ideia.situacao = Ideia.Situacao.TRANSCREVENDO
        ideia.save(update_fields=["situacao", "atualizada_em"])
        with ideia.audio.open("rb") as arquivo:
            conteudo = arquivo.read()
        try:
            segmentos, _duracao = transcrever_audio(
                ideia.audio.name.rsplit("/", 1)[-1],
                conteudo,
                idioma="pt",
                dono=f"ideia:{ideia.pk}",
                timeout=600.0,
            )
            ideia.transcricao = " ".join(t.strip() for _s, t in segmentos).strip()[:20000]
            ideia.save(update_fields=["transcricao", "atualizada_em"])
        except ConversorOcupado as exc:
            raise self.retry(exc=exc, countdown=600) from exc
        except ExtracaoIndisponivel as exc:
            logger.warning("Audio da ideia %s nao transcrito: %s", ideia.pk, exc)
            if not ideia.texto.strip():
                ideia.situacao = Ideia.Situacao.ERRO
                ideia.erro = f"o audio nao foi transcrito: {exc}"[:2000]
                ideia.save(update_fields=["situacao", "erro", "atualizada_em"])
                return ideia.situacao
    processar(ideia)
    return ideia.situacao
