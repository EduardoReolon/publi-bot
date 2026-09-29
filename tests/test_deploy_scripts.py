"""Os scripts de implantacao, exercitados fora de um servidor.

Eles nao tem como ser testados por inteiro aqui — nao ha systemd, nem sudo, nem
PostgreSQL de producao. O que se testa e a logica que erra em silencio:
sincronizar units so quando mudam, a ordem do release e o .env que chega do
secret.

Nada aqui toca o banco.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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
# alternativa — rodar o release inteiro — exigiria um servidor, e o que se
# quer impedir aqui e alguem trocar a ordem sem perceber.
RELEASE = RAIZ / "deploy" / "scripts" / "release.sh"


def _release() -> str:
    return RELEASE.read_text(encoding="utf-8")


def test_o_release_semeia_a_conexao_de_inferencia_sem_sobrescrever():
    """Com `--atualizar`, trocar de modelo pelo admin duraria ate a proxima
    implantacao."""
    assert "manage configurar_inferencia" in _release()
    assert "configurar_inferencia --atualizar" not in _release()


def test_o_release_instala_dependencias_antes_de_trocar_o_codigo():
    """Um requirements que nao instala precisa parar a implantacao com o codigo
    antigo inteiro no ar, e nao com codigo novo e pacote velho."""
    texto = _release()
    assert texto.index('pip" install -q -r "$ENVIO/requirements.txt"') < texto.index(
        "rsync -a --delete"
    )


def test_o_release_migra_antes_de_reiniciar_e_sincroniza_units_antes():
    texto = _release()
    assert texto.index("migrate_schemas") < texto.index("systemctl restart celery-publibot")
    assert texto.index("sincronizar-systemd.sh") < texto.index("systemctl reload publibot")


def test_o_rsync_nao_apaga_o_que_so_existe_no_servidor():
    """`--delete` tira do servidor o que saiu do git; venv, .env, midia e o
    modelo baixado nao estao no git e nao podem ir junto."""
    texto = _release()
    for protegido in ("/venv/", "/.env", "/media/", "/.model_cache/", "/staticfiles/"):
        assert f"--exclude '{protegido}'" in texto


@pytest.mark.parametrize(
    "script", ["release.sh", "sincronizar-systemd.sh", "backup.sh", "restore.sh"]
)
def test_scripts_param_no_primeiro_erro(script):
    """Sem `set -e`, um passo que falha no meio da implantacao e seguido pelos
    proximos — e o servico reinicia com migrations pela metade."""
    texto = (RAIZ / "deploy" / "scripts" / script).read_text(encoding="utf-8")

    assert "set -euo pipefail" in texto


@pytest.mark.parametrize("script", ["release.sh", "sincronizar-systemd.sh"])
def test_scripts_sao_executaveis(script):
    assert os.access(RAIZ / "deploy" / "scripts" / script, os.X_OK), (
        f"{script} sem bit de execucao: o git preserva o modo, e sem ele a "
        f"instrucao da documentacao nao funciona."
    )


def _prologo_em(envio: Path) -> Path:
    """Uma copia do release.sh ate a leitura do .env, dentro de uma pasta de envio.

    Recortado em vez de rodado inteiro porque o resto quer um servidor (apt,
    banco, systemd). A leitura do .env roda de verdade, e nao por texto.
    """
    linhas = _release().splitlines()
    fim = next(i for i, linha in enumerate(linhas) if linha.startswith("MIDIA="))
    copia = envio / "deploy" / "scripts" / "release.sh"
    copia.parent.mkdir(parents=True)
    copia.write_text(
        "\n".join(linhas[: fim + 1]) + '\necho "$DJANGO_SECRET_KEY|$ROOT_DOMAIN|$MIDIA"\n',
        encoding="utf-8",
    )
    return copia


def _rodar(copia: Path, raiz: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - o alvo e um script do proprio repositorio
        [shutil.which("bash") or "/bin/bash", str(copia)],
        capture_output=True,
        text=True,
        env={**os.environ, "PUBLIBOT_ROOT": str(raiz)},
        check=False,
    )


def test_o_release_instala_o_env_enviado_e_o_carrega(tmp_path):
    """O .env vem na pasta de envio (do secret) e vai para a raiz com modo 600,
    saindo da pasta de envio. Os `manage.py` do release rodam fora das units,
    entao o script precisa carrega-lo ele mesmo."""
    envio, raiz = tmp_path / "envio", tmp_path / "raiz"
    copia = _prologo_em(envio)
    (envio / ".env").write_text(
        "# um comentario\n"
        "DJANGO_SECRET_KEY=segredo-do-servidor\n"
        "ROOT_DOMAIN=publibot.com.br\n"
        "MEDIA_ROOT=/dados/midia\n"
        "isto nao e uma variavel\n",
        encoding="utf-8",
    )

    resultado = _rodar(copia, raiz)

    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "segredo-do-servidor|publibot.com.br|/dados/midia"
    assert (raiz / ".env").stat().st_mode & 0o777 == 0o600
    assert not (envio / ".env").exists()


def test_o_release_para_sem_env(tmp_path):
    """Seguir sem ele so adiaria a falha para o primeiro `manage.py`, com uma
    mensagem que nao menciona implantacao nem o arquivo que falta."""
    copia = _prologo_em(tmp_path / "envio")

    resultado = _rodar(copia, tmp_path / "raiz")

    assert resultado.returncode == 1
    assert "PRODUCTION_ENV_FILE" in resultado.stderr


def test_o_molde_do_nginx_so_tem_marcadores_que_o_release_troca():
    """Um marcador esquecido vira `server_name __DOMINIO__` no servidor, e o
    `nginx -t` nao reclama: o site so nao responde."""
    molde = (RAIZ / "deploy" / "nginx" / "publibot.conf").read_text(encoding="utf-8")
    marcadores = set(re.findall(r"__[A-Z]+__", molde))

    assert marcadores == {"__DOMINIO__", "__RAIZ__", "__MIDIA__"}
    for marcador in marcadores:
        assert f"s|{marcador}|" in _release()


def test_as_units_apontam_para_a_raiz_do_release():
    raiz = re.search(r'RAIZ="\$\{PUBLIBOT_ROOT:-([^}]+)\}"', _release()).group(1)
    for unit in (RAIZ / "deploy" / "systemd").glob("*.service"):
        texto = unit.read_text(encoding="utf-8")
        assert f"WorkingDirectory={raiz}" in texto, unit.name
        assert f"EnvironmentFile={raiz}/.env" in texto, unit.name


# ---------------------------------------------------------------------------
# Workflow do GitHub Actions
# ---------------------------------------------------------------------------
WORKFLOW = RAIZ / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_o_deploy_so_roda_em_push_na_main(workflow):
    """A condicao e a unica coisa entre um push numa branch de trabalho e o
    servidor de producao. Lida como dado, e nao como texto, porque um `if`
    sutilmente errado continua sendo YAML valido."""
    condicao = workflow["jobs"]["deploy"]["if"]

    assert "refs/heads/main" in condicao
    assert "github.event_name == 'push'" in condicao


def test_nenhuma_outra_branch_alcanca_o_servidor(workflow):
    """O teste acima passa com `main || claude-cli`: a substring continua la.

    Conferir a presenca de `main` diz que a main implanta; nao diz que so ela.
    """
    condicao = workflow["jobs"]["deploy"]["if"]

    assert "||" not in condicao, "um `ou` na condicao abre a implantacao para outra branch"
    assert condicao.count("refs/heads/") == 1


def test_o_deploy_espera_os_testes(workflow):
    assert workflow["jobs"]["deploy"]["needs"] == "testes"


def _passos(workflow) -> list[dict]:
    return workflow["jobs"]["deploy"]["steps"]


def test_o_deploy_chama_o_mesmo_script_que_se_roda_a_mao(workflow):
    """Descrever a implantacao duas vezes — uma no script, outra no YAML — cria
    dois caminhos que divergem no dia em que alguem corrige so um deles."""
    script = "\n".join(p.get("with", {}).get("script", "") for p in _passos(workflow))

    assert "deploy/scripts/release.sh" in script
    assert "migrate_schemas" not in script
    assert "collectstatic" not in script


def test_o_scp_so_esvazia_a_pasta_de_envio(workflow):
    """`rm: true` apaga o destino antes de copiar. Na pasta do servico levaria
    junto o venv, o .env e o modelo baixado."""
    copia = next(p for p in _passos(workflow) if "scp-action" in p.get("uses", ""))
    envio = workflow["jobs"]["deploy"]["env"]["PASTA_DE_ENVIO"]
    raiz = re.search(r'RAIZ="\$\{PUBLIBOT_ROOT:-([^}]+)\}"', _release()).group(1)

    assert copia["with"]["target"] == "${{ env.PASTA_DE_ENVIO }}"
    assert envio.rstrip("/") != raiz.rstrip("/")


def test_o_env_de_producao_nunca_entra_no_texto_de_um_script(workflow):
    """O secret vira arquivo por variavel de ambiente do passo. Colado no texto
    de um `run` ou do script remoto, um `$` ou uma aspa na senha o corromperia
    — e o valor apareceria no comando que o GitHub registra."""
    for passo in _passos(workflow):
        texto = passo.get("run", "") + passo.get("with", {}).get("script", "")
        assert "secrets.PRODUCTION_ENV_FILE" not in texto


def test_o_ci_sobe_postgres_com_pgvector(workflow):
    """Um banco sem a extensao passaria em quase tudo e nao diria nada sobre o
    que este projeto tem de mais fragil."""
    servicos = workflow["jobs"]["testes"]["services"]

    assert "pgvector" in servicos["postgres"]["image"]
    assert "redis" in servicos["redis"]["image"]


def test_o_ci_instala_as_extensoes_no_template1():
    """O banco de teste e criado em tempo de execucao pelo usuario da
    aplicacao, que nao e superusuario — e `vector` nao e uma extensao
    "trusted". Sem o `template1` preparado, ele nasceria sem ela."""
    texto = WORKFLOW.read_text(encoding="utf-8")

    assert "-d template1" in texto
    assert "WITH SCHEMA extensions" in texto


def test_o_molde_do_nginx_funciona_no_nginx_do_ubuntu_lts():
    """`http2 on;` e do Nginx 1.25+; o do Ubuntu 24.04 e o 1.24, e o nginx -t
    recusa a diretiva — o release.sh entao restaura a config antiga e para."""
    molde = (RAIZ / "deploy" / "nginx" / "publibot.conf").read_text(encoding="utf-8")
    assert "http2 on" not in molde.replace("`http2 on;`", "")


def test_o_gunicorn_nao_pre_carrega_a_aplicacao():
    """Com preload, o reload (HUP) sobe workers com o codigo ANTIGO: a
    implantacao termina bem e o site fica na versao anterior."""
    conf = (RAIZ / "deploy" / "gunicorn" / "gunicorn.conf.py").read_text(encoding="utf-8")
    assert "preload_app = False" in conf


def test_o_release_confere_a_versao_nova_e_reinicia_se_preciso():
    texto = _release()
    assert 'echo "$IMPLANTACAO" > "$RAIZ/.release"' in texto
    assert texto.index("systemctl reload publibot") < texto.index("esperar_versao 15")
    assert texto.index("esperar_versao 15") < texto.index("systemctl restart publibot.service")
