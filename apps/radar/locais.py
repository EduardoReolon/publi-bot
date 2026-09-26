"""As regioes do radar: a lista de locais da DataForSEO e o seletor.

A lista vem da rota de locais do Google Ads na DataForSEO, que e gratuita, e
fica guardada em `LocalDisponivel`. O seletor da tela procura nela.

Regiao sobreposta (Curitiba E Parana) conta o mesmo volume duas vezes, porque
o volume das regioes e SOMADO. O formulario avisa, e a arvore de `pai` e o que
permite saber que uma esta dentro da outra.
"""

from __future__ import annotations

import logging
import unicodedata

import httpx

from apps.radar import custos
from apps.radar.models import ChamadaExterna, ContasExternas, LocalDisponivel
from apps.radar.provedores import (
    DATAFORSEO_BASE,
    TIMEOUT,
    ProvedorIndisponivel,
    _credenciais_dataforseo,
    conferir_http_dataforseo,
)

logger = logging.getLogger("publibot.radar")

# Tipos que fazem sentido como regiao de um site. CEP, aeroporto e
# universidade existem na lista do Google Ads e so poluiriam o seletor.
TIPOS = {
    "Country": "pais",
    "State": "estado",
    "Region": "regiao",
    "City": "cidade",
    "Municipality": "municipio",
    "District": "distrito",
    "Neighborhood": "bairro",
}


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def _nome_legivel(nome_do_google: str) -> str:
    """'Curitiba,State of Parana,Brazil' -> 'Curitiba, Parana'."""
    partes = [p.strip() for p in nome_do_google.split(",") if p.strip()]
    if len(partes) > 1:
        partes = partes[:-1]  # o pais, que e o mesmo para todos
    return ", ".join(p.removeprefix("State of ") for p in partes) or nome_do_google


def atualizar_lista(contas: ContasExternas, *, pais: str = "br") -> int:
    """Baixa os locais do pais e guarda. Devolve quantos ficaram."""
    login, senha = _credenciais_dataforseo(contas)
    caminho = f"/keywords_data/google_ads/locations/{pais.lower()}"
    try:
        resposta = httpx.get(f"{DATAFORSEO_BASE}{caminho}", auth=(login, senha), timeout=TIMEOUT)
        conferir_http_dataforseo(resposta)
        dados = resposta.json()
        if dados.get("status_code") != 20000:
            raise ProvedorIndisponivel(
                f"DataForSEO: {dados.get('status_code')} {dados.get('status_message', '')}"
            )
    except (httpx.HTTPError, ValueError, ProvedorIndisponivel) as exc:
        custos.registrar(
            provedor=ChamadaExterna.Provedor.DATAFORSEO,
            endpoint=caminho.lstrip("/"),
            finalidade=ChamadaExterna.Finalidade.RADAR,
            consulta="lista de locais",
            sucesso=False,
            erro=str(exc),
        )
        if isinstance(exc, ProvedorIndisponivel):
            raise
        raise ProvedorIndisponivel(f"DataForSEO nao respondeu: {exc}") from exc

    itens = (dados.get("tasks") or [{}])[0].get("result") or []
    novos = []
    for item in itens:
        tipo = item.get("location_type") or ""
        if tipo not in TIPOS or not item.get("location_code"):
            continue
        nome = _nome_legivel(item.get("location_name") or "")
        novos.append(
            LocalDisponivel(
                codigo=int(item["location_code"]),
                nome=nome[:200],
                tipo=tipo,
                pai=item.get("location_code_parent") or None,
                pais=(item.get("country_iso_code") or pais).upper()[:2],
                busca=normalizar(nome)[:200],
            )
        )
    LocalDisponivel.objects.bulk_create(
        novos,
        update_conflicts=True,
        unique_fields=["codigo"],
        update_fields=["nome", "tipo", "pai", "pais", "busca"],
    )
    custos.registrar(
        provedor=ChamadaExterna.Provedor.DATAFORSEO,
        endpoint=caminho.lstrip("/"),
        finalidade=ChamadaExterna.Finalidade.RADAR,
        consulta="lista de locais",
        custo=dados.get("cost") or 0,
        itens=len(novos),
    )
    return len(novos)


def procurar(texto: str, *, limite: int = 15) -> list[dict]:
    """Locais cujo nome contem o texto; cidades e estados primeiro."""
    termo = normalizar(texto)
    if len(termo) < 2:
        return []
    ordem = {"State": 0, "City": 1, "Municipality": 2, "Region": 3, "Country": 4}
    achados = list(LocalDisponivel.objects.filter(busca__contains=termo)[:200])
    achados.sort(key=lambda x: (not x.busca.startswith(termo), ordem.get(x.tipo, 9), x.nome))
    return [
        {"codigo": x.codigo, "nome": x.nome, "tipo": TIPOS.get(x.tipo, x.tipo)}
        for x in achados[:limite]
    ]


def _ancestrais(codigo: int) -> set[int]:
    vistos: set[int] = set()
    atual = LocalDisponivel.objects.filter(codigo=codigo).values_list("pai", flat=True).first()
    while atual and atual not in vistos:
        vistos.add(atual)
        atual = LocalDisponivel.objects.filter(codigo=atual).values_list("pai", flat=True).first()
    return vistos


def sobrepostas(regioes: list[dict]) -> list[tuple[str, str]]:
    """Pares (de dentro, de fora) de regioes em que uma contem a outra."""
    codigos = {int(r["codigo"]): r.get("nome") or str(r["codigo"]) for r in regioes}
    pares = []
    for codigo, nome in codigos.items():
        for acima in _ancestrais(codigo) & set(codigos):
            pares.append((nome, codigos[acima]))
    return pares
