"""Formularios do acervo."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.knowledge.models import Document, DocumentCategory

# O que a extracao sem GPU consegue ler. Recusar aqui e melhor que aceitar e
# falhar minutos depois, dentro do worker, com o arquivo ja gravado.
EXTENSOES_ACEITAS = (".pdf", ".txt", ".md", ".markdown")


class EnvioDeDocumento(forms.Form):
    arquivo = forms.FileField(
        label=_("Arquivo"),
        help_text=_("PDF, ou .txt/.md ja convertidos."),
    )
    category = forms.ModelChoiceField(
        queryset=DocumentCategory.objects.all(),
        label=_("Categoria"),
        empty_label=None,
    )
    title = forms.CharField(
        label=_("Titulo"),
        required=False,
        help_text=_("Opcional: o sistema tenta ler do proprio documento."),
    )
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
            "language",
            "license",
            "authority_score",
        ]
        labels = {
            "authority_score": _("Autoridade (0-100)"),
        }
        help_texts = {
            "authority_score": _(
                "Entre as fontes recuperadas, a de maior autoridade e a que "
                "recebe o link de saida do artigo."
            ),
            "license": _(
                "Documentos proprietarios ou de licenca desconhecida perdem o "
                "texto integral ao serem curados; o trecho selecionado fica."
            ),
        }

    def clean(self):
        dados = super().clean()
        # A URL nao e obrigatoria no model, mas sem ela o documento nao serve
        # para o que o produto faz: citar com link.
        if not dados.get("source_url"):
            self.add_error(
                "source_url",
                _(
                    "Informe a URL de origem. E ela que vira o link publicado; "
                    "sem ela o documento nunca sera escolhido como fonte primaria."
                ),
            )
        if not dados.get("authors"):
            self.add_error("authors", _("Os autores formam o texto-ancora do link."))
        return dados
