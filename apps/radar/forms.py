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
            "estrategia_de_temas",
            "sementes",
            "dores",
            "usar_serp",
            "usar_volume",
            "usar_perguntas_do_site",
            "usar_youtube",
            "usar_search_console",
            "fontes_pelo_radar",
            "buscar_fontes",
            "fontes_por_pauta",
            "artigos_cientificos",
            "sementes_cientificas",
            "artigos_por_rodada",
            "procurar_links_quebrados",
            "idade_para_vigiar",
            "concorrentes",
            "usar_concorrentes_conteudo",
            "usar_concorrentes_buscas",
            "usar_avaliacoes",
            "modo_dataforseo",
            "buscador",
            "taxa_de_comparacao",
            "regioes",
            "codigo_de_local",
            "codigo_de_idioma",
            "teto_mensal_usd",
            "propriedade_search_console",
        ]
        widgets = {
            "regioes": forms.HiddenInput(),
            "sementes": forms.Textarea(attrs={"rows": 6}),
            "dores": forms.Textarea(attrs={"rows": 6}),
            "sementes_cientificas": forms.Textarea(attrs={"rows": 5}),
            "concorrentes": forms.Textarea(attrs={"rows": 4}),
        }

    # Os campos em blocos, na ordem em que a pessoa pensa: o que buscar, onde,
    # de que fontes, contra quem, com que provedor, e quanto gastar.
    GRUPOS = [
        (_("Ritmo"), ["intensidade", "estrategia_de_temas"]),
        # Sementes e dores juntas: as duas viram busca no Google, cada rodada
        # pega algumas de cada (as nunca buscadas primeiro, na ordem da lista).
        (_("O que o radar busca"), ["sementes", "dores"]),
        (_("Onde"), ["regioes", "codigo_de_local", "codigo_de_idioma"]),
        (
            _("Fontes de demanda"),
            [
                "usar_serp",
                "usar_volume",
                "usar_perguntas_do_site",
                "usar_youtube",
                "usar_search_console",
                "propriedade_search_console",
            ],
        ),
        (
            _("Fontes para os artigos"),
            [
                "fontes_pelo_radar",
                "buscar_fontes",
                "fontes_por_pauta",
                "procurar_links_quebrados",
            ],
        ),
        (
            _("Artigos cientificos"),
            ["artigos_cientificos", "sementes_cientificas", "artigos_por_rodada"],
        ),
        (_("Artigos publicados"), ["idade_para_vigiar"]),
        (
            _("Concorrentes"),
            [
                "concorrentes",
                "usar_concorrentes_conteudo",
                "usar_concorrentes_buscas",
                "usar_avaliacoes",
            ],
        ),
        (
            _("Buscador e custo"),
            ["buscador", "modo_dataforseo", "taxa_de_comparacao", "teto_mensal_usd"],
        ),
    ]

    def grupos(self):
        """[(titulo, [campos])] para o template."""
        return [(titulo, [self[nome] for nome in nomes]) for titulo, nomes in self.GRUPOS]

    MAXIMO_DE_REGIOES = 10

    def clean_regioes(self):
        """Lista de {codigo, nome, tipo}, sem repeticao e sem sobreposicao.

        Sobreposicao e erro, e nao aviso: o volume das regioes e SOMADO, e
        Curitiba dentro de Parana contaria a mesma busca duas vezes.
        """
        from apps.radar.locais import sobrepostas

        bruto = self.cleaned_data.get("regioes") or []
        if not isinstance(bruto, list):
            raise forms.ValidationError(_("Formato de regioes invalido."))
        regioes, vistos = [], set()
        for item in bruto:
            codigo = str((item or {}).get("codigo", "")).strip()
            if not codigo.isdigit() or codigo in vistos:
                continue
            vistos.add(codigo)
            regioes.append(
                {
                    "codigo": int(codigo),
                    "nome": str(item.get("nome") or codigo)[:200],
                    "tipo": str(item.get("tipo") or "")[:40],
                }
            )
        if len(regioes) > self.MAXIMO_DE_REGIOES:
            raise forms.ValidationError(
                _("No maximo %(n)s regioes: cada uma multiplica o custo das buscas.")
                % {"n": self.MAXIMO_DE_REGIOES}
            )
        pares = sobrepostas(regioes)
        if pares:
            dentro, fora = pares[0]
            raise forms.ValidationError(
                _(
                    "%(dentro)s fica dentro de %(fora)s: o volume seria contado duas "
                    "vezes. Escolha uma das duas."
                )
                % {"dentro": dentro, "fora": fora}
            )
        return regioes

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "artigos_por_rodada" in self.fields:
            self.fields["artigos_por_rodada"].required = False

    def clean_artigos_por_rodada(self):
        # Vazio (ou formulario de antes do campo existir): o padrao.
        valor = self.cleaned_data.get("artigos_por_rodada")
        return 5 if valor in (None, "") else valor

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
    openalex_chave = forms.CharField(
        label=_("Chave da API do OpenAlex"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Gratuita, em openalex.org (Settings > API). Deixe vazio para manter."),
    )
    remover_dataforseo = forms.BooleanField(label=_("Remover a conta DataForSEO"), required=False)
    remover_youtube = forms.BooleanField(label=_("Remover a chave do YouTube"), required=False)

    class Meta:
        model = ContasExternas
        fields = ["dataforseo_login", "searxng_url", "email_para_bases_academicas"]
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
        if dados.get("openalex_chave"):
            contas.openalex_chave_ciphertext = cifrar(dados["openalex_chave"])
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
