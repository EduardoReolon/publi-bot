"""O caminho comum de "Conectar a conta": autorizar na rede, trocar o codigo
pelo acesso, escolher a conta (pagina, perfil, local) e renovar.

Cada rede implementa so o que muda (enderecos, escopos, formato da resposta).
As chaves do APLICATIVO (o app criado no LinkedIn Developers, na Meta, no
Google Cloud) ficam nas variaveis de ambiente; o acesso de cada cliente fica
cifrado no destino.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.social.redes.base import ErroDaRede, NaoConectado


class OAuth:
    # Nomes das variaveis de ambiente (settings) com o id e o segredo do app.
    ID_DO_APP = ""
    SEGREDO_DO_APP = ""
    # Com quanta antecedencia renovar o acesso (onde a rede deixa renovar).
    RENOVAR_ANTES = timedelta(minutes=5)

    # Quando `contas()` nao acha nenhuma: o que a rede mostrou e o que falta.
    sem_contas = ""

    def __init__(self, *, http=None):
        import httpx

        self.http = http or httpx.Client(timeout=30.0)

    def app(self) -> tuple[str, str]:
        cliente = getattr(settings, self.ID_DO_APP, "") or ""
        segredo = getattr(settings, self.SEGREDO_DO_APP, "") or ""
        if not cliente or not segredo:
            raise NaoConectado(
                f"o app desta rede nao esta configurado: defina {self.ID_DO_APP} e "
                f"{self.SEGREDO_DO_APP} no .env do servidor (ver docs/REDES_SOCIAIS.md)."
            )
        return cliente, segredo

    def url_de_autorizacao(self, destino, redirect_uri: str, state: str) -> str:
        raise NotImplementedError

    def trocar_codigo(self, destino, codigo: str, redirect_uri: str) -> dict:
        """Devolve as credenciais (access_token...) com "expira_em" (ISO)."""
        raise NotImplementedError

    def contas(self, destino) -> list[tuple[str, str]]:
        """[(id, nome)] das contas que o acesso alcanca (paginas, locais...)."""
        raise NotImplementedError

    def renovar(self, destino) -> bool:
        """Renova o acesso perto de vencer, quando a rede permite. Devolve se renovou."""
        return False

    # -- Comum ---------------------------------------------------------------
    @staticmethod
    def expira(segundos) -> str:
        if not segundos:
            return ""
        return (timezone.now() + timedelta(seconds=int(segundos))).isoformat()

    @staticmethod
    def gravar(destino, credenciais: dict) -> None:
        from django.utils.dateparse import parse_datetime

        destino.gravar_credenciais(credenciais)
        destino.expira_em = parse_datetime(credenciais.get("expira_em") or "") or None
        destino.conectado_em = timezone.now()
        destino.ultimo_erro = ""
        destino.save(update_fields=["credenciais", "expira_em", "conectado_em", "ultimo_erro"])

    def _json(self, resposta, contexto: str) -> dict:
        if resposta.status_code >= 400:
            try:
                corpo = resposta.json()
            except ValueError:
                corpo = resposta.text[:300]
            raise ErroDaRede(f"{contexto}: HTTP {resposta.status_code} — {corpo}")
        return resposta.json()


def acesso_valido(destino, oauth: OAuth | None) -> dict:
    """As credenciais, renovadas se perto de vencer; vencidas sem renovacao
    levantam NaoConectado (a tela pede para reconectar)."""
    if not destino.conectado:
        raise NaoConectado(f"{destino.nome}: conta nao conectada.")
    antes = oauth.RENOVAR_ANTES if oauth is not None else timedelta(0)
    perto = destino.expira_em and destino.expira_em <= timezone.now() + antes
    if perto and oauth is not None and oauth.renovar(destino):
        destino.refresh_from_db()
    elif destino.expira_em and destino.expira_em <= timezone.now():
        raise NaoConectado(f"{destino.nome}: o acesso venceu. Conecte a conta de novo.")
    return destino.ler_credenciais()
