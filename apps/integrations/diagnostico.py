"""Teste de conexao com o site: da aceitacao da assinatura a recusa do que e falso.

Conferir so que o site responde nao basta. Um site que aceita QUALQUER
assinatura tambem responde — e nele qualquer um publica. Por isso o teste
tambem manda o que o site precisa recusar: segredo errado, nonce repetido e
instante vencido (regras 3 e 4 do contrato, docs/contrato/README.md).

Usado pela tela Site e cadencia e pelo `conferir_instalacao`: o mesmo teste,
num lugar so.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from django.utils.translation import gettext as _

from apps.integrations.errors import SiteAuthError, SiteError


@dataclass
class Etapa:
    nome: str
    ok: bool
    detalhe: str = ""


def _recusa(cliente, auth, *, repetir: bool = False) -> tuple[bool, str]:
    """(o site recusou?, detalhe) para uma requisicao que ele NAO deve aceitar."""
    try:
        if repetir:
            cliente._requisitar("GET", "/health/", auth=auth)
        cliente._requisitar("GET", "/health/", auth=auth)
    except SiteAuthError as exc:
        return True, _("recusou (HTTP %(status)s)") % {"status": exc.status or "?"}
    except SiteError as exc:
        return False, _("respondeu com outro erro: %(erro)s") % {"erro": exc}
    return False, _("ACEITOU")


def testar_site(site) -> list[Etapa]:
    """As etapas, em ordem. Para na primeira que impede as seguintes."""
    from apps.inference.security import decifrar
    from apps.integrations.client import SiteClient
    from apps.integrations.signing import JANELA_DE_TEMPO_SEGUNDOS, AssinaturaHttpx

    cliente = SiteClient(site)
    etapas: list[Etapa] = []

    try:
        saude = cliente.health()
    except SiteAuthError as exc:
        etapas.append(
            Etapa(
                _("Chave e segredo aceitos"),
                False,
                _(
                    "o site recusou a assinatura (%(erro)s). Confira se a chave e o segredo "
                    "sao os mesmos dos dois lados, e se o relogio do site esta certo."
                )
                % {"erro": exc},
            )
        )
        return etapas
    except SiteError as exc:
        etapas.append(
            Etapa(
                _("O site responde"),
                False,
                _("%(erro)s. Confira o endereco e se o site esta no ar com HTTPS.") % {"erro": exc},
            )
        )
        return etapas

    # O teste e tambem o momento de atualizar o cadastro: os recursos sao os
    # que o site declara agora, e nao os digitados a mao.
    site.atualizar_recursos(saude)
    versoes = saude.get("contract_versions") or saude.get("contract_version") or "?"
    if isinstance(versoes, list | tuple):
        versoes = ", ".join(map(str, versoes))
    recursos = saude.get("capabilities") or saude.get("features") or []
    etapas.append(
        Etapa(
            _("Chave e segredo aceitos"),
            True,
            _("contrato %(versoes)s; recursos: %(recursos)s")
            % {"versoes": versoes, "recursos": ", ".join(map(str, recursos)) or "-"},
        )
    )

    chave = decifrar(site.api_key_ciphertext)
    segredo = decifrar(site.signing_secret_ciphertext) or chave

    recusou, detalhe = _recusa(
        cliente, AssinaturaHttpx(api_key=chave, signing_secret=f"errado-{uuid.uuid4()}")
    )
    etapas.append(Etapa(_("Recusa segredo errado"), recusou, detalhe))

    recusou, detalhe = _recusa(
        cliente,
        AssinaturaHttpx(api_key=chave, signing_secret=segredo, nonce=str(uuid.uuid4())),
        repetir=True,
    )
    etapas.append(Etapa(_("Recusa nonce repetido"), recusou, detalhe))

    vencido = str(int(time.time()) - 2 * JANELA_DE_TEMPO_SEGUNDOS)
    recusou, detalhe = _recusa(
        cliente, AssinaturaHttpx(api_key=chave, signing_secret=segredo, timestamp=vencido)
    )
    etapas.append(Etapa(_("Recusa horario vencido"), recusou, detalhe))

    try:
        contexto = cliente.seo_context(limite=1)
        etapas.append(
            Etapa(
                _("Leitura das publicacoes"),
                True,
                _("%(total)s publicacao(oes) na primeira pagina")
                % {"total": len(contexto.get("published_posts") or [])},
            )
        )
    except SiteError as exc:
        etapas.append(Etapa(_("Leitura das publicacoes"), False, str(exc)))

    return etapas
