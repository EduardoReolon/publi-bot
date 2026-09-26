"""Formularios do acervo."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.knowledge.models import Document, DocumentCategory, RetrievalSettings

# O que a extracao sem GPU consegue ler. Recusar aqui e melhor que aceitar e
# falhar minutos depois, dentro do worker, com o arquivo ja gravado.
EXTENSOES_ACEITAS = (
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    # Audio: transcrito no worker da placa.
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
    ".opus",
    ".webm",
    ".mp4",
    ".flac",
)


class EnvioDeDocumento(forms.Form):
    arquivo = forms.FileField(
        label=_("Arquivo"),
        help_text=_(
            "PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), pagina salva "
            "(.html), texto (.txt/.md) ou audio (.mp3, .m4a...), que e transcrito "
            "no worker."
        ),
    )
    category = forms.ModelChoiceField(
        queryset=DocumentCategory.objects.all(),
        label=_("Categoria"),
        empty_label=None,
    )
    # Nao ha campo de titulo aqui de proposito. O titulo sai do proprio arquivo
    # — do `/Title` do PDF ou das primeiras linhas do texto — e a curadoria
    # confirma ou corrige. Pedi-lo no envio era trabalho duplicado: a pessoa
    # digitava o que o documento ja dizia.
    source_url = forms.URLField(
        label=_("URL de origem"),
        required=False,
        assume_scheme="https",
        help_text=_(
            "E daqui que sai o link publicado no artigo. Sem ela, o documento "
            "sustenta o texto mas nao pode ser citado com link."
        ),
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        nome = (arquivo.name or "").lower()
        if not nome.endswith(EXTENSOES_ACEITAS):
            raise forms.ValidationError(
                _("Formato nao suportado. Aceitos: %(lista)s.")
                % {"lista": ", ".join(EXTENSOES_ACEITAS)}
            )
        return arquivo


class EnvioPorUrl(forms.Form):
    url = forms.URLField(
        label=_("Endereco da pagina"),
        assume_scheme="https",
        help_text=_(
            "A pagina e buscada agora e guardada como copia. O texto principal "
            "vai para a curadoria, como um arquivo enviado."
        ),
    )
    category = forms.ModelChoiceField(
        queryset=DocumentCategory.objects.all(), label=_("Categoria"), empty_label=None
    )


class NotaDoEspecialista(forms.Form):
    """O que a pessoa sabe, escrito por ela, para servir de fonte.

    Pode ser em topicos. O que importa e que seja dela: o texto publicado vai
    atribuir a afirmacao a quem escreveu.
    """

    titulo = forms.CharField(label=_("Assunto"), max_length=300)
    autor = forms.CharField(label=_("Quem escreve"), max_length=150)
    credencial = forms.CharField(
        label=_("Credencial"),
        max_length=150,
        required=False,
        help_text=_("Ex.: 'fisioterapeuta, CREFITO 12345' ou 'engenheiro civil'."),
    )
    texto = forms.CharField(
        label=_("O que voce sabe sobre isso"),
        widget=forms.Textarea(attrs={"rows": 12}),
        help_text=_(
            "Use titulos com '#' para separar assuntos: cada parte vira um bloco "
            "de busca. Nao invente numero que voce nao conferiu."
        ),
    )


class CuradoriaDeDocumento(forms.ModelForm):
    """Os campos que viram a citacao publicada.

    Nenhum deles e cosmetico: autores e ano formam o texto-ancora do link, e a
    URL e o destino dele. Um erro aqui sai no site de um cliente com aparencia
    de fonte conferida.
    """

    class Meta:
        model = Document
        fields = [
            "title",
            "authors",
            "year",
            "doi",
            "source_url",
            "source_label",
            "published_on",
            "language",
            "license",
            "authority_score",
        ]
        labels = {
            "authority_score": _("Autoridade (0-100)"),
        }
        widgets = {"published_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        help_texts = {
            "authority_score": _(
                "Entre as fontes recuperadas, a de maior autoridade e a que "
                "recebe o link de saida do artigo."
            ),
            "source_label": _(
                "Como a fonte aparece no texto quando nao ha autores e ano: "
                "'Manual de uso da planilha, v3', 'SINAPI PR, set/2026'."
            ),
            "published_on": _(
                "A validade da fonte conta daqui. Sem data, conta de quando foi buscada."
            ),
            "license": _(
                "Registro do que a fonte permite. Nao apaga nada por conta "
                "propria: o descarte do texto integral so acontece para as "
                "licencas listadas em LICENCAS_QUE_DESCARTAM_TEXTO_INTEGRAL, "
                "vazia por padrao."
            ),
        }

    def clean(self):
        dados = super().clean()
        categoria = self.instance.category
        modo = categoria.modo_de_citacao_efetivo

        # A URL so e obrigatoria quando a categoria cita com link: e ela que
        # vira o destino publicado. Manual interno e nota do especialista nao
        # tem URL, e exigi-la empurrava as pessoas a inventar uma.
        if modo == DocumentCategory.CitationMode.LINK and not dados.get("source_url"):
            self.add_error(
                "source_url",
                _(
                    "Informe a URL de origem. Esta categoria cita com link; sem "
                    "URL o documento nunca sera a fonte do link de saida."
                ),
            )

        # Artigo cientifico se cita por autores e ano. O resto precisa de ALGUM
        # nome: autores, ou o rotulo.
        if categoria.source_class == DocumentCategory.SourceClass.SCIENTIFIC:
            if not dados.get("authors"):
                self.add_error("authors", _("Os autores formam o texto-ancora do link."))
        elif (
            modo != DocumentCategory.CitationMode.INTERNAL
            and not dados.get("authors")
            and not dados.get("source_label")
        ):
            self.add_error(
                "source_label",
                _("Informe os autores ou um rotulo: e o nome com que a fonte aparece no texto."),
            )
        return dados


class CategoriaDeDocumento(forms.ModelForm):
    """Nome e perfil da categoria."""

    class Meta:
        model = DocumentCategory
        fields = [
            "name",
            "source_class",
            "supports_central_idea",
            "citation_mode",
            "confidential",
            "validity_days",
            "description",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class ConfiguracaoDeBusca(forms.ModelForm):
    """O limiar e quantas fontes sustentam um texto.

    Sao dois numeros com efeito oposto sobre o mesmo risco. Apertar o limiar
    reduz a chance de citar algo que so tangencia o assunto, ao custo de mais
    geracoes falharem por falta de fonte; afrouxa-lo faz o inverso, e o segundo
    caso e o perigoso, porque nao aparece como erro.
    """

    class Meta:
        model = RetrievalSettings
        fields = ["max_cosine_distance", "top_k"]
        widgets = {
            "max_cosine_distance": forms.NumberInput(
                attrs={"step": "0.005", "min": "0", "max": "2"}
            ),
            "top_k": forms.NumberInput(attrs={"step": "1", "min": "1", "max": "20"}),
        }


class TesteDeBusca(forms.Form):
    """Uma consulta de mentira, para ver as distancias antes de fixar o corte."""

    consulta = forms.CharField(
        label=_("Consulta de teste"),
        widget=forms.TextInput(
            attrs={"placeholder": _("ex.: monitoramento de pressao alta na gravidez")}
        ),
        help_text=_(
            "Escreva como uma pauta real chegaria. O resultado nao entra nas "
            "metricas nem gera artigo."
        ),
    )
