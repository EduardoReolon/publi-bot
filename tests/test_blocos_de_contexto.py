"""Os blocos de contexto cobrem o projeto inteiro, e continuam cobrindo.

Este teste existe por causa de um modo de falha especifico e silencioso: um
arquivo novo que nao entra em bloco nenhum. O bloco continua sendo gerado,
continua parecendo completo, e a IA que o recebe responde com confianca sobre
um codigo que nao viu.

Nada aqui toca o banco: sao arquivos e padroes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

from gerar_blocos import (  # noqa: E402
    ConfiguracaoInvalida,
    arquivos_do_projeto,
    carregar_config,
    casa,
    cerca_para,
    dependencias_reais,
    distribuir,
    montar_markdown,
    vizinhanca,
)


@pytest.fixture
def config():
    return carregar_config()


def test_todo_arquivo_do_projeto_cabe_em_algum_bloco(config):
    """A garantia central. Um orfao aqui significa que alguem acrescentou
    codigo numa area nova e o script precisa saber onde ele mora."""
    blocos, ignorar, testes = config

    _, orfaos = distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)

    assert orfaos == [], (
        f"arquivo(s) fora de qualquer bloco: {orfaos}. "
        f"Acrescente um padrao em scripts/blocos.toml, antes do curinga da area."
    )


def test_nenhum_bloco_fica_vazio(config):
    """Bloco vazio e padrao que nao casa mais nada — arquivo renomeado ou
    removido. O nome continua no indice prometendo conteudo que nao existe."""
    blocos, ignorar, testes = config
    distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)

    vazios = [b.nome for b in blocos if not b.arquivos]
    assert vazios == []


def test_cada_arquivo_esta_em_exatamente_um_bloco(config):
    """O mesmo arquivo em dois blocos dobraria o tamanho dos dois sem
    acrescentar informacao."""
    blocos, ignorar, testes = config
    distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)

    todos = [a for b in blocos for a in b.arquivos]
    assert len(todos) == len(set(todos))


def test_o_arquivo_de_entrada_declarado_existe_no_proprio_bloco(config):
    """`entrada` diz "comece por aqui". Apontar para arquivo que nao esta no
    bloco manda a IA procurar o que nao recebeu."""
    blocos, ignorar, testes = config
    distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)

    for bloco in blocos:
        presentes = {a.relative_to(RAIZ).as_posix() for a in bloco.arquivos}
        faltando = [e for e in bloco.entrada if e not in presentes]
        assert faltando == [], f"bloco {bloco.nome}: entrada fora do bloco: {faltando}"


# ---------------------------------------------------------------------------
# Regras de casamento
# ---------------------------------------------------------------------------


def test_curinga_de_area_pega_o_arquivo_na_raiz_da_pasta():
    """`fnmatch` sozinho nao casa `apps/content/**` com `apps/content/novo.py`,
    e e exatamente assim que um curinga de area e escrito."""
    assert casa("apps/content/novo.py", ["apps/content/**"])
    assert casa("apps/content/sub/novo.py", ["apps/content/**"])
    assert not casa("apps/outro/novo.py", ["apps/content/**"])


def test_o_primeiro_bloco_que_casa_leva_o_arquivo():
    """E o que permite um bloco especifico existir antes do curinga da area sem
    ser engolido por ele."""
    from gerar_blocos import Bloco

    especifico = Bloco(nome="especifico", titulo="", resumo="", padroes=["apps/x/alvo.py"])
    curinga = Bloco(nome="curinga", titulo="", resumo="", padroes=["apps/x/**"])

    distribuir([especifico, curinga], ["apps/x/alvo.py", "apps/x/outro.py"], ignorar=[])

    assert [a.name for a in especifico.arquivos] == ["alvo.py"]
    assert [a.name for a in curinga.arquivos] == ["outro.py"]


def test_padrao_de_ignorar_vence_qualquer_bloco():
    from gerar_blocos import Bloco

    bloco = Bloco(nome="tudo", titulo="", resumo="", padroes=["**"])

    ignorados, orfaos = distribuir(
        [bloco], ["apps/x/models.py", "apps/x/migrations/0001.py"], ignorar=["**/migrations/**"]
    )

    assert ignorados == ["apps/x/migrations/0001.py"]
    assert orfaos == []
    assert [a.name for a in bloco.arquivos] == ["models.py"]


# ---------------------------------------------------------------------------
# Vizinhanca declarada no .md
# ---------------------------------------------------------------------------


def test_todo_bloco_que_o_codigo_alcanca_e_avisado(config):
    """A lista "fora deste bloco" e uma instrucao para a IA. Incompleta, o
    modelo supoe em vez de dizer que falta contexto — o oposto do que se quer.

    Escrever isso a mao nao funciona: no primeiro exame deste projeto, TODOS os
    blocos importavam de vizinhos que nao declaravam."""
    blocos, ignorar, testes = config
    distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)

    detectados = dependencias_reais(blocos)

    for bloco in blocos:
        avisados = set(vizinhanca(bloco, detectados[bloco.nome]))
        faltando = set(detectados[bloco.nome]) - avisados
        assert faltando == set(), f"bloco {bloco.nome}: alcanca sem avisar: {sorted(faltando)}"


def test_o_curado_vem_antes_do_detectado(config):
    """O que foi escrito a mao diz o que vale ler junto; o detectado so garante
    que nada fique sem aviso. A ordem carrega essa diferenca."""
    blocos, ignorar, testes = config
    distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)
    detectados = dependencias_reais(blocos)

    bloco = next(b for b in blocos if b.nome == "capas")
    ordem = vizinhanca(bloco, detectados["capas"])

    assert ordem[: len(bloco.relacionados)] == bloco.relacionados
    assert len(ordem) > len(bloco.relacionados), "o teste perdeu o sentido se nada for detectado"


def test_um_bloco_nunca_aparece_como_vizinho_de_si_mesmo(config):
    blocos, ignorar, testes = config
    distribuir(blocos, arquivos_do_projeto(), ignorar=ignorar + testes)
    detectados = dependencias_reais(blocos)

    for nome, vizinhos in detectados.items():
        assert nome not in vizinhos


# ---------------------------------------------------------------------------
# Integridade do Markdown gerado
# ---------------------------------------------------------------------------


def test_cerca_cresce_para_caber_o_conteudo():
    """Um .md com bloco de codigo dentro fecharia a cerca externa no meio, e o
    resto do arquivo vazaria como texto solto — sem nenhum sinal disso."""
    assert cerca_para("codigo comum") == "```"
    assert cerca_para("texto\n```python\nx = 1\n```\n") == "````"
    assert cerca_para("````\nja tem quatro\n````") == "`````"


def test_markdown_aninhado_sobrevive_inteiro(config):
    """Prova de ponta a ponta com um .md real do projeto, que tem cercas de
    codigo dentro. O conteudo precisa sair identico ao original."""
    from gerar_blocos import Bloco

    original = RAIZ / "docs" / "contrato" / "README.md"
    bloco = Bloco(nome="b", titulo="T", resumo="R", padroes=["*"], arquivos=[original])

    saida = montar_markdown(bloco, com_testes=False)
    corpo = original.read_text(encoding="utf-8").rstrip("\n")

    # A cerca externa precisou crescer, e o conteudo esta la inteiro.
    assert "````markdown" in saida
    assert corpo in saida
    # E o que vem depois da cerca externa e o proximo cabecalho, nao codigo solto.
    assert saida.rstrip().endswith("````")


# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------


def test_config_recusa_bloco_relacionado_inexistente(tmp_path):
    """Erro de digitacao em `relacionados` manda a pessoa procurar um bloco que
    nao ha."""
    caminho = tmp_path / "blocos.toml"
    caminho.write_text(
        '[[bloco]]\nnome = "a"\npadroes = ["*"]\nrelacionados = ["nao-existe"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfiguracaoInvalida, match="nao-existe"):
        carregar_config(caminho)


def test_config_recusa_nome_repetido(tmp_path):
    caminho = tmp_path / "blocos.toml"
    caminho.write_text(
        '[[bloco]]\nnome = "a"\npadroes = ["*"]\n\n[[bloco]]\nnome = "a"\npadroes = ["*"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfiguracaoInvalida, match="duas vezes"):
        carregar_config(caminho)


def _rodar(*argumentos: str) -> subprocess.CompletedProcess:
    """Chama o script como a pessoa chamaria, pelo terminal.

    Como subprocesso e nao importando `main()`: o que se quer verificar aqui e o
    codigo de saida, que e o que um CI ou um pre-commit vai olhar.
    """
    return subprocess.run(  # noqa: S603 - o alvo e um script do proprio repositorio
        [sys.executable, str(RAIZ / "scripts" / "gerar_blocos.py"), *argumentos],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )


def test_o_comando_conferir_sai_com_zero_no_estado_atual():
    """O modo feito para rodar em CI. Com a configuracao real tudo cabe — e e
    esse o estado que o projeto se compromete a manter."""
    resultado = _rodar("--conferir")

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "Cobertura completa" in resultado.stdout


def test_gerar_escreve_um_md_por_bloco_pedido(tmp_path):
    """Gerar so um bloco escreve so ele — mas o indice cobre todos, senao
    esconderia o que existe."""
    resultado = _rodar("capas", "--saida", str(tmp_path))

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["README.md", "capas.md"]

    indice = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "publicacao.md" in indice


def test_bloco_inexistente_e_recusado_com_a_lista_do_que_existe(tmp_path):
    resultado = _rodar("capaz", "--saida", str(tmp_path))

    assert resultado.returncode == 2
    assert "capaz" in resultado.stderr
    assert "capas" in resultado.stderr
