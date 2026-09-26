"""Formularios do radar."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.radar.models import ConfiguracaoDoRadar, ContasExternas


class ConfiguracaoForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoDoRadar
        fields = [
            "intensidade",
            "sementes",
            "usar_serp",
            "usar_volume",
            "usar_perguntas_do_site",
            "usar_youtube",
            "usar_search_console",
            "buscar_fontes",
            "fontes_por_pauta",
            "concorrentes",
            "usar_concorrentes_conteudo",
            "usar_concorrentes_buscas",
            "usar_avaliacoes",
            "modo_dataforseo",
            "buscador",
            "taxa_de_comparacao",
            "codigo_de_local",
            "codigo_de_idioma",
            "teto_mensal_usd",
            "propriedade_search_console",
        ]
        widgets = {
            "sementes": forms.Textarea(attrs={"rows": 5}),
            "concorrentes": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_teto_mensal_usd(self):
        from decimal import Decimal

        from django.conf import settings

        valor = self.cleaned_data["teto_mensal_usd"]
        maximo = Decimal(str(getattr(settings, "RADAR_TETO_MAXIMO_USD", 20)))
        if valor > maximo:
            raise forms.ValidationError(
                _("O maximo desta instalacao e US$ %(maximo)s (RADAR_TETO_MAXIMO_USD).")
                % {"maximo": maximo}
            )
        return valor


class ContasForm(forms.ModelForm):
    """Credenciais. Nunca voltam para a tela: o campo vem vazio e so grava o
    que for digitado."""

    dataforseo_senha = forms.CharField(
        label=_("Senha da API DataForSEO"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Deixe vazio para manter a atual."),
    )
    youtube_chave = forms.CharField(
        label=_("Chave da API do YouTube"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Google Cloud > APIs > YouTube Data API v3. Deixe vazio para manter."),
    )
    remover_dataforseo = forms.BooleanField(label=_("Remover a conta DataForSEO"), required=False)
    remover_youtube = forms.BooleanField(label=_("Remover a chave do YouTube"), required=False)

    class Meta:
        model = ContasExternas
        fields = ["dataforseo_login", "searxng_url"]
        help_texts = {
            "dataforseo_login": _("O e-mail de login da API (painel DataForSEO > API Access)."),
            "searxng_url": _("Endereco de uma instancia sua, com a saida JSON ligada."),
        }

    def save(self, commit: bool = True):
        from apps.inference.security import cifrar

        contas = super().save(commit=False)
        dados = self.cleaned_data
        if dados.get("remover_dataforseo"):
            contas.dataforseo_login = ""
            contas.dataforseo_senha_ciphertext = None
        elif dados.get("dataforseo_senha"):
            contas.dataforseo_senha_ciphertext = cifrar(dados["dataforseo_senha"])
        if dados.get("remover_youtube"):
            contas.youtube_chave_ciphertext = None
        elif dados.get("youtube_chave"):
            contas.youtube_chave_ciphertext = cifrar(dados["youtube_chave"])
        if commit:
            contas.save()
        return contas


class BuscaManualForm(forms.Form):
    consulta = forms.CharField(
        label=_("O que buscar"),
        max_length=300,
        widget=forms.TextInput(attrs={"placeholder": _("ex.: higienizacao de tapete")}),
    )
    com_volume = forms.BooleanField(
        label=_("Trazer volume de busca (uma chamada paga a mais)"), required=False
    )
