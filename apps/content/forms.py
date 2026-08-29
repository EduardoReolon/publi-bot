"""Formularios de pauta, revisao e aprovacao."""

from __future__ import annotations

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.content.models import Article, Author, Topic


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

    # Escolha do cadastro, e nao texto livre. Digitar o autor a cada artigo
    # produz grafias diferentes da mesma pessoa, e nao ha como anexar foto,
    # contato ou redes a um nome digitado.
    #
    # Opcional para SALVAR e obrigatorio para APROVAR: exigir aqui impediria o
    # revisor de guardar uma correcao de texto num ambiente que ainda nao
    # cadastrou ninguem. A trava esta em `aprovar_e_agendar`.
    author = forms.ModelChoiceField(
        label=_("Autor"),
        queryset=Author.objects.none(),
        required=False,
        empty_label=_("— escolha quem assina —"),
        help_text=_("Conteudo sem autor identificado nao pode ser publicado."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["author"].queryset = Author.objects.filter(is_active=True).order_by("name")


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
    """A revisao de uma resposta, que vale tanto para a gerada quanto para a
    escrita a mao.

    Um so formulario para os dois casos: a resposta escrita a mao passa pela
    MESMA revisao e pela mesma aprovacao da gerada. Um caminho mais curto para
    o texto humano seria uma segunda porta para o site do cliente.
    """

    body_markdown = forms.CharField(widget=forms.Textarea, label=_("Resposta (Markdown)"))

    # Mesmo criterio do artigo: escolha do cadastro, opcional para salvar e
    # obrigatoria para aprovar.
    author = forms.ModelChoiceField(
        label=_("Autor"),
        queryset=Author.objects.none(),
        required=False,
        empty_label=_("— escolha quem assina —"),
        help_text=_("Conteudo sem autor identificado nao pode ser publicado."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["author"].queryset = Author.objects.filter(is_active=True).order_by("name")


SITUACOES_DE_ARTIGO = Article.Status.choices


class CadastroDeAutor(forms.ModelForm):
    """Quem assina. So o nome e obrigatorio.

    A foto e convertida para WebP aqui, na entrada. Converter no envio gastaria
    CPU em toda publicacao e deixaria dois formatos no disco.
    """

    remover_foto = forms.BooleanField(label=_("Remover a foto atual"), required=False)

    class Meta:
        model = Author
        fields = ["name", "credentials", "bio", "email", "phone", "photo", "is_active"]
        labels = {"photo": _("Foto de perfil")}
        help_texts = {
            "name": _("Aparece como assinatura no site. E o unico campo obrigatorio."),
            "credentials": _("Ex.: 'nutricionista, CRN-3 12345'. Entra na divulgacao de conteudo."),
            "photo": _("Convertida para WebP automaticamente. JPEG, PNG, WebP ou GIF."),
            "is_active": _("Autor inativo nao aparece na escolha de novos artigos."),
        }

    def clean_photo(self):
        from apps.content.imagens import ImagemInvalida, converter_para_webp

        foto = self.cleaned_data.get("photo")
        # Sem arquivo novo, ou o mesmo que ja estava gravado: nada a converter.
        if not foto or not hasattr(foto, "file") or not hasattr(foto, "content_type"):
            return foto

        try:
            return converter_para_webp(foto, nome="foto")
        except ImagemInvalida as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        dados = super().clean()
        if dados.get("remover_foto"):
            dados["photo"] = None
        return dados


class LinkSocial(forms.Form):
    """Uma linha da lista de redes do autor."""

    label = forms.CharField(label=_("Rede"), max_length=40, required=False)
    url = forms.URLField(label=_("Endereco"), assume_scheme="https", required=False)
