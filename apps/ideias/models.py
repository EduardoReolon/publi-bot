"""A ideia como ela chega (texto ou audio) e o que o PubliBot faz com ela."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Ideia(models.Model):
    class Situacao(models.TextChoices):
        NOVA = "nova", _("Na fila")
        OUTRA_IA = "outra_ia", _("Esperando a resposta da outra IA")
        TRANSCREVENDO = "audio", _("Transcrevendo o audio")
        LENDO = "lendo", _("O modelo esta lendo")
        BUSCANDO = "buscando", _("Buscando fontes")
        CURADORIA = "curadoria", _("Esperando a sua curadoria")
        # Voce aprovou a ideia (ou um artigo que nasceu dela): pode ir para as
        # redes, com ou sem artigo no site.
        APROVADA = "aprovada", _("Aprovada")
        PAUTA = "pauta", _("Virou pauta")
        DESCARTADA = "descartada", _("Descartada")
        ERRO = "erro", _("Erro")

    class Onde(models.TextChoices):
        SITE = "site", _("Artigo no site")
        REDES = "redes", _("Post nas redes")
        OS_DOIS = "os_dois", _("Os dois")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    texto = models.TextField(_("a ideia"), blank=True)
    audio = models.FileField(upload_to="ideias/audios/%Y/%m/", max_length=300, blank=True)
    transcricao = models.TextField(blank=True)
    # Links que a pessoa ja tem (a materia que viu): entram como DISCURSO sugerido.
    links = models.JSONField(default=list, blank=True)
    situacao = models.CharField(max_length=10, choices=Situacao.choices, default=Situacao.NOVA)
    # O que o modelo leu: {"titulo", "afirmacao", "tese", "linhas": [...], "onde",
    # "buscas": {"discurso": [...], "a_favor": [...], "contra": [...]},
    # "videos": bool, "estudos": bool}. Ver leitura.py.
    leitura = models.JSONField(default=dict, blank=True)
    # Quantas sugestoes cada busca trouxe: {"discurso": n, "a_favor": n, "contra": n,
    # "citadas": n, "erros": [...]}
    buscas = models.JSONField(default=dict, blank=True)
    onde = models.CharField(max_length=8, choices=Onde.choices, blank=True)
    pauta = models.ForeignKey(
        "content.Topic", on_delete=models.SET_NULL, null=True, blank=True, related_name="ideias"
    )
    erro = models.TextField(blank=True)
    # Investigar de novo e REFINAMENTO: {"pedido": "...", "buscar_existentes": bool,
    # "buscar": [nomes das frentes a buscar]}. As frentes ja definidas ficam.
    refino = models.JSONField(default=dict, blank=True)
    aprovada_em = models.DateTimeField(null=True, blank=True)
    criada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    criada_em = models.DateTimeField(default=timezone.now)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criada_em"]

    def __str__(self) -> str:
        return self.titulo

    @property
    def relato(self) -> str:
        return "\n".join(x.strip() for x in [self.texto, self.transcricao] if x and x.strip())

    @property
    def titulo(self) -> str:
        if self.leitura.get("titulo"):
            return self.leitura["titulo"]
        primeira = next((x.strip() for x in self.relato.splitlines() if x.strip()), "")
        return primeira[:120] or str(_("Ideia por audio"))
