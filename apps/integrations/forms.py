"""Formularios do site de destino e da cadencia."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.integrations.models import PublicationSchedule, Site
from apps.integrations.validators import validar_url_de_site


class SiteForm(forms.ModelForm):
    """Cadastro do site e das credenciais.

    As credenciais nunca voltam para a tela. O campo vem sempre vazio e so
    grava quando alguem digita algo — exibir a chave para reedita-la a
    transformaria em texto visivel em qualquer ombro por perto, e os ultimos
    quatro caracteres ja bastam para saber QUAL chave esta cadastrada.
    """

    api_key = forms.CharField(
        label=_("Chave de API"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Deixe vazio para manter a atual."),
    )
    signing_secret = forms.CharField(
        label=_("Segredo de assinatura"),
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=_("Usado no HMAC de cada requisicao. Deixe vazio para manter o atual."),
    )

    class Meta:
        model = Site
        fields = [
            "name",
            "slug",
            "base_url",
            "platform",
            "content_language",
            "site_timezone",
            "niche",
            "default_author",
            "default_author_credentials",
            "is_sensitive",
            "publishing_paused",
            "max_articles_per_month",
        ]
        help_texts = {
            "is_sensitive": _(
                "Saude, financas e direito: exige revisor com credencial tecnica "
                "registrada para aprovar."
            ),
            "max_articles_per_month": _(
                "Teto de volume. Publicacao alta e previsivel e o padrao que "
                "buscadores tratam como producao em escala."
            ),
            "site_timezone": _("A cadencia e calculada neste fuso e convertida para UTC."),
        }

    def clean_base_url(self):
        url = self.cleaned_data["base_url"]
        # Resolve o DNS e recusa endereco interno: sem isso, cadastrar
        # `http://169.254.169.254/` faria o sistema buscar credenciais de nuvem
        # a cada publicacao.
        validar_url_de_site(url)
        return url

    def save(self, commit=True):
        from apps.inference.security import guardar_chave
        from apps.integrations.signing import impressao_da_chave

        site = super().save(commit=False)

        chave = self.cleaned_data.get("api_key")
        if chave:
            guardar_chave(site, chave)
            site.api_key_fingerprint = impressao_da_chave(chave)

        segredo = self.cleaned_data.get("signing_secret")
        if segredo:
            guardar_chave(site, segredo, campo="signing_secret_ciphertext")

        if commit:
            site.save()
        return site


class CadenciaForm(forms.ModelForm):
    """Quando o site recebe conteudo.

    `weekdays` e `times_of_day` sao JSON no banco. O formulario aceita texto
    simples — "0,2,4" e "09:00, 15:00" — porque digitar JSON a mao e um convite
    a erro de sintaxe numa tela de configuracao.
    """

    dias = forms.CharField(
        label=_("Dias da semana"),
        required=False,
        help_text=_("0 = segunda, 6 = domingo. Separados por virgula: 0,2,4"),
    )
    horarios = forms.CharField(
        label=_("Horarios"),
        required=False,
        help_text=_("No fuso do site, separados por virgula: 09:00, 15:00"),
    )

    class Meta:
        model = PublicationSchedule
        fields = [
            "mode",
            "interval_days",
            "max_per_day",
            "buffer_threshold",
            "qa_consumes_slot",
            "is_active",
        ]
        help_texts = {
            "buffer_threshold": _(
                "Abaixo disto o sistema avisa que a reserva de conteudo aprovado esta acabando."
            ),
            "qa_consumes_slot": _(
                "Quando marcado, uma resposta publicada ocupa um horario da "
                "cadencia — senao o site publica mais do que o configurado."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["dias"].initial = ",".join(str(d) for d in self.instance.weekdays or [])
            self.fields["horarios"].initial = ", ".join(self.instance.times_of_day or [])

    def clean_dias(self):
        bruto = (self.cleaned_data.get("dias") or "").strip()
        if not bruto:
            return []
        try:
            dias = [int(p.strip()) for p in bruto.split(",") if p.strip()]
        except ValueError as exc:
            raise forms.ValidationError(_("Use apenas numeros de 0 a 6.")) from exc
        if any(d < 0 or d > 6 for d in dias):
            raise forms.ValidationError(_("Os dias vao de 0 (segunda) a 6 (domingo)."))
        return sorted(set(dias))

    def clean_horarios(self):
        bruto = (self.cleaned_data.get("horarios") or "").strip()
        if not bruto:
            return []
        horarios = []
        for parte in bruto.split(","):
            parte = parte.strip()
            if not parte:
                continue
            try:
                hora, minuto = parte.split(":")
                if not (0 <= int(hora) <= 23 and 0 <= int(minuto) <= 59):
                    raise ValueError
            except ValueError as exc:
                raise forms.ValidationError(
                    _("Horario invalido: %(valor)s. Use HH:MM.") % {"valor": parte}
                ) from exc
            horarios.append(f"{int(hora):02d}:{int(minuto):02d}")
        return sorted(set(horarios))

    def clean(self):
        dados = super().clean()
        if dados.get("mode") == PublicationSchedule.Mode.WEEKLY_SLOTS:
            if not dados.get("dias") or not dados.get("horarios"):
                raise forms.ValidationError(
                    _(
                        "No modo de dias e horarios fixos, informe ao menos um dia "
                        "e um horario — senao nenhum horario e gerado e nada e publicado."
                    )
                )
        return dados

    def save(self, commit=True):
        agenda = super().save(commit=False)
        agenda.weekdays = self.cleaned_data.get("dias") or []
        agenda.times_of_day = self.cleaned_data.get("horarios") or []
        if commit:
            agenda.save()
        return agenda
