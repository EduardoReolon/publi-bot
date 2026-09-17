"""Os scripts de implantacao, exercitados fora de um servidor.

Eles nao tem como ser testados por inteiro aqui — nao ha systemd, nem sudo, nem
PostgreSQL de producao. O que se testa e a logica que erra em silencio:
sincronizar units so quando mudam, e nunca sobrescrever o arquivo de segredos.

Nada aqui toca o banco.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SINCRONIZAR = RAIZ / "deploy" / "scripts" / "sincronizar-systemd.sh"


def _sincronizar(destino: Path) -> subprocess.CompletedProcess:
    """Roda o script com o systemd e o sudo neutralizados.

    `PUBLIBOT_SUDO` vazio faz os comandos rodarem direto; `systemctl` nao existe
    neste ambiente e a chamada falha — por isso o codigo de saida nao e
    verificado, so a saida de texto, que e onde a decisao aparece.
    """
    ambiente = {
        **os.environ,
        "PUBLIBOT_ROOT": str(RAIZ),
        "PUBLIBOT_SYSTEMD_DIR": str(destino),
        "PUBLIBOT_SUDO": "",
    }
    return subprocess.run(  # noqa: S603 - o alvo e um script do proprio repositorio
        [shutil.which("bash") or "/bin/bash", str(SINCRONIZAR)],
        capture_output=True,
        text=True,
        env=ambiente,
        check=False,
    )


def test_primeira_execucao_instala_todos_os_units(tmp_path):
    resultado = _sincronizar(tmp_path)

    instalados = sorted(p.name for p in tmp_path.iterdir())
    assert instalados == [
        "celery-beat-publibot.service",
        "celery-publibot.service",
        "publibot.service",
        "publibot.socket",
    ]
    assert "recarregando o systemd" in resultado.stdout


def test_segunda_execucao_nao_recarrega_o_systemd(tmp_path):
    """`daemon-reload` a cada implantacao e ruido: ele reavalia todas as units
    da maquina, inclusive as de outros projetos."""
    _sincronizar(tmp_path)

    resultado = _sincronizar(tmp_path)

    assert "units ja estao em dia" in resultado.stdout
    assert "recarregando" not in resultado.stdout
    assert resultado.returncode == 0


def test_unit_alterado_no_repositorio_e_reinstalado(tmp_path):
    """O sintoma sem isto e perverso: a mudanca esta no git, foi revisada, foi
    implantada, e o servico segue rodando a versao antiga."""
    _sincronizar(tmp_path)
    (tmp_path / "publibot.socket").write_text("versao antiga\n", encoding="utf-8")

    resultado = _sincronizar(tmp_path)

    assert "unit alterada: publibot.socket" in resultado.stdout
    original = (RAIZ / "deploy" / "systemd" / "publibot.socket").read_text(encoding="utf-8")
    assert (tmp_path / "publibot.socket").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Garantias lidas do texto dos scripts
# ---------------------------------------------------------------------------
# Sao asserts sobre o codigo-fonte, e nao sobre a execucao. Valem porque a
# alternativa — rodar o bootstrap de verdade — exigiria um servidor, e o que se
# quer impedir aqui e alguem remover a protecao sem perceber.


def test_o_bootstrap_nunca_sobrescreve_o_arquivo_de_segredos():
    """NODE_KEY_ENCRYPTION_KEY cifra as credenciais dos sites dos clientes.
    Gerar uma chave nova por cima torna todas irrecuperaveis, e o erro so
    aparece na proxima publicacao."""
    texto = (RAIZ / "deploy" / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    assert 'if [[ -f "$ARQUIVO_ENV" ]]; then' in texto
    assert "ja existe e foi preservado" in texto


def test_o_release_semeia_a_conexao_de_inferencia_sem_sobrescrever():
    """Com `--atualizar`, trocar de modelo pelo admin duraria ate a proxima
    implantacao."""
    texto = (RAIZ / "deploy" / "scripts" / "release.sh").read_text(encoding="utf-8")

    assert "manage.py configurar_inferencia" in texto
    assert "configurar_inferencia --atualizar" not in texto


def test_o_release_sincroniza_os_units_antes_de_reiniciar():
    """Reiniciar antes de copiar o unit novo aplicaria a configuracao antiga, e
    a seguinte so valeria na implantacao seguinte."""
    texto = (RAIZ / "deploy" / "scripts" / "release.sh").read_text(encoding="utf-8")

    assert texto.index("sincronizar-systemd.sh") < texto.index("systemctl reload")


@pytest.mark.parametrize(
    "script", ["bootstrap.sh", "release.sh", "sincronizar-systemd.sh", "backup.sh", "restore.sh"]
)
def test_scripts_param_no_primeiro_erro(script):
    """Sem `set -e`, um passo que falha no meio da implantacao e seguido pelos
    proximos — e o servico reinicia com migrations pela metade."""
    texto = (RAIZ / "deploy" / "scripts" / script).read_text(encoding="utf-8")

    assert "set -euo pipefail" in texto


@pytest.mark.parametrize("script", ["bootstrap.sh", "release.sh", "sincronizar-systemd.sh"])
def test_scripts_sao_executaveis(script):
    assert os.access(RAIZ / "deploy" / "scripts" / script, os.X_OK), (
        f"{script} sem bit de execucao: o git preserva o modo, e sem ele a "
        f"instrucao do README nao funciona."
    )
