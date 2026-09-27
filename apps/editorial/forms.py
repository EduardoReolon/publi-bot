"""Formulario do perfil editorial.

As listas (pares "somos / nao somos", termos proibidos e estruturas) sao
editadas como texto, uma linha por item e `|` separando as partes. E menos
bonito que uma tabela dinamica, e muito mais rapido de preencher, colar de
outro lugar e revisar de relance.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.editorial.models import EditorialProfile, PerfilDoNegocio
from apps.editorial.presets import TIPOS_DE_CONTEUDO


def _linhas(texto: str) -> list[list[str]]:
    return [
        [parte.strip() for parte in linha.split("|")]
        for linha in (texto or "").splitlines()
        if linha.strip()
    ]


def somos_para_texto(pares) -> str:
    return "\n".join(
        f"{p.get('somos', '')} | {p.get('nao_somos', '')}".rstrip(" |")
        for p in pares or []
        if isinstance(p, dict)
    )


def termos_para_texto(termos) -> str:
    linhas = []
    for t in termos or []:
        if not isinstance(t, dict):
            continue
        partes = [t.get("termo", ""), t.get("troca", ""), t.get("motivo", "")]
        while partes and not partes[-1]:
            partes.pop()
        linhas.append(" | ".join(partes))
    return "\n".join(linhas)


class PerfilEditorialForm(forms.ModelForm):
    somos_texto = forms.CharField(
        label=_("Somos / nao somos"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Uma linha por par: 'proximos | intimos'."),
    )
    termos_texto = forms.CharField(
        label=_("Termos proibidos"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_(
            "Uma linha por termo: 'termo | troca sugerida | motivo'. A troca e o "
            "motivo sao opcionais. Aparecer um destes bloqueia a aprovacao, a "
            "menos que o revisor confirme."
        ),
    )

    class Meta:
        model = EditorialProfile
        fields = [
            "tom_humor",
            "tom_formalidade",
            "tom_respeito",
            "tom_entusiasmo",
            "pessoa",
            "regra_de_ouro",
            "tipo_padrao",
            "convite",
            "exemplos",
        ]
        widgets = {
            campo: forms.NumberInput(attrs={"min": 1, "max": 5, "step": 1})
            for campo in ("tom_humor", "tom_formalidade", "tom_respeito", "tom_entusiasmo")
        } | {
            "convite": forms.Textarea(attrs={"rows": 3}),
            "exemplos": forms.Textarea(attrs={"rows": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        perfil = self.instance
        self.fields["somos_texto"].initial = somos_para_texto(perfil.somos)
        self.fields["termos_texto"].initial = termos_para_texto(perfil.termos)
        for tipo, rotulo in TIPOS_DE_CONTEUDO:
            self.fields[f"estrutura_{tipo}"] = forms.CharField(
                label=rotulo,
                required=False,
                widget=forms.Textarea(attrs={"rows": 5}),
                initial="\n".join(perfil.estrutura_de(tipo)),
            )

    @property
    def campos_de_estrutura(self):
        return [self[f"estrutura_{tipo}"] for tipo, _rotulo in TIPOS_DE_CONTEUDO]

    def save(self, commit: bool = True):
        perfil = super().save(commit=False)
        dados = self.cleaned_data
        perfil.somos = [
            {"somos": partes[0], "nao_somos": partes[1] if len(partes) > 1 else ""}
            for partes in _linhas(dados.get("somos_texto", ""))
            if partes[0]
        ]
        perfil.termos = [
            {
                "termo": partes[0],
                "troca": partes[1] if len(partes) > 1 else "",
                "motivo": partes[2] if len(partes) > 2 else "",
            }
            for partes in _linhas(dados.get("termos_texto", ""))
            if partes[0]
        ]
        perfil.estruturas = {
            tipo: [linha.strip() for linha in texto.splitlines() if linha.strip()]
            for tipo, _rotulo in TIPOS_DE_CONTEUDO
            if (texto := dados.get(f"estrutura_{tipo}", ""))
        }
        if commit:
            perfil.save()
        return perfil


class PerfilDoNegocioForm(forms.ModelForm):
    """O perfil do negocio, e as dores do publico (guardadas no radar)."""

    dores = forms.CharField(
        label=_("Dores do publico"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text=_(
            "Uma por linha: o problema nas palavras de quem sente, nao o seu servico. "
            "Quem busca 'consultoria de custos' ja sabe o que quer; quem busca 'obra "
            "estourou o orcamento' ainda nao."
        ),
    )

    class Meta:
        model = PerfilDoNegocio
        fields = [
            "tema",
            "publico",
            "oferta",
            "frentes",
            "valor_da_conversao",
            "investimento_publibot",
            "investimento_anuncios",
            "cotacao_do_dolar",
        ]
        widgets = {
            "oferta": forms.Textarea(attrs={"rows": 3}),
            "frentes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._config = config
        if config is not None:
            self.fields["dores"].initial = config.dores

    def save(self, commit: bool = True):
        perfil = super().save(commit=commit)
        if commit and self._config is not None:
            self._config.dores = self.cleaned_data.get("dores", "")
            self._config.save(update_fields=["dores"])
        return perfil
