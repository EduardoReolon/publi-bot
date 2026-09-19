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


RELEASE = RAIZ / "deploy" / "scripts" / "release.sh"


def _prologo_do_release() -> str:
    """O trecho do release.sh que carrega o ambiente, ate o `fi` que o fecha.

    Recortado em vez de rodado inteiro porque o resto do script quer um
    servidor: git, venv, systemd. O que se testa aqui e so a leitura do
    arquivo de ambiente — e ela roda de verdade, nao por leitura de texto.
    """
    linhas = RELEASE.read_text(encoding="utf-8").splitlines()
    fim = next(i for i, linha in enumerate(linhas) if linha == "fi")
    return "\n".join(linhas[: fim + 1])


def test_o_release_carrega_o_arquivo_de_ambiente(tmp_path):
    """As units do systemd leem `/etc/publibot/env` por `EnvironmentFile`, mas
    os `manage.py` do release rodam FORA delas.

    Sem carregar o arquivo, o primeiro comando morre em
    `ImproperlyConfigured: DJANGO_SECRET_KEY` — antes de qualquer migration, e
    com uma mensagem que nao menciona implantacao nenhuma.
    """
    arquivo = tmp_path / "env"
    arquivo.write_text(
        "# um comentario\n"
        "DJANGO_SECRET_KEY=segredo-do-servidor\n"
        "ROOT_DOMAIN=publibot.com.br\n"
        "isto nao e uma variavel\n",
        encoding="utf-8",
    )
    script = _prologo_do_release() + '\necho "$DJANGO_SECRET_KEY|$ROOT_DOMAIN"\n'

    resultado = subprocess.run(  # noqa: S603 - o alvo e um script do proprio repositorio
        [shutil.which("bash") or "/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PUBLIBOT_ROOT": str(tmp_path), "PUBLIBOT_ENV_FILE": str(arquivo)},
        check=False,
    )

    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "segredo-do-servidor|publibot.com.br"


def test_o_release_para_se_o_arquivo_de_ambiente_nao_existe(tmp_path):
    """Seguir sem ele so adiaria a falha para o primeiro `manage.py`, com uma
    mensagem que nao menciona implantacao nem o arquivo que falta."""
    resultado = subprocess.run(  # noqa: S603 - o alvo e um script do proprio repositorio
        [shutil.which("bash") or "/bin/bash", "-c", _prologo_do_release()],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PUBLIBOT_ROOT": str(tmp_path),
            "PUBLIBOT_ENV_FILE": str(tmp_path / "nao-existe"),
        },
        check=False,
    )

    assert resultado.returncode == 1
    assert "bootstrap.sh" in resultado.stderr


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


def test_o_deploy_espera_os_testes(workflow):
    assert workflow["jobs"]["deploy"]["needs"] == "testes"


def test_o_deploy_chama_o_mesmo_script_que_se_roda_a_mao(workflow):
    """Descrever a implantacao duas vezes — uma no script, outra no YAML — cria
    dois caminhos que divergem no dia em que alguem corrige so um deles."""
    passos = workflow["jobs"]["deploy"]["steps"]
    script = "\n".join(passo.get("with", {}).get("script", "") for passo in passos)

    assert "deploy/scripts/release.sh" in script
    # Nenhum passo da implantacao repetido aqui: quem migra e o release.sh.
    assert "migrate_schemas" not in script
    assert "collectstatic" not in script


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


def test_o_ci_nao_guarda_o_env_de_producao_num_segredo(workflow):
    """Reescrever `/etc/publibot/env` a cada implantacao trocaria a
    `NODE_KEY_ENCRYPTION_KEY` que cifrou as credenciais dos sites, e o erro so
    apareceria dias depois, contra o site do cliente.

    Os segredos nascem no servidor, no `bootstrap.sh`, e nunca sao
    sobrescritos — entao o job de implantacao nao carrega nenhum deles. A
    chave do job de TESTE nao conta: e descartavel e publica de proposito.
    """
    deploy = yaml.safe_dump(workflow["jobs"]["deploy"])

    assert "PRODUCTION_ENV_FILE" not in WORKFLOW.read_text(encoding="utf-8")
    assert "NODE_KEY_ENCRYPTION_KEY" not in deploy
    assert "DJANGO_SECRET_KEY" not in deploy


# ---------------------------------------------------------------------------
# Regra de sudo da implantacao
# ---------------------------------------------------------------------------
SUDOERS = RAIZ / "deploy" / "sudoers" / "publibot-deploy"


def test_a_regra_de_sudo_e_valida(tmp_path):
    """Um arquivo invalido em /etc/sudoers.d quebra o sudo da maquina inteira,
    para todos os usuarios, e o unico caminho de volta e um console fisico.

    O `visudo -c` do bootstrap protege o servidor; este teste protege quem
    edita o molde, que e onde o erro de fato nasce.
    """
    visudo = shutil.which("visudo")
    if visudo is None:
        pytest.skip("visudo nao existe neste ambiente")

    arquivo = tmp_path / "publibot-deploy"
    arquivo.write_text(
        SUDOERS.read_text(encoding="utf-8").replace("USUARIO ", "deployer "), encoding="utf-8"
    )

    resultado = subprocess.run(  # noqa: S603 - alvo e um arquivo do proprio repositorio
        [visudo, "-c", "-f", str(arquivo)], capture_output=True, text=True, check=False
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def test_a_regra_de_sudo_nao_da_a_maquina_inteira():
    """`NOPASSWD: ALL` resolveria o mesmo problema e daria ao segredo
    `DEPLOY_KEY`, guardado no GitHub, o poder de root sobre o servidor —
    inclusive sobre os outros projetos que dividem a maquina."""
    # So as linhas de regra: o comentario que EXPLICA por que `NOPASSWD: ALL`
    # esta errado tambem contem `NOPASSWD: ALL`, e faria o teste acusar
    # justamente o texto que o defende. Ja aconteceu com um hook do
    # pre-commit deste repositorio.
    regras = "\n".join(
        linha
        for linha in SUDOERS.read_text(encoding="utf-8").splitlines()
        if not linha.lstrip().startswith("#")
    )

    assert "NOPASSWD: ALL" not in regras
    # Cada comando permitido e nomeado, e nenhum deles e um shell.
    assert "/usr/bin/systemctl reload publibot.service" in regras
    for perigoso in ("/bin/bash", "/bin/sh", "/usr/bin/su "):
        assert perigoso not in regras


def test_o_bootstrap_confere_a_regra_antes_de_instalar():
    texto = (RAIZ / "deploy" / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    assert texto.index("visudo -c") < texto.index("/etc/sudoers.d/publibot-deploy")


# ---------------------------------------------------------------------------
# Instalador do worker de conversao
# ---------------------------------------------------------------------------
WORKER = RAIZ / "worker-gpu"
INSTALAR = WORKER / "deploy" / "instalar.sh"


# Um `.env` que passa por todas as conferencias, para os testes que querem
# chegar ALEM delas.
_ENV_COMPLETO = (
    "BIND_HOST=127.0.0.1\nBIND_PORT=8100\nIMAGEM_BIND_PORT=8101\nWORKER_SHARED_SECRET=abc\n"
)


def _worker_falso(tmp_path: Path, env: str) -> Path:
    """Uma arvore que parece a do worker, sem os 3 GB de torch."""
    (tmp_path / "deploy").mkdir()
    shutil.copy(INSTALAR, tmp_path / "deploy" / "instalar.sh")
    for unit in ("docling-api.service", "imagem-api.service"):
        shutil.copy(WORKER / "deploy" / unit, tmp_path / "deploy")
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    uvicorn = tmp_path / "venv" / "bin" / "uvicorn"
    uvicorn.touch()
    uvicorn.chmod(0o755)
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    return tmp_path


def _instalar(raiz: Path, *argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - o alvo e um script do proprio repositorio
        [shutil.which("bash") or "/bin/bash", str(raiz / "deploy" / "instalar.sh"), *argumentos],
        capture_output=True,
        text=True,
        check=False,
    )


def test_o_instalador_recusa_bind_em_todas_as_interfaces(tmp_path):
    """Um endpoint que aceita PDF e roda modelo, aberto na internet, deixa a
    placa disponivel para qualquer pessoa. Recusar aqui e mais barato que
    descobrir depois."""
    raiz = _worker_falso(tmp_path, "BIND_HOST=0.0.0.0\nWORKER_SHARED_SECRET=abc\n")

    resultado = _instalar(raiz)

    assert resultado.returncode == 1
    # O bandit ve o literal e nao o teste: esta linha EXIGE que o instalador
    # recuse esse endereco, por isso a dispensa ao lado. (Escrever "n-o-q-a"
    # no inicio deste comentario o transformaria numa diretiva de verdade,
    # que o ruff entao acusaria de nao suprimir nada.)
    assert "0.0.0.0" in resultado.stderr  # noqa: S104
    assert "Tailscale" in resultado.stderr


def test_o_instalador_recusa_segredo_vazio(tmp_path):
    """Sem segredo o servico sobe e responde 500 a TODA conversao, com uma
    mensagem que fala de configuracao sem dizer qual arquivo preencher."""
    raiz = _worker_falso(tmp_path, "BIND_HOST=127.0.0.1\nWORKER_SHARED_SECRET=\n")

    resultado = _instalar(raiz)

    assert resultado.returncode == 1
    assert "WORKER_SHARED_SECRET" in resultado.stderr
    # Diz de onde vem o par do outro lado, que e a parte que se esquece.
    assert "CONVERSAO_SEGREDO" in resultado.stderr


def test_o_instalador_recusa_sem_o_venv(tmp_path):
    (tmp_path / "deploy").mkdir()
    shutil.copy(INSTALAR, tmp_path / "deploy" / "instalar.sh")
    (tmp_path / ".env").write_text("BIND_HOST=127.0.0.1\nWORKER_SHARED_SECRET=abc\n")

    resultado = _instalar(tmp_path)

    assert resultado.returncode == 1
    assert "requirements.txt" in resultado.stderr


def test_a_unit_gerada_nao_deixa_marcador_para_tras(tmp_path):
    """O molde nao e arquivo pronto. Um marcador que sobrevivesse viraria um
    caminho literal chamado "RAIZ", e o systemd falharia ao carregar."""
    molde = (WORKER / "deploy" / "docling-api.service").read_text(encoding="utf-8")

    gerada = (
        molde.replace("RAIZ", str(tmp_path))
        .replace("LINHA_DE_USUARIO", "# (unit de usuario)")
        .replace("ALVO_DE_INSTALACAO", "default.target")
    )

    for marcador in ("RAIZ", "LINHA_DE_USUARIO", "ALVO_DE_INSTALACAO"):
        assert marcador not in gerada
    assert f"WorkingDirectory={tmp_path}" in gerada
    assert f"ExecStart={tmp_path}/venv/bin/uvicorn" in gerada


def test_a_unit_sobe_um_processo_so():
    """Duas conversoes simultaneas estouram a VRAM, e o resultado e 15x mais
    lento sem erro que denuncie."""
    molde = (WORKER / "deploy" / "docling-api.service").read_text(encoding="utf-8")

    assert "--workers 1" in molde
    assert "Restart=always" in molde


def test_o_requirements_do_worker_fixa_o_opencv():
    """O `cv2` nao vem sozinho em Python 3.14: quem o trazia era o `rapidocr`,
    que o Docling pede so para `python_version < "3.14"`. Sem este pin, a
    analise de tabela quebra com ModuleNotFoundError na primeira conversao."""
    texto = (WORKER / "requirements.txt").read_text(encoding="utf-8")

    assert "opencv-python-headless" in texto
    # A variante completa linka libGL, que uma maquina sem tela nao tem.
    assert "\nopencv-python>" not in texto


def test_o_instalador_recusa_um_endereco_que_nao_existe_nesta_maquina(tmp_path):
    """O caso real: `BIND_HOST=100.x.y.z`, o exemplo nunca substituido.

    Ele sobreviveu meses porque `manage.py dev` ignora o `BIND_HOST` — ele
    escuta no host da URL configurada no PubliBot. So a unit do systemd usa
    este valor, e ai o uvicorn morre no boot, o systemd o reinicia a cada 10s,
    e a unica pista fica no journal.
    """
    raiz = _worker_falso(tmp_path, "BIND_HOST=100.x.y.z\nWORKER_SHARED_SECRET=abc\n")

    resultado = _instalar(raiz)

    assert resultado.returncode == 1
    assert "BIND_HOST=100.x.y.z" in resultado.stderr
    assert "tailscale" in resultado.stderr.lower()


def test_o_instalador_recusa_um_ip_valido_que_nao_e_desta_maquina(tmp_path):
    """A Tailscale parada, ou um IP que mudou de lugar: o endereco e valido e
    mesmo assim nao da para escutar nele. Nao ha erro de digitacao a procurar,
    e por isso a mensagem manda olhar o `tailscale status`.

    `203.0.113.x` e a faixa reservada a documentacao (TEST-NET-3): nao existe
    em maquina nenhuma, aqui ou no CI.
    """
    raiz = _worker_falso(tmp_path, "BIND_HOST=203.0.113.1\nWORKER_SHARED_SECRET=abc\n")

    resultado = _instalar(raiz)

    assert resultado.returncode == 1
    assert "nao consegue escutar" in resultado.stderr


def test_o_instalador_aceita_o_loopback(tmp_path):
    """127.0.0.1 e o padrao do `.env.example` e precisa passar — senao o
    caminho normal de quem esta comecando nao existe.

    Para aqui, sem instalar unit nenhuma: `systemctl` no CI nao tem sessao de
    usuario. O que se afirma e que a conferencia de endereco nao barrou.
    """
    raiz = _worker_falso(tmp_path, _ENV_COMPLETO)

    resultado = _instalar(raiz)

    assert "nao consegue escutar" not in resultado.stderr


def test_o_exemplo_do_worker_traz_um_endereco_que_funciona(tmp_path):
    """Um valor de exemplo que nao funciona em lugar nenhum e uma armadilha
    plantada: quem copia o arquivo e segue o passo a passo cai nela."""
    import re

    texto = (WORKER / ".env.example").read_text(encoding="utf-8")
    valor = re.search(r"^BIND_HOST=(.*)$", texto, re.MULTILINE).group(1).strip()

    assert valor == "127.0.0.1"


def test_o_instalador_recusa_env_antigo_sem_a_porta_da_imagem(tmp_path):
    """O caminho de atualizacao: quem ja tinha o worker tem um `.env` anterior
    ao servico de imagem, sem `IMAGEM_BIND_PORT`.

    O systemd troca uma variavel ausente por string VAZIA, sem reclamar. O
    uvicorn recebe `--port ""`, morre no boot, e um `.env.example` novo nao
    conserta quem nao vai copia-lo de novo.
    """
    raiz = _worker_falso(tmp_path, "BIND_HOST=127.0.0.1\nBIND_PORT=8100\nWORKER_SHARED_SECRET=x\n")

    resultado = _instalar(raiz, "--imagem")

    assert resultado.returncode == 1
    assert "IMAGEM_BIND_PORT=8101" in resultado.stderr


def test_o_exemplo_do_worker_define_as_duas_portas(tmp_path):
    """Uma copia nova do exemplo tem de passar pela conferencia acima."""
    texto = (WORKER / ".env.example").read_text(encoding="utf-8")

    assert "\nBIND_PORT=" in texto
    assert "\nIMAGEM_BIND_PORT=" in texto


def test_o_instalador_recusa_argumento_desconhecido(tmp_path):
    """Um `--imagen` com erro de digitacao instalaria o Docling calado, e a
    pessoa concluiria que o servico de imagem esta de pe."""
    raiz = _worker_falso(tmp_path, _ENV_COMPLETO)

    resultado = _instalar(raiz, "--imagen")

    assert resultado.returncode == 1
    assert "--imagem" in resultado.stderr


def test_o_instalador_de_imagem_recusa_venv_sem_diffusers(tmp_path):
    """Quem instalou o worker antes deste servico existir tem o venv sem o
    `diffusers`. A unit subiria, morreria com ModuleNotFoundError e o systemd
    a reiniciaria a cada 10s — visivel so no journal."""
    raiz = _worker_falso(tmp_path, _ENV_COMPLETO)

    resultado = _instalar(raiz, "--imagem")

    assert resultado.returncode == 1
    assert "diffusers" in resultado.stderr
    assert "requirements.txt" in resultado.stderr


def test_as_duas_units_nao_disputam_a_mesma_porta(tmp_path):
    """Dois servicos na mesma maquina. Portas iguais dariam "address already
    in use" no segundo, e o systemd o reiniciaria para sempre."""
    docling = (WORKER / "deploy" / "docling-api.service").read_text(encoding="utf-8")
    imagem = (WORKER / "deploy" / "imagem-api.service").read_text(encoding="utf-8")

    assert "${BIND_PORT}" in docling
    assert "${IMAGEM_BIND_PORT}" in imagem
    assert "docling_api:app" in docling
    assert "imagem_api:app" in imagem


def test_a_unit_de_imagem_tambem_sobe_um_processo_so():
    """Dois processos significam dois modelos de difusao residentes."""
    molde = (WORKER / "deploy" / "imagem-api.service").read_text(encoding="utf-8")

    assert "--workers 1" in molde
    assert "Restart=always" in molde


def test_a_unit_de_imagem_nao_deixa_marcador_para_tras(tmp_path):
    molde = (WORKER / "deploy" / "imagem-api.service").read_text(encoding="utf-8")

    gerada = (
        molde.replace("RAIZ", str(tmp_path))
        .replace("LINHA_DE_USUARIO", "# (unit de usuario)")
        .replace("ALVO_DE_INSTALACAO", "default.target")
    )

    for marcador in ("RAIZ", "LINHA_DE_USUARIO", "ALVO_DE_INSTALACAO"):
        assert marcador not in gerada
    assert f"ExecStart={tmp_path}/venv/bin/uvicorn" in gerada


def test_o_requirements_do_worker_traz_o_accelerate():
    """Sem ele nao existe `enable_model_cpu_offload()`, e o pipeline inteiro
    vai para a VRAM — o dobro do pico, numa placa que ja esta apertada. O
    diffusers o declara como opcional; aqui ele nao e."""
    texto = (WORKER / "requirements.txt").read_text(encoding="utf-8")

    assert "diffusers" in texto
    assert "accelerate" in texto


def test_o_release_semeia_a_conexao_de_imagem_sem_derrubar_a_implantacao():
    """`--opcional`: uma instalacao sem gerador de imagem e o caso comum, e
    nao pode fazer o deploy falhar."""
    texto = (RAIZ / "deploy" / "scripts" / "release.sh").read_text(encoding="utf-8")

    assert "configurar_imagem --opcional" in texto
