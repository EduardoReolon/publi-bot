"""Um adaptador por instituicao: traduz o formato dela para um so.

A geracao do artigo nunca ve a estrutura de cada lugar. Ela pede o valor de
uma serie, num local, e recebe `ValorObservado`. Adaptador que ainda nao foi
escrito (`pronto = False`) nao quebra nada: o catalogo usa os valores ja
gravados (digitados a mao, por exemplo).

Para escrever um adaptador: implemente `procurar` (o catalogo da instituicao,
para sugerir series) e `valores` (os numeros de uma serie), marque `pronto`, e
registre em ADAPTADORES. Teste com respostas gravadas, sem rede.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


class AdaptadorPendente(Exception):
    """O adaptador desta instituicao ainda nao foi escrito."""


@dataclass
class ValorObservado:
    periodo: str
    valor: Decimal
    local: str = "Brasil"
    nota: str = ""


@dataclass
class SerieEncontrada:
    codigo: str
    titulo: str
    descricao: str = ""
    unidade: str = ""
    recortes: list = field(default_factory=list)
    periodicidade: str = ""
    url: str = ""


class Adaptador:
    nome = ""
    pronto = False

    def procurar(self, termo: str, limite: int = 20) -> list[SerieEncontrada]:
        raise AdaptadorPendente(f"o adaptador {self.nome} ainda nao foi escrito.")

    def valores(self, serie, *, local: str = "Brasil", ultimos: int = 1) -> list[ValorObservado]:
        """`local`: "Brasil" ou o nome do estado (apps/dados/locais.py); traduza
        para o codigo da instituicao. Sem esse recorte, devolva []."""
        raise AdaptadorPendente(f"o adaptador {self.nome} ainda nao foi escrito.")

    def codigo_do_link(self, url: str) -> str:
        """O codigo da serie num link citado pelas fontes ("" se nao reconhece).
        Ex.: sidra.ibge.gov.br/tabela/4752 -> "4752". Alimenta o catalogo pelo acervo."""
        return ""


def _chave(texto: str) -> str:
    import unicodedata

    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def _numero(texto) -> Decimal | None:
    """Valor como as APIs mandam ("190755799", "12.5", 0.44). Os simbolos de
    ausencia do IBGE ("-", "...", "X", "..") e vazio viram None."""
    if texto is None:
        return None
    try:
        return Decimal(str(texto).strip())
    except (InvalidOperation, ValueError):
        return None


class _ComRede(Adaptador):
    """Um GET com JSON, com uma nova tentativa em falha de rede ou 5xx."""

    TEMPO = 30.0

    def _get(self, url: str, params: dict | None = None):
        import httpx

        ultima: Exception | None = None
        for _tentativa in range(2):
            try:
                resposta = httpx.get(url, params=params, timeout=self.TEMPO, follow_redirects=True)
                if resposta.status_code >= 500:
                    ultima = httpx.HTTPStatusError(
                        f"HTTP {resposta.status_code}", request=resposta.request, response=resposta
                    )
                    continue
                resposta.raise_for_status()
                return resposta.json()
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                ultima = exc
        raise ultima or RuntimeError(url)


def periodo_ordenavel(bruto: str, periodicidade: str = "") -> str:
    """O periodo como a instituicao manda, numa forma que ordena como texto
    (`Valor` e ordenado por periodo): "2019", "2024-08", "2024-T1", "2024-08-15".

    IBGE: "2019" (anual), "201908" (mensal) e "201901" (1o trimestre).
    BCB: "15/08/2024" (a data do dado; mensal vem no dia 1)."""
    bruto = str(bruto).strip()
    freq = _chave(periodicidade)
    if re.fullmatch(r"\d{4}", bruto):
        return bruto
    if re.fullmatch(r"\d{6}", bruto):
        ano, resto = bruto[:4], bruto[4:]
        if "trimes" in freq:
            return f"{ano}-T{int(resto)}"
        return f"{ano}-{resto}"
    data = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", bruto)
    if data:
        dia, mes, ano = data.groups()
        if "anu" in freq:
            return ano
        if "mens" in freq or (not freq and dia == "01"):
            return f"{ano}-{mes}"
        return f"{ano}-{mes}-{dia}"
    return bruto


def periodo_legivel(periodo: str) -> str:
    """Para o texto: "2024-08" -> "ago/2024"; "2024-T1" -> "1º tri/2024";
    "2024-08-15" -> "15/08/2024"; o resto como esta."""
    meses = "jan fev mar abr mai jun jul ago set out nov dez".split()
    if m := re.fullmatch(r"(\d{4})-(\d{2})", periodo or ""):
        return f"{meses[int(m.group(2)) - 1]}/{m.group(1)}"
    if m := re.fullmatch(r"(\d{4})-T(\d)", periodo or ""):
        return f"{m.group(2)}º tri/{m.group(1)}"
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", periodo or ""):
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return periodo


# Codigo IBGE de cada estado (o mesmo do SIDRA, do IBGE e do DATASUS).
UF_IBGE = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
    "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}  # fmt: skip


class IBGE(_ComRede):
    """SIDRA pela API de agregados (servicodados.ibge.gov.br/api/v3/agregados).

    O codigo da serie e "tabela/variavel" (ex.: "4752/1000"), com um filtro de
    classificacao opcional no fim, no formato da API ("4752/1000/2[6794]").
    Tabela sem variavel (veio de um link citado) usa a primeira variavel dela.
    Recortes: Brasil (N1) e estado (N3)."""

    nome = "IBGE"
    pronto = True
    BASE = "https://servicodados.ibge.gov.br/api/v3/agregados"

    def procurar(self, termo: str, limite: int = 20) -> list[SerieEncontrada]:
        palavras = _chave(termo).split()
        if not palavras:
            return []
        achadas = []
        for pesquisa in self._get(self.BASE) or []:
            for agregado in pesquisa.get("agregados") or []:
                texto = _chave(f"{pesquisa.get('nome', '')} {agregado.get('nome', '')}")
                if all(p in texto for p in palavras):
                    achadas.append(agregado)
        saida: list[SerieEncontrada] = []
        for agregado in achadas:
            saida += self._series_da_tabela(str(agregado["id"]), limite - len(saida))
            if len(saida) >= limite:
                break
        return saida[:limite]

    def _metadados(self, tabela: str) -> dict:
        return self._get(f"{self.BASE}/{tabela}/metadados") or {}

    def _series_da_tabela(self, tabela: str, limite: int) -> list[SerieEncontrada]:
        meta = self._metadados(tabela)
        niveis = (meta.get("nivelTerritorial") or {}).get("Administrativo") or []
        recortes = [nome for nome, n in (("Brasil", "N1"), ("estado", "N3")) if n in niveis]
        frequencia = (meta.get("periodicidade") or {}).get("frequencia", "")
        descricao = " — ".join(x for x in (meta.get("nome"), meta.get("pesquisa")) if x)
        return [
            SerieEncontrada(
                codigo=f"{tabela}/{v['id']}",
                titulo=str(v.get("nome", ""))[:300],
                descricao=descricao[:1000],
                unidade=str(v.get("unidade") or "")[:60],
                recortes=recortes,
                periodicidade=frequencia,
                url=f"https://sidra.ibge.gov.br/tabela/{tabela}",
            )
            for v in (meta.get("variaveis") or [])[: max(limite, 0)]
        ]

    def valores(self, serie, *, local: str = "Brasil", ultimos: int = 1) -> list[ValorObservado]:
        from apps.dados.locais import BRASIL, normalizar, sigla

        local = normalizar(local)
        if local == BRASIL:
            localidade = "N1[all]"
        elif sigla(local) in UF_IBGE:
            localidade = f"N3[{UF_IBGE[sigla(local)]}]"
        else:
            return []
        partes = serie.codigo.split("/")
        tabela = partes[0]
        variavel = partes[1] if len(partes) > 1 else ""
        if not variavel:
            primeira = (self._metadados(tabela).get("variaveis") or [{}])[0]
            variavel = str(primeira.get("id", ""))
            if not variavel:
                return []
        params = {"localidades": localidade}
        if len(partes) > 2:
            params["classificacao"] = "/".join(partes[2:])
        dados = self._get(
            f"{self.BASE}/{tabela}/periodos/-{max(int(ultimos), 1)}/variaveis/{variavel}",
            params,
        )
        return self._ler_valores(dados, local, serie.periodicidade)

    @staticmethod
    def _total(resultado: dict) -> bool:
        """Resultado do total de cada classificacao (sem recorte por sexo,
        idade...): e o numero que um artigo cita."""
        for classificacao in resultado.get("classificacoes") or []:
            nomes = [_chave(str(n)) for n in (classificacao.get("categoria") or {}).values()]
            if nomes and not all(n == "total" for n in nomes):
                return False
        return True

    def _ler_valores(self, dados, local: str, periodicidade: str) -> list[ValorObservado]:
        saida = []
        for variavel in dados or []:
            resultados = variavel.get("resultados") or []
            escolhidos = [r for r in resultados if self._total(r)] or resultados[:1]
            for resultado in escolhidos[:1]:
                for serie in resultado.get("series") or []:
                    for periodo, bruto in (serie.get("serie") or {}).items():
                        numero = _numero(bruto)
                        if numero is None:
                            continue
                        saida.append(
                            ValorObservado(
                                periodo=periodo_ordenavel(periodo, periodicidade),
                                valor=numero,
                                local=local,
                            )
                        )
        return sorted(saida, key=lambda v: v.periodo, reverse=True)

    def codigo_do_link(self, url: str) -> str:
        achado = re.search(r"sidra\.ibge\.gov\.br/(?:tabela|Tabela)/(\d+)", url or "")
        if achado:
            return achado.group(1)
        achado = re.search(r"apisidra\.ibge\.gov\.br/values/t/(\d+)", url or "")
        return achado.group(1) if achado else ""


class BancoCentral(_ComRede):
    """SGS (series temporais). Busca pelo portal de dados abertos do BC (CKAN),
    que lista cada serie com o link `bcdata.sgs.{codigo}`; valores pela API do
    SGS. So o Brasil: o SGS nao recorta por estado."""

    nome = "Banco Central"
    pronto = True
    BUSCA = "https://dadosabertos.bcb.gov.br/api/3/action/package_search"
    SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}"
    _CODIGO = re.compile(r"bcdata\.sgs\.(\d+)")

    def procurar(self, termo: str, limite: int = 20) -> list[SerieEncontrada]:
        if not termo.strip():
            return []
        dados = self._get(self.BUSCA, {"q": termo, "rows": limite}) or {}
        saida = []
        for pacote in (dados.get("result") or {}).get("results") or []:
            codigo = next(
                (
                    achado.group(1)
                    for recurso in pacote.get("resources") or []
                    if (achado := self._CODIGO.search(str(recurso.get("url", ""))))
                ),
                "",
            )
            if not codigo:
                continue
            extras = {e.get("key"): e.get("value") for e in pacote.get("extras") or []}
            saida.append(
                SerieEncontrada(
                    codigo=codigo,
                    titulo=str(pacote.get("title") or pacote.get("name") or "")[:300],
                    descricao=str(pacote.get("notes") or "")[:1000],
                    unidade=str(extras.get("unidade") or extras.get("Unidade") or "")[:60],
                    recortes=["Brasil"],
                    periodicidade=str(
                        extras.get("periodicidade") or extras.get("Periodicidade") or ""
                    )[:40],
                    url=f"https://dadosabertos.bcb.gov.br/dataset/{pacote.get('name', codigo)}",
                )
            )
        return saida[:limite]

    def valores(self, serie, *, local: str = "Brasil", ultimos: int = 1) -> list[ValorObservado]:
        from apps.dados.locais import BRASIL, normalizar

        if normalizar(local) != BRASIL:
            return []
        n = min(max(int(ultimos), 1), 20)  # a API aceita no maximo 20
        dados = self._get(self.SGS.format(codigo=serie.codigo, n=n), {"formato": "json"})
        saida = []
        for item in dados or []:
            numero = _numero(item.get("valor"))
            if numero is None:
                continue
            saida.append(
                ValorObservado(
                    periodo=periodo_ordenavel(item.get("data", ""), serie.periodicidade),
                    valor=numero,
                    local=BRASIL,
                )
            )
        return sorted(saida, key=lambda v: v.periodo, reverse=True)

    def codigo_do_link(self, url: str) -> str:
        achado = self._CODIGO.search(url or "")
        return achado.group(1) if achado else ""


class OMS(_ComRede):
    """Global Health Observatory (OData): indicadores por pais. So o Brasil.
    Os nomes dos indicadores estao em ingles: procure em ingles."""

    nome = "OMS"
    pronto = True
    BASE = "https://ghoapi.azureedge.net/api"
    # Recorte "ambos os sexos"/sem recorte: o numero que um artigo cita.
    _TOTAL = {None, "", "SEX_BTSX", "BTSX", "BOTHSEXES"}

    def procurar(self, termo: str, limite: int = 20) -> list[SerieEncontrada]:
        termo = termo.replace("'", "").strip()
        if not termo:
            return []
        dados = self._get(
            f"{self.BASE}/Indicator", {"$filter": f"contains(IndicatorName,'{termo}')"}
        )
        return [
            SerieEncontrada(
                codigo=str(item["IndicatorCode"]),
                titulo=str(item.get("IndicatorName", ""))[:300],
                recortes=["Brasil"],
                url=f"{self.BASE}/{item['IndicatorCode']}",
            )
            for item in (dados or {}).get("value", [])[:limite]
            if item.get("IndicatorCode")
        ]

    def valores(self, serie, *, local: str = "Brasil", ultimos: int = 1) -> list[ValorObservado]:
        from apps.dados.locais import BRASIL, normalizar

        if normalizar(local) != BRASIL:
            return []
        dados = self._get(f"{self.BASE}/{serie.codigo}", {"$filter": "SpatialDim eq 'BRA'"})
        por_ano: dict[str, ValorObservado] = {}
        for item in (dados or {}).get("value", []):
            if item.get("Dim1") not in self._TOTAL or item.get("Dim2") or item.get("Dim3"):
                continue
            numero = _numero(item.get("NumericValue"))
            ano = str(item.get("TimeDim") or "")
            if numero is None or not ano:
                continue
            por_ano[ano] = ValorObservado(periodo=ano, valor=numero, local=BRASIL)
        return [por_ano[a] for a in sorted(por_ano, reverse=True)[: max(int(ultimos), 1)]]

    def codigo_do_link(self, url: str) -> str:
        achado = re.search(r"ghoapi\.azureedge\.net/api/([A-Za-z0-9_]+)", url or "")
        if achado and achado.group(1) != "Indicator":
            return achado.group(1)
        return ""


@contextmanager
def arquivo_temporario(url: str, *, limite_mb: int = 2048):
    """Baixa um arquivo grande (DATASUS, planilhas) para um temporario e APAGA
    ao sair, deu certo ou nao. O adaptador le, resume (por ano e estado) e
    grava so o resumo em `Valor`: o servidor nao guarda a base inteira.
    Passou de `limite_mb`: para e apaga."""
    import os
    import tempfile

    import httpx

    caminho = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, prefix="publibot-dados-") as destino:
            caminho = destino.name
            baixado = 0
            with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as resposta:
                resposta.raise_for_status()
                for pedaco in resposta.iter_bytes(1 << 20):
                    baixado += len(pedaco)
                    if baixado > limite_mb * (1 << 20):
                        raise ValueError(f"{url} passou de {limite_mb} MB.")
                    destino.write(pedaco)
        yield caminho
    finally:
        if caminho and os.path.exists(caminho):
            os.remove(caminho)


ADAPTADORES: dict[str, Adaptador] = {"ibge": IBGE(), "bcb": BancoCentral(), "oms": OMS()}


def adaptador_de(instituicao) -> Adaptador | None:
    return ADAPTADORES.get(instituicao.adaptador or "")


def situacao_do_adaptador(instituicao) -> str:
    """ "pronto", "a_fazer" (previsto, sem codigo ainda) ou "nenhum"."""
    adaptador = adaptador_de(instituicao)
    if adaptador is None:
        return "nenhum"
    return "pronto" if adaptador.pronto else "a_fazer"
