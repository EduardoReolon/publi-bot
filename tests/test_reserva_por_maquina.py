"""Duas conexoes, uma placa so.

O Ollama em `:11434` e o Docling em `:8100` sao duas linhas no banco e o mesmo
hardware. Cada uma com `max_concurrency=1`, a contagem por conexao permitiria
duas execucoes simultaneas ali — uma gerando texto, outra convertendo PDF.

Numa placa de 8 GB isso estoura a VRAM, e o que acontece entao nao e um erro: o
processo cai para CPU em silencio. O trabalho termina, dezenas de vezes mais
lento, sem log, sem excecao e sem nada no painel. O sintoma e alguem dizer que
"hoje esta lento" — e essa e a unica pista.

Por isso a reserva conta por MAQUINA.
"""

from __future__ import annotations

import pytest

from apps.inference.leases import (
    SemCapacidade,
    adquirir,
    liberar,
    vizinhas_de_hardware,
)
from apps.inference.models import InferenceConnection

pytestmark = pytest.mark.django_db

TAILSCALE = "100.64.0.9"


def _conexao(nome: str, url: str, *, kind=None, concorrencia: int = 1) -> InferenceConnection:
    return InferenceConnection.objects.create(
        name=nome,
        kind=kind or InferenceConnection.Kind.OPENAI_COMPATIBLE,
        base_url=url,
        workloads=[InferenceConnection.Workload.TEXT],
        max_concurrency=concorrencia,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# O agrupamento
# ---------------------------------------------------------------------------
def test_o_host_e_derivado_da_url():
    """Derivado e nao configurado a parte: uma segunda fonte de verdade so
    acrescentaria uma forma de errar — mudar o endereco e esquecer o
    agrupamento devolveria o problema, sem sinal."""
    conexao = _conexao("Ollama", f"http://{TAILSCALE}:11434")

    assert conexao.maquina == TAILSCALE


def test_portas_diferentes_no_mesmo_host_sao_a_mesma_maquina():
    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    docling = _conexao("Docling", f"http://{TAILSCALE}:8100", kind=InferenceConnection.Kind.DOCLING)

    vizinhas = vizinhas_de_hardware(ollama)

    assert {c.pk for c in vizinhas} == {ollama.pk, docling.pk}


def test_hosts_diferentes_nao_se_misturam():
    """Um worker de conversao numa maquina e o Ollama noutra nao disputam nada,
    e serializa-los cortaria a vazao pela metade sem motivo."""
    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    _conexao("Docling", "http://100.64.0.77:8100", kind=InferenceConnection.Kind.DOCLING)

    assert [c.pk for c in vizinhas_de_hardware(ollama)] == [ollama.pk]


def test_conexao_inativa_nao_ocupa_a_maquina():
    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    desligada = _conexao(
        "Docling", f"http://{TAILSCALE}:8100", kind=InferenceConnection.Kind.DOCLING
    )
    InferenceConnection.objects.filter(pk=desligada.pk).update(is_active=False)

    assert [c.pk for c in vizinhas_de_hardware(ollama)] == [ollama.pk]


# ---------------------------------------------------------------------------
# A reserva
# ---------------------------------------------------------------------------
def test_converter_e_gerar_texto_nao_acontecem_juntos():
    """O teste que da nome ao arquivo.

    Antes disto os dois passavam: cada um olhava so a propria conexao e
    concluia que havia vaga.
    """
    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    docling = _conexao("Docling", f"http://{TAILSCALE}:8100", kind=InferenceConnection.Kind.DOCLING)

    lease = adquirir(ollama, owner_key="texto")

    with pytest.raises(SemCapacidade, match="maquina"):
        adquirir(docling, owner_key="conversao")

    # Solta a primeira e a segunda passa: e ocupacao, nao bloqueio permanente.
    liberar(lease)
    assert adquirir(docling, owner_key="conversao") is not None


def test_a_mensagem_nomeia_quem_divide_a_maquina():
    """ "Sem capacidade" numa conexao que esta parada e desnorteante. A causa e
    a outra conexao, e o texto precisa dize-lo."""
    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    docling = _conexao("Docling", f"http://{TAILSCALE}:8100", kind=InferenceConnection.Kind.DOCLING)

    adquirir(ollama, owner_key="texto")

    with pytest.raises(SemCapacidade) as capturado:
        adquirir(docling, owner_key="conversao")

    assert "Ollama" in str(capturado.value)


def test_a_capacidade_e_a_menor_declarada_e_nao_a_soma():
    """Somar diria que duas conexoes de 1 aguentam 2 — a conclusao exata que o
    comentario de `max_concurrency` desaconselha. A mais conservadora e a que
    sabe de alguma restricao que a outra nao sabe."""
    folgada = _conexao("Folgada", f"http://{TAILSCALE}:11434", concorrencia=4)
    apertada = _conexao(
        "Apertada",
        f"http://{TAILSCALE}:8100",
        kind=InferenceConnection.Kind.DOCLING,
        concorrencia=1,
    )

    adquirir(folgada, owner_key="primeira")

    with pytest.raises(SemCapacidade):
        adquirir(apertada, owner_key="segunda")


def test_maquinas_diferentes_rodam_em_paralelo():
    """A serializacao existe por causa da placa compartilhada. Onde nao ha
    placa compartilhada, ela seria so desperdicio."""
    aqui = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    la = _conexao("Docling", "http://100.64.0.77:8100", kind=InferenceConnection.Kind.DOCLING)

    adquirir(aqui, owner_key="texto")

    assert adquirir(la, owner_key="conversao") is not None


def test_a_conversao_reserva_de_fato(tenant_factory, monkeypatch):
    """Nao basta a reserva existir: `extrair_markdown` precisa passar por ela.

    Ate aqui a conversao nao reservava nada — ela so fazia o POST e contava com
    o 503 do worker, que protege contra duas conversoes e nao contra uma
    conversao ao lado de uma geracao de texto.
    """
    import hashlib

    import httpx
    from django.core.files.base import ContentFile
    from django_tenants.utils import schema_context

    from apps.inference.security import guardar_chave
    from apps.knowledge.extraction import ConversorOcupado, extrair_markdown
    from apps.knowledge.models import Document, DocumentCategory

    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    docling = _conexao("Docling", f"http://{TAILSCALE}:8100", kind=InferenceConnection.Kind.DOCLING)
    guardar_chave(docling, "segredo")
    docling.save()

    # O Ollama esta gerando texto agora.
    adquirir(ollama, owner_key="texto")

    tenant = tenant_factory("reserva")
    with schema_context(tenant.schema_name):
        DocumentCategory.objects.create(name="Artigo", slug="artigo")
        bruto = b"%PDF-1.4 conteudo qualquer"
        documento = Document.objects.create(
            category=DocumentCategory.objects.first(),
            original_file=ContentFile(bruto, name="estudo.pdf"),
            file_sha256=hashlib.sha256(bruto).hexdigest(),
            file_size_bytes=len(bruto),
            status=Document.Status.UPLOADED,
        )

        def nao_deveria_postar(*a, **k):
            raise AssertionError("a conversao nao pode chegar ao worker sem reserva")

        monkeypatch.setattr(httpx, "post", nao_deveria_postar)

        # Adiamento, nao falha: a maquina esta ocupada e isso nao gasta
        # tentativa (o fluxo trata `ConversorOcupado` como `PassoAdiado`).
        with pytest.raises(ConversorOcupado, match="ocupada"):
            extrair_markdown(documento)


# ---------------------------------------------------------------------------
# A escolha e a reserva precisam contar igual
# ---------------------------------------------------------------------------
def test_a_escolha_nao_entrega_conexao_que_a_reserva_vai_recusar():
    """As duas contas tem de ser a mesma.

    Enquanto `escolher_conexao` contava por conexao e `adquirir` por maquina, a
    escolha devolvia uma conexao livre cuja MAQUINA estava ocupada — e o
    trabalho fazia a viagem inteira para levar `SemCapacidade` e ser adiado.
    """
    from apps.inference.leases import escolher_conexao

    ollama = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    _conexao("Docling", f"http://{TAILSCALE}:8100", kind=InferenceConnection.Kind.DOCLING)

    # O Docling esta convertendo: a maquina inteira esta ocupada.
    adquirir(InferenceConnection.objects.get(name="Docling"), owner_key="conversao")

    assert escolher_conexao(workload=InferenceConnection.Workload.TEXT) is None
    assert ollama.max_concurrency == 1  # a conexao em si estaria "livre"


def test_a_mensagem_de_ocupado_diz_quem_esta_segurando():
    """ "Todas as conexoes estao ocupadas" e verdadeiro e inutil.

    Um processo que morreu com a reserva na mao produz a mesma frase de uma
    inferencia saudavel em curso — e a diferenca entre as duas e a diferenca
    entre esperar e agir.
    """
    from apps.inference.leases import descrever_ocupacao

    _conexao("Ollama", f"http://{TAILSCALE}:11434")
    adquirir(InferenceConnection.objects.get(name="Ollama"), owner_key="texto")

    frase = descrever_ocupacao()

    assert "Ollama" in frase
    assert "min" in frase
    assert "reservas --liberar" in frase


def test_sem_reserva_ativa_a_mensagem_aponta_o_disjuntor(tenant_factory):
    """Sem vaga e sem reserva, sobrou o circuito aberto. Mandar a pessoa
    procurar reserva ali seria manda-la para o lugar errado."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.inference.leases import descrever_ocupacao

    conexao = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    InferenceConnection.objects.filter(pk=conexao.pk).update(
        consecutive_failures=5,
        circuit_open_until=timezone.now() + timedelta(minutes=12),
    )

    frase = descrever_ocupacao()

    assert "disjuntor" in frase
    assert "Ollama" in frase
    assert "5 falhas" in frase
    assert "configurar_inferencia --atualizar" in frase


def test_a_mensagem_do_disjuntor_carrega_a_causa(tenant_factory):
    """ "Disjuntor aberto apos falhas seguidas" descreve o MECANISMO, que quem
    le ja deduziu da propria frase. Falta a causa — conexao recusada, modelo
    inexistente, chave invalida sao tres problemas com tres consertos, e a
    resposta ja estava gravada em `InferenceLog.error`."""
    from datetime import timedelta

    from django.utils import timezone
    from django_tenants.utils import schema_context

    from apps.inference.leases import descrever_ocupacao
    from apps.ops.models import InferenceLog

    conexao = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    InferenceConnection.objects.filter(pk=conexao.pk).update(
        consecutive_failures=5,
        circuit_open_until=timezone.now() + timedelta(minutes=12),
    )

    tenant = tenant_factory("com_log")
    with schema_context(tenant.schema_name):
        InferenceLog.objects.create(
            connection=conexao,
            model_name="qwen2.5:7b-instruct",
            workload="text",
            succeeded=False,
            error="model 'qwen2.5:7b-instruct' not found, try pulling it first",
        )

        frase = descrever_ocupacao()

    assert "not found" in frase


def test_sem_log_no_schema_a_mensagem_nao_quebra(tenant_factory):
    """`InferenceLog` vive no schema do tenant e esta funcao pode ser chamada
    de fora dele. Nao achar o registro nao pode virar excecao: sem o detalhe,
    o resto da frase ainda ajuda."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.inference.leases import descrever_ocupacao

    conexao = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    InferenceConnection.objects.filter(pk=conexao.pk).update(
        consecutive_failures=7,
        circuit_open_until=timezone.now() + timedelta(minutes=3),
    )

    assert "7 falhas" in descrever_ocupacao()


def test_reservas_lista_e_libera_as_presas(capsys):
    """Sem este comando, a unica saida para uma reserva presa era esperar uma
    hora ou abrir o banco na mao."""
    from datetime import timedelta

    from django.core.management import call_command
    from django.utils import timezone

    from apps.inference.models import InferenceLease

    conexao = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    lease = adquirir(conexao, owner_key="texto")
    # Envelhece a reserva: uma recem-criada quase sempre e trabalho de verdade.
    InferenceLease.objects.filter(pk=lease.pk).update(
        acquired_at=timezone.now() - timedelta(minutes=40)
    )

    call_command("reservas")
    assert "suspeita" in capsys.readouterr().out

    call_command("reservas", liberar=True)

    lease.refresh_from_db()
    assert lease.released_at is not None


def test_reservas_nao_solta_uma_inferencia_recem_comecada(capsys):
    """Soltar uma reserva viva poe duas tarefas na mesma placa — o problema
    que a reserva existe para evitar."""
    from django.core.management import call_command

    conexao = _conexao("Ollama", f"http://{TAILSCALE}:11434")
    lease = adquirir(conexao, owner_key="texto")

    call_command("reservas", liberar=True)

    lease.refresh_from_db()
    assert lease.released_at is None
    assert "--liberar --tudo" in capsys.readouterr().out
