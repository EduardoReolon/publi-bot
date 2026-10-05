from __future__ import annotations

import re

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.social.models import Abordagem, ConfiguracaoSocial, Destino
from apps.social.redes import REDES, escolhas

DIAS = [
    (0, _("seg")),
    (1, _("ter")),
    (2, _("qua")),
    (3, _("qui")),
    (4, _("sex")),
    (5, _("sab")),
    (6, _("dom")),
]
_HORARIO = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


class DestinoForm(forms.ModelForm):
    rede = forms.ChoiceField(label=_("rede"), choices=escolhas())
    dias = forms.TypedMultipleChoiceField(
        label=_("dias de postar"),
        choices=DIAS,
        coerce=int,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Vazio: segunda a sexta."),
    )
    horarios = forms.CharField(
        label=_("horarios"),
        required=False,
        help_text=_("Separados por virgula, ex.: 08:30, 18:00. Vazio: o horario comum da rede."),
    )
    cor_fundo = forms.CharField(label=_("cor de fundo das laminas"), required=False)
    cor_texto = forms.CharField(label=_("cor do texto"), required=False)
    cor_destaque = forms.CharField(label=_("cor de destaque"), required=False)

    class Meta:
        model = Destino
        fields = [
            "rede",
            "nome",
            "autor",
            "ligado",
            "aprovacao",
            "teto_semanal",
            "fotos_por_cento",
            "publico",
            "tom",
            "instrucoes",
            "hashtags_fixas",
            "hashtags_de_referencia",
            "chamada_final",
        ]
        widgets = {
            "publico": forms.Textarea(attrs={"rows": 3}),
            "instrucoes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instancia = self.instance
        autores = [("", "—")]
        for r in REDES.values():
            autores += [(c, f"{r.nome}: {rotulo}") for c, rotulo in r.autores]
        self.fields["fotos_por_cento"].required = False  # vazio: so artigos
        self.fields["autor"] = forms.ChoiceField(
            label=_("quem posta"), choices=autores, required=False
        )
        if instancia is not None and not instancia._state.adding:
            self.fields["rede"].disabled = True
            self.initial["horarios"] = ", ".join(instancia.horarios or [])
            self.initial["dias"] = instancia.dias or []
            for chave in ("fundo", "texto", "destaque"):
                self.initial[f"cor_{chave}"] = (instancia.cores or {}).get(chave, "")

    def clean_horarios(self):
        horarios = [
            h.strip() for h in (self.cleaned_data.get("horarios") or "").split(",") if h.strip()
        ]
        ruins = [h for h in horarios if not _HORARIO.match(h)]
        if ruins:
            raise forms.ValidationError(
                _("Horario invalido: %(h)s (use 08:30).") % {"h": ", ".join(ruins)}
            )
        return [f"{int(h.split(':')[0]):02d}:{h.split(':')[1]}" for h in horarios]

    def clean_fotos_por_cento(self):
        return min(self.cleaned_data.get("fotos_por_cento") or 0, 100)

    def save(self, commit=True):
        destino = super().save(commit=False)
        destino.horarios = self.cleaned_data["horarios"]
        destino.dias = sorted(self.cleaned_data.get("dias") or [])
        destino.cores = {
            chave: self.cleaned_data.get(f"cor_{chave}", "").strip()
            for chave in ("fundo", "texto", "destaque")
            if self.cleaned_data.get(f"cor_{chave}", "").strip()
        }
        if commit:
            destino.save()
        return destino


class AbordagemForm(forms.ModelForm):
    redes = forms.MultipleChoiceField(
        label=_("redes"),
        choices=escolhas(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Nenhuma marcada: vale para todas."),
    )

    class Meta:
        model = Abordagem
        fields = ["nome", "instrucao", "redes", "ativa"]
        widgets = {"instrucao": forms.Textarea(attrs={"rows": 4})}


class ConfiguracaoForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoSocial
        fields = [
            "ligado",
            "metrica",
            "variantes",
            "reciclar_apos_meses",
            "espacamento_dias",
            "instrucoes",
            "regras",
            "descrever_fotos",
        ]
        widgets = {
            "instrucoes": forms.Textarea(attrs={"rows": 3}),
            "regras": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_variantes(self):
        return min(max(self.cleaned_data["variantes"], 1), 2)
