"""Formularios de pauta, revisao e aprovacao."""

from __future__ import annotations

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.content.models import Article, Topic


class PautaForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ["title", "target_keyword", "briefing"]
        labels = {
            "title": _("Titulo da pauta"),
            "target_keyword": _("Palavra-chave principal"),
            "briefing": _("Orientacao"),
        }
        help_texts = {
            "briefing": _(
                "Entra na busca por fontes junto com o titulo. Quanto mais "
                "proximo do vocabulario do acervo, melhor a recuperacao."
            ),
        }
        widgets = {"briefing": forms.Textarea(attrs={"style": "min-height:8rem"})}


class RevisaoDeArtigo(forms.Form):
    """A edicao humana do texto.

    O Markdown e a fonte da verdade editavel: modelos produzem Markdown de
    forma muito mais confiavel que HTML, e o HTML vai direto para o site de um
    terceiro.
    """

    title = forms.CharField(label=_("Titulo"), max_length=300)
    meta_description = forms.CharField(
        label=_("Meta description"),
        max_length=160,
        required=False,
        help_text=_("Ate 160 caracteres. E o que aparece no resultado de busca."),
    )
    body_markdown = forms.CharField(widget=forms.Textarea, label=_("Corpo (Markdown)"))
    author_name = forms.CharField(
        label=_("Autor"),
        max_length=150,
        help_text=_("Conteudo sem autor identificado nao pode ser publicado."),
    )
    author_credentials = forms.CharField(
        label=_("Credenciais do autor"), max_length=200, required=False
    )


class AgendamentoForm(forms.Form):
    """Quando publicar.

    Vazio significa "no proximo horario livre da cadencia" — o caminho normal.
    A data explicita existe para o caso pontual em que o texto tem de sair num
    momento certo.
    """

    quando = forms.DateTimeField(
        label=_("Publicar em"),
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        help_text=_("Deixe vazio para usar o proximo horario livre da cadencia."),
    )
    confirmar_divergencia = forms.BooleanField(
        label=_("Confirmo que o texto apresenta a divergencia entre as fontes"),
        required=False,
    )

    def clean_quando(self):
        quando = self.cleaned_data.get("quando")
        if quando and quando < timezone.now():
            raise forms.ValidationError(
                _("A data ja passou. O agendador publicaria imediatamente.")
            )
        return quando


class RevisaoDeResposta(forms.Form):
    body_markdown = forms.CharField(widget=forms.Textarea, label=_("Resposta (Markdown)"))
    author_name = forms.CharField(label=_("Autor"), max_length=150)
    author_credentials = forms.CharField(label=_("Credenciais"), max_length=200, required=False)


SITUACOES_DE_ARTIGO = Article.Status.choices
