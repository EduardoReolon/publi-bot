#!/usr/bin/env python3
"""Empacota o projeto em blocos de contexto, um arquivo .md por bloco.

O problema que isto resolve: uma IA de contexto pequeno nao cabe o repositorio
inteiro, e um arquivo solto nao se explica. O meio-termo util e o **bloco** —
os arquivos que respondem juntos por uma capacidade do sistema, num unico .md
com orientacao de leitura na frente.

A promessa que exige cuidado e a outra: *acrescentei um arquivo, ele ja entra no
bloco certo.* Se a lista de arquivos fosse escrita a mao, todo modulo novo
ficaria de fora em silencio — e o pior sintoma possivel aqui e um bloco que
parece completo e nao esta, porque a IA responde com confianca sobre um codigo
que nao viu inteiro.

Por isso a atribuicao e por PADRAO de caminho, cada area termina num curinga, e
o que nao casar com nada faz o comando **falhar** dizendo o nome do arquivo.
Ver scripts/blocos.toml.

Uso:

    python scripts/gerar_blocos.py                  # todos os blocos
    python scripts/gerar_blocos.py capas publicacao # so estes
    python scripts/gerar_blocos.py --com-testes     # inclui os testes
    python scripts/gerar_blocos.py --listar         # so o resumo, sem escrever
    python scripts/gerar_blocos.py --conferir       # so valida a cobertura

Ver docs/BLOCOS.md.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "scripts" / "blocos.toml"
SAIDA_PADRAO = RAIZ / "docs" / "blocos"

# Aproximacao grosseira, e assumida como tal: ~3.5 caracteres por token e o que
# se observa em codigo com identificadores longos. Serve para decidir "isto cabe
# num contexto de 32k?", nao para orcamento fino.
CARACTERES_POR_TOKEN = 3.5

# Acima disto, o bloco provavelmente nao cabe confortavelmente num modelo
# pequeno junto da pergunta e da resposta. Nao impede nada — avisa.
TOKENS_DE_ALERTA = 30_000

LINGUAGENS = {
    ".py": "python",
    ".html": "html",
    ".css": "css",
    ".js": "javascript",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".sh": "bash",
    ".sql": "sql",
    ".md": "markdown",
    ".txt": "text",
    ".cfg": "ini",
    ".ini": "ini",
    ".conf": "nginx",
    ".service": "ini",
    ".timer": "ini",
    ".example": "bash",
}


def numero(valor: int) -> str:
    """Milhar com ponto, como se escreve em portugues.

    Existe como funcao porque a primeira versao formatava com `,` e trocava a
    virgula por ponto no texto ja pronto — o que tambem atingia as virgulas dos
    TITULOS, e "embeddings, limiar e saude" virava "embeddings. limiar e saude".
    """
    return f"{valor:,}".replace(",", ".")


@dataclass
class Bloco:
    nome: str
    titulo: str
    resumo: str
    padroes: list[str]
    entrada: list[str] = field(default_factory=list)
    relacionados: list[str] = field(default_factory=list)
    arquivos: list[Path] = field(default_factory=list)

    @property
    def bytes_totais(self) -> int:
        return sum(caminho.stat().st_size for caminho in self.arquivos)

    @property
    def tokens_estimados(self) -> int:
        return int(self.bytes_totais / CARACTERES_POR_TOKEN)


class ConfiguracaoInvalida(RuntimeError):
    """O blocos.toml nao descreve algo utilizavel."""


class ArquivosOrfaos(RuntimeError):
    """Ha arquivo no projeto que nao pertence a bloco nenhum."""


# ---------------------------------------------------------------------------
# Leitura da configuracao e dos arquivos
# ---------------------------------------------------------------------------
def carregar_config(caminho: Path = CONFIG) -> tuple[list[Bloco], list[str], list[str]]:
    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))

    brutos = dados.get("bloco") or []
    if not brutos:
        raise ConfiguracaoInvalida(f"{caminho} nao define nenhum [[bloco]].")

    blocos = []
    vistos = set()
    for bruto in brutos:
        nome = bruto.get("nome", "")
        if not nome:
            raise ConfiguracaoInvalida("ha um [[bloco]] sem 'nome'.")
        if nome in vistos:
            raise ConfiguracaoInvalida(f"o bloco {nome!r} aparece duas vezes.")
        if not bruto.get("padroes"):
            raise ConfiguracaoInvalida(f"o bloco {nome!r} nao tem 'padroes'.")
        vistos.add(nome)

        blocos.append(
            Bloco(
                nome=nome,
                titulo=bruto.get("titulo", nome),
                resumo=(bruto.get("resumo") or "").strip(),
                padroes=list(bruto["padroes"]),
                entrada=list(bruto.get("entrada") or []),
                relacionados=list(bruto.get("relacionados") or []),
            )
        )

    # Um bloco relacionado que nao existe e erro de digitacao, e o efeito seria
    # mandar a pessoa procurar um arquivo que nao ha.
    for bloco in blocos:
        for referido in bloco.relacionados:
            if referido not in vistos:
                raise ConfiguracaoInvalida(
                    f"o bloco {bloco.nome!r} aponta para {referido!r}, que nao existe."
                )

    return blocos, list(dados.get("ignorar") or []), list(dados.get("padroes_de_teste") or [])


def arquivos_do_projeto() -> list[str]:
    """Os arquivos versionados, na visao do git.

    Perguntar ao git e nao caminhar o disco resolve de graca tres coisas que
    dariam trabalho: respeita o .gitignore, ignora venv e caches, e nao inclui
    a propria pasta de saida se alguem decidir versiona-la.
    """
    resultado = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(linha for linha in resultado.stdout.splitlines() if linha.strip())


def casa(caminho: str, padroes: list[str]) -> bool:
    """Casamento de glob, com `**` valendo tambem para zero diretorios.

    `fnmatch` sozinho nao trata `apps/content/**` como "a pasta inteira,
    inclusive os arquivos na raiz dela" — e essa e exatamente a forma como um
    curinga de area e escrito aqui.
    """
    for padrao in padroes:
        if fnmatch.fnmatch(caminho, padrao):
            return True
        if padrao.endswith("/**") and fnmatch.fnmatch(caminho, padrao[:-3] + "/*"):
            return True
    return False


def distribuir(
    blocos: list[Bloco], arquivos: list[str], *, ignorar: list[str]
) -> tuple[list[str], list[str]]:
    """Poe cada arquivo no primeiro bloco cujo padrao casar.

    Devolve (ignorados, orfaos). O primeiro que casa vence: e o que permite ter
    um bloco especifico antes do curinga da area, sem que o curinga engula o
    especifico.
    """
    ignorados, orfaos = [], []

    for arquivo in arquivos:
        if casa(arquivo, ignorar):
            ignorados.append(arquivo)
            continue

        for bloco in blocos:
            if casa(arquivo, bloco.padroes):
                bloco.arquivos.append(RAIZ / arquivo)
                break
        else:
            orfaos.append(arquivo)

    return ignorados, orfaos


# `from apps.x.y import ...` e `import apps.x.y`, inclusive dentro de funcao —
# que e como boa parte deste projeto importa, para evitar ciclo.
PADRAO_DE_IMPORT = re.compile(r"^\s*(?:from|import)\s+((?:apps|core)[\w.]*)", re.MULTILINE)


def dependencias_reais(blocos: list[Bloco]) -> dict[str, list[str]]:
    """De que outros blocos cada bloco importa, de fato.

    Escrever `relacionados` a mao parece suficiente e nao e: o primeiro exame
    deste projeto mostrou que TODOS os blocos importavam de vizinhos que nao
    declaravam. A lista "fora deste bloco" e uma instrucao para a IA — se ela
    esta incompleta, o modelo supoe em vez de dizer que falta contexto, que e
    exatamente o contrario do que se quer.

    Derivar do codigo custa uma passada e nao envelhece.
    """
    dono: dict[str, str] = {}
    for bloco in blocos:
        for caminho in bloco.arquivos:
            nome = relativo(caminho)
            if nome.endswith(".py"):
                dono[nome[:-3].replace("/", ".")] = bloco.nome

    encontrados: dict[str, list[str]] = {}
    for bloco in blocos:
        vizinhos: set[str] = set()
        for caminho in bloco.arquivos:
            if caminho.suffix != ".py":
                continue
            try:
                texto = caminho.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for achado in PADRAO_DE_IMPORT.finditer(texto):
                modulo = achado.group(1)
                # `from apps.content.models import X` aponta para o modulo; um
                # import de pacote (`apps.content`) nao aponta para arquivo
                # nenhum e e ignorado por nao estar no mapa.
                alvo = dono.get(modulo)
                if alvo and alvo != bloco.nome:
                    vizinhos.add(alvo)
        encontrados[bloco.nome] = sorted(vizinhos)
    return encontrados


def vizinhanca(bloco: Bloco, detectados: list[str]) -> list[str]:
    """Os relacionados escritos a mao primeiro, depois o que o codigo revelou.

    Os dois servem a coisas diferentes: o curado diz o que e conceitualmente
    proximo (e vale ler junto), o detectado garante que nada que o codigo
    alcanca fique sem aviso.
    """
    ordenados = list(bloco.relacionados)
    ordenados += [n for n in detectados if n not in ordenados]
    return ordenados


# ---------------------------------------------------------------------------
# Escrita do Markdown
# ---------------------------------------------------------------------------
def cerca_para(conteudo: str) -> str:
    """Cerca de codigo mais longa que qualquer sequencia de crases no conteudo.

    Sem isto, um arquivo .md com bloco de codigo dentro fecha a cerca externa no
    meio, e o resto do bloco vaza como texto solto — a IA recebe o arquivo
    partido ao meio sem nenhum sinal de que isso aconteceu.
    """
    maior = 0
    atual = 0
    for caractere in conteudo:
        atual = atual + 1 if caractere == "`" else 0
        maior = max(maior, atual)
    return "`" * max(3, maior + 1)


def relativo(caminho: Path) -> str:
    """Caminho a partir da raiz do projeto, ou absoluto se estiver fora dela.

    O `--saida` aceita qualquer pasta, inclusive uma fora do repositorio, e
    `relative_to` levanta em vez de devolver `..`.
    """
    try:
        return caminho.relative_to(RAIZ).as_posix()
    except ValueError:
        return str(caminho)


def montar_markdown(bloco: Bloco, *, com_testes: bool, vizinhos: list[str] | None = None) -> str:
    partes = [
        f"# {bloco.titulo}",
        "",
        f"> Bloco `{bloco.nome}` do projeto PubliBot — {len(bloco.arquivos)} arquivo(s), "
        f"~{numero(bloco.tokens_estimados)} tokens estimados."
        f"{'' if com_testes else ' Gerado SEM os testes.'}",
        "",
        bloco.resumo,
        "",
        "## Como ler este arquivo",
        "",
        "Este e um recorte fechado do projeto: os arquivos abaixo respondem",
        "juntos pela capacidade descrita acima. Cada um vem inteiro, com o",
        "caminho real no repositorio como titulo.",
        "",
        "Os comentarios no codigo explicam **por que** cada decisao foi tomada,",
        "e nao o que a linha faz. Sao a parte mais informativa do texto.",
        "",
    ]

    if bloco.entrada:
        partes += ["**Comece por:**", ""]
        partes += [f"- `{e}`" for e in bloco.entrada]
        partes.append("")

    vizinhos = bloco.relacionados if vizinhos is None else vizinhos
    if vizinhos:
        partes += [
            "**Fora deste bloco.** O codigo aqui alcanca os blocos abaixo, que",
            "NAO estao neste arquivo. Se a resposta depender de algum deles,",
            "diga que falta contexto em vez de supor:",
            "",
        ]
        partes += [f"- `{r}`" for r in vizinhos]
        partes.append("")

    partes += ["## Arquivos", ""]
    for caminho in bloco.arquivos:
        tamanho = caminho.stat().st_size
        partes.append(f"- `{relativo(caminho)}` — {tamanho / 1024:.1f} KB")
    partes += ["", "---", ""]

    for caminho in bloco.arquivos:
        nome = relativo(caminho)
        try:
            conteudo = caminho.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as erro:
            # Um binario que escapou dos padroes de ignorar. Registrar e seguir
            # e melhor que derrubar o bloco inteiro por causa de um arquivo.
            partes += [f"## `{nome}`", "", f"> Nao foi possivel ler: {erro}", ""]
            continue

        cerca = cerca_para(conteudo)
        linguagem = LINGUAGENS.get(caminho.suffix, "")
        partes += [
            f"## `{nome}`",
            "",
            f"{cerca}{linguagem}",
            conteudo.rstrip("\n"),
            cerca,
            "",
        ]

    return "\n".join(partes) + "\n"


def montar_indice(blocos: list[Bloco], *, com_testes: bool) -> str:
    partes = [
        "# Blocos de contexto",
        "",
        "Cada arquivo desta pasta e um recorte fechado do projeto, pronto para",
        "ser enviado inteiro a uma IA de contexto pequeno.",
        "",
        f"Gerado por `scripts/gerar_blocos.py`{'' if com_testes else ' (sem os testes)'}.",
        "**Nao edite nada aqui** — a proxima geracao sobrescreve.",
        "",
        "| Bloco | Arquivos | ~Tokens | Assunto |",
        "|---|---:|---:|---|",
    ]
    for bloco in sorted(blocos, key=lambda b: b.nome):
        if not bloco.arquivos:
            continue
        alerta = " ⚠️" if bloco.tokens_estimados > TOKENS_DE_ALERTA else ""
        partes.append(
            f"| [`{bloco.nome}`]({bloco.nome}.md) | {len(bloco.arquivos)} | "
            f"{numero(bloco.tokens_estimados)}{alerta} | {bloco.titulo} |"
        )

    partes += [
        "",
        f"⚠️ = acima de {numero(TOKENS_DE_ALERTA)} tokens estimados; "
        "pode nao caber com folga num modelo pequeno.",
        "",
        "A estimativa e grosseira (~3,5 caracteres por token) e serve para",
        "decidir se cabe, nao para orcamento fino.",
        "",
    ]
    return "\n".join(partes) + "\n"


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------
def relatar(blocos: list[Bloco], ignorados: list[str], orfaos: list[str]) -> None:
    largura = max(len(b.nome) for b in blocos)
    print(f"{'bloco'.ljust(largura)}  arqs   ~tokens")
    print("-" * (largura + 18))

    for bloco in blocos:
        alerta = "  <- grande" if bloco.tokens_estimados > TOKENS_DE_ALERTA else ""
        vazio = "  <- vazio" if not bloco.arquivos else ""
        print(
            f"{bloco.nome.ljust(largura)}  {len(bloco.arquivos):>4}  "
            f"{numero(bloco.tokens_estimados):>8}{alerta}{vazio}"
        )

    total = sum(b.tokens_estimados for b in blocos)
    print("-" * (largura + 18))
    arquivos = sum(len(b.arquivos) for b in blocos)
    print(f"{'total'.ljust(largura)}  {arquivos:>4}  {numero(total):>8}")
    print(f"\n{len(ignorados)} arquivo(s) ignorado(s) por padrao.")

    if orfaos:
        print(f"\nERRO: {len(orfaos)} arquivo(s) fora de qualquer bloco:")
        for arquivo in orfaos:
            print(f"  {arquivo}")
        print("\nAcrescente um padrao em scripts/blocos.toml — antes do curinga da area.")


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(
        description="Empacota o projeto em blocos de contexto (.md) para IAs de contexto pequeno.",
    )
    analisador.add_argument("blocos", nargs="*", help="nomes a gerar. Sem nenhum, gera todos.")
    analisador.add_argument(
        "--com-testes",
        action="store_true",
        help="inclui os arquivos de teste. Explicam bem o comportamento e dobram o tamanho.",
    )
    analisador.add_argument(
        "--saida",
        type=Path,
        default=SAIDA_PADRAO,
        help=f"pasta de destino (padrao: {SAIDA_PADRAO.relative_to(RAIZ)})",
    )
    analisador.add_argument(
        "--listar", action="store_true", help="so mostra o resumo, sem escrever arquivo."
    )
    analisador.add_argument(
        "--conferir",
        action="store_true",
        help="so confere que todo arquivo cabe num bloco. Serve para rodar em CI.",
    )
    argumentos = analisador.parse_args(argv)

    try:
        blocos, ignorar, padroes_de_teste = carregar_config()
    except (ConfiguracaoInvalida, tomllib.TOMLDecodeError) as erro:
        print(f"ERRO na configuracao: {erro}", file=sys.stderr)
        return 2

    if not argumentos.com_testes:
        ignorar = ignorar + padroes_de_teste

    ignorados, orfaos = distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar)

    if argumentos.conferir:
        if orfaos:
            relatar(blocos, ignorados, orfaos)
            return 1
        print(
            f"Cobertura completa: {sum(len(b.arquivos) for b in blocos)} arquivo(s) em "
            f"{len([b for b in blocos if b.arquivos])} bloco(s)."
        )
        return 0

    if orfaos:
        relatar(blocos, ignorados, orfaos)
        return 1

    if argumentos.listar:
        relatar(blocos, ignorados, orfaos)
        return 0

    escolhidos = blocos
    if argumentos.blocos:
        por_nome = {b.nome: b for b in blocos}
        desconhecidos = [n for n in argumentos.blocos if n not in por_nome]
        if desconhecidos:
            print(f"\nERRO: bloco(s) inexistente(s): {', '.join(desconhecidos)}", file=sys.stderr)
            print(f"Disponiveis: {', '.join(sorted(por_nome))}", file=sys.stderr)
            return 2
        escolhidos = [por_nome[n] for n in argumentos.blocos]

    detectados = dependencias_reais(blocos)

    destino = argumentos.saida
    destino.mkdir(parents=True, exist_ok=True)

    # Todo o trabalho acontece antes da primeira linha impressa. Relatar
    # enquanto escreve parece mais vivo e tem um custo real:
    # `gerar_blocos.py | head` fecha o stdout, o print levanta BrokenPipeError,
    # e a geracao morre no meio — deixando uma pasta que parece pronta e nao
    # esta, ou pasta nenhuma.
    escritos = []
    for bloco in escolhidos:
        if not bloco.arquivos:
            escritos.append(f"  {bloco.nome}: vazio, nao escrito.")
            continue
        arquivo = destino / f"{bloco.nome}.md"
        texto = montar_markdown(
            bloco,
            com_testes=argumentos.com_testes,
            vizinhos=vizinhanca(bloco, detectados[bloco.nome]),
        )
        arquivo.write_text(texto, encoding="utf-8")
        escritos.append(f"  {relativo(arquivo)}")

    # O indice sempre cobre TODOS os blocos, mesmo numa geracao parcial: um
    # indice que so lista o que acabou de ser gerado esconderia o que existe.
    indice = destino / "README.md"
    indice.write_text(montar_indice(blocos, com_testes=argumentos.com_testes), encoding="utf-8")
    escritos.append(f"  {relativo(indice)}")

    relatar(blocos, ignorados, orfaos)
    print()
    for linha in escritos:
        print(linha)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # `| head` fecha o stdout antes do fim. Neste ponto os arquivos ja
        # foram escritos; so o relatorio ficou pela metade. Redirecionar para
        # devnull evita o "Exception ignored" que o Python imprime ao sair.
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
