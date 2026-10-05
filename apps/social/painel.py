"""Contagens e alertas para o nucleo (via apps/ops/extensoes.py)."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

# Leitura automatica parada ha mais que isto vira aviso (um dia de folga para
# instabilidade passageira da rede).
DIAS_SEM_LER = 2
DIAS_SEM_ANUNCIOS = 3
# Sem API de anuncios: lembrete para mandar a planilha de novo.
DIAS_DA_PLANILHA = 35


def leitura_parada(destino) -> str:
    """O aviso de leitura automatica parada desta conta ('' se esta em dia)."""
    agora = timezone.now()
    avisos = []
    if destino.ligado and destino.conectado:
        ultimo = destino.sincronizado_em or destino.conectado_em
        if ultimo and ultimo < agora - timedelta(days=DIAS_SEM_LER):
            texto = f"{destino.nome}: {(agora - ultimo).days} dias sem atualizar sozinho"
            if destino.erro_de_sincronia:
                texto += f" (ultimo erro: {destino.erro_de_sincronia[:200]})"
            avisos.append(
                texto + ". Se foi instabilidade da rede, volta sozinho; se continuar, "
                "conecte a conta de novo em Redes > Configurar."
            )
    if destino.anuncios_conta_id:
        ultimo = destino.anuncios_sincronizados_em or destino.conectado_em
        if ultimo is None or ultimo < agora - timedelta(days=DIAS_SEM_ANUNCIOS):
            dias = (agora - ultimo).days if ultimo else None
            texto = f"{destino.nome}: gasto com anuncios sem atualizar" + (
                f" ha {dias} dias" if dias is not None else ""
            )
            if destino.anuncios_erro:
                texto += f" (a Meta respondeu: {destino.anuncios_erro[:200]})"
            avisos.append(
                texto + ". Enquanto isso, envie a planilha do Gerenciador em Redes > Diagnostico."
            )
    elif destino.anuncios_planilha_em and destino.anuncios_planilha_em < agora - timedelta(
        days=DIAS_DA_PLANILHA
    ):
        avisos.append(
            f"{destino.nome}: a planilha de anuncios tem "
            f"{(agora - destino.anuncios_planilha_em).days} dias. Envie a do mes "
            "(Redes > Diagnostico) para o gasto entrar na conta."
        )
    return " ".join(avisos)


def pendencias() -> dict:
    from apps.social.models import Destino, Post

    paradas = sum(1 for d in Destino.objects.filter(ligado=True) if leitura_parada(d))
    return {"redes": Post.objects.filter(situacao=Post.Situacao.RASCUNHO).count() + paradas}


def alertas() -> list[str]:
    """Para o painel do cliente: leituras automaticas paradas e banco de fotos
    acabando (conta que vive de foto)."""
    from apps.social.models import Destino
    from apps.social.proprio import estoque

    saida = [texto for d in Destino.objects.filter(ligado=True) if (texto := leitura_parada(d))]
    for destino in Destino.objects.filter(ligado=True, fotos_por_cento__gt=0):
        e = estoque(destino)
        if e["baixo"]:
            saida.append(
                f"{destino.nome}: o banco de fotos da para ~{e['dias']} dia(s) "
                f"({e['grupos']} atendimento(s)). Envie fotos novas em Redes > Fotos."
            )
    return saida
