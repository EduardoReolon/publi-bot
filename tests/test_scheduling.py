"""Testes do motor de cadencia e das sondas de saude."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.content.models import Answer, Article, Question
from apps.integrations.models import PublicationSchedule, PublicationSlot, Site
from apps.integrations.scheduling import (
    artigos_publicados_no_mes,
    contar_reserva,
    conteudo_pronto_para_publicar,
    excedeu_o_teto_mensal,
    garantir_horarios_livres,
    gerar_horarios,
    horarios_livres,
    proximo_horario,
    reserva_esta_baixa,
)


@pytest.fixture
def tenant_cadencia(tenant_factory):
    tenant = tenant_factory("cadencia")
    with schema_context(tenant.schema_name):
        yield tenant


@pytest.fixture
def site(tenant_cadencia):
    return Site.objects.create(
        name="Site",
        slug="site",
        base_url="https://exemplo.com.br",
        site_timezone="America/Sao_Paulo",
        max_articles_per_month=8,
    )


def _artigo(**extra) -> Article:
    padroes = {"title": "T", "author_name": "Ana"}
    return Article.objects.create(**{**padroes, **extra})


# ---------------------------------------------------------------------------
# A regra do `<=` — a mais importante deste modulo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_artigo_atrasado_e_publicado_e_nao_pulado(site):
    """Com comparacao por igualdade de minuto, qualquer atraso do agendador —
    uma implantacao, um pico de carga — faria o horario ser pulado e o conteudo
    nunca sair, sem erro em lugar nenhum."""
    atrasado = _artigo(
        status=Article.Status.APPROVED_SCHEDULED,
        scheduled_for=timezone.now() - timedelta(days=3),
    )

    selecionados = conteudo_pronto_para_publicar()
    assert ("article", str(atrasado.pk)) in selecionados


@pytest.mark.django_db
def test_artigo_do_futuro_nao_e_publicado_antes_da_hora(site):
    _artigo(
        status=Article.Status.APPROVED_SCHEDULED,
        scheduled_for=timezone.now() + timedelta(hours=2),
    )
    assert conteudo_pronto_para_publicar() == []


@pytest.mark.django_db
def test_artigo_nao_aprovado_nao_entra_na_selecao(site):
    _artigo(
        status=Article.Status.PENDING_REVIEW,
        scheduled_for=timezone.now() - timedelta(hours=1),
    )
    assert conteudo_pronto_para_publicar() == []


@pytest.mark.django_db
def test_resposta_agendada_tambem_e_selecionada(site):
    pergunta = Question.objects.create(
        site=site, remote_id="1", question_text="duvida", submitted_at=timezone.now()
    )
    resposta = Answer.objects.create(
        question=pergunta,
        status=Answer.Status.APPROVED_SCHEDULED,
        scheduled_for=timezone.now() - timedelta(minutes=5),
    )

    selecionados = conteudo_pronto_para_publicar()
    assert ("answer", str(resposta.pk)) in selecionados


# ---------------------------------------------------------------------------
# Geracao de horarios
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horarios_semanais_sao_criados_no_fuso_do_site(site):
    """Calculado no fuso do site, gravado em UTC. Guardar e comparar em UTC
    evita a classe inteira de erro das trocas de horario de verao."""
    schedule = PublicationSchedule.objects.create(
        site=site,
        mode=PublicationSchedule.Mode.WEEKLY_SLOTS,
        weekdays=[1, 3],
        times_of_day=["10:00"],
    )

    criados = gerar_horarios(schedule, ate=timezone.now() + timedelta(days=14))
    assert len(criados) >= 3

    for horario in criados:
        assert horario.slot_at.tzinfo is not None
        assert horario.local_slot_at is not None
        # Sao Paulo fica atras de UTC, entao 10h local sai depois das 10h UTC.
        assert horario.local_slot_at.hour == 10


@pytest.mark.django_db
def test_com_os_30_dias_ocupados_gerar_vai_alem_ate_ter_horarios_livres(site):
    """Todos os horarios dos proximos 30 dias com artigo aprovado: gerar "ate
    30 dias" nao criava nada ("0 horarios gerados") e o aprovado nao tinha onde
    entrar."""
    schedule = PublicationSchedule.objects.create(site=site, weekdays=[0], times_of_day=["10:00"])
    for horario in gerar_horarios(schedule):
        horario.article = _artigo(
            status=Article.Status.APPROVED_SCHEDULED, scheduled_for=horario.slot_at
        )
        horario.save()
    assert horarios_livres().count() == 0

    criados = garantir_horarios_livres(schedule)

    assert len(criados) >= 5
    assert horarios_livres().count() >= 5
    assert garantir_horarios_livres(schedule) == []  # ja tem livres: nada a fazer


@pytest.mark.django_db
def test_intervalo_em_dias_tambem_vai_alem_dos_30_dias(site):
    schedule = PublicationSchedule.objects.create(
        site=site,
        mode=PublicationSchedule.Mode.INTERVAL_DAYS,
        interval_days=10,
        times_of_day=["09:00"],
    )
    garantir_horarios_livres(schedule)
    assert horarios_livres().count() >= 5


@pytest.mark.django_db
def test_proximo_horario_sem_livre_gera_mais_e_nunca_e_agora(site):
    schedule = PublicationSchedule.objects.create(site=site, weekdays=[2], times_of_day=["10:00"])
    for horario in gerar_horarios(schedule):
        horario.article = _artigo(
            status=Article.Status.APPROVED_SCHEDULED, scheduled_for=horario.slot_at
        )
        horario.save()

    quando = proximo_horario()

    assert quando > timezone.now() + timedelta(days=25)
    assert quando == horarios_livres().first().slot_at


@pytest.mark.django_db
def test_proximo_horario_sem_cadencia_e_o_dia_seguinte_ao_ultimo_agendado(site):
    """Sem cadencia, agendar ainda agenda (no dia seguinte, na hora padrao do
    site); publicar na hora e so o botao "Publicar ja"."""
    quando = proximo_horario()
    assert quando > timezone.now() + timedelta(hours=1)

    _artigo(
        status=Article.Status.APPROVED_SCHEDULED,
        scheduled_for=timezone.now() + timedelta(days=5),
    )
    depois = proximo_horario()
    assert depois > timezone.now() + timedelta(days=5)
    assert proximo_horario() == depois


@pytest.mark.django_db
def test_gerar_horarios_duas_vezes_nao_duplica(site):
    schedule = PublicationSchedule.objects.create(
        site=site,
        weekdays=[0, 2, 4],
        times_of_day=["09:00"],
    )
    gerar_horarios(schedule, ate=timezone.now() + timedelta(days=10))
    total = PublicationSlot.objects.count()

    gerar_horarios(schedule, ate=timezone.now() + timedelta(days=10))
    assert PublicationSlot.objects.count() == total


@pytest.mark.django_db
def test_cadencia_inativa_nao_gera_horario(site):
    schedule = PublicationSchedule.objects.create(
        site=site, weekdays=[0], times_of_day=["09:00"], is_active=False
    )
    assert gerar_horarios(schedule) == []


@pytest.mark.django_db
def test_horario_invalido_nao_derruba_a_geracao(site):
    """Um horario malformado na configuracao nao pode impedir os demais de
    serem criados."""
    schedule = PublicationSchedule.objects.create(
        site=site,
        weekdays=[0, 1, 2, 3, 4],
        times_of_day=["nao-e-hora", "10:00"],
    )
    criados = gerar_horarios(schedule, ate=timezone.now() + timedelta(days=7))
    assert criados, "os horarios validos deveriam ter sido criados"


@pytest.mark.django_db
def test_fuso_desconhecido_cai_para_utc_sem_quebrar(site):
    site.site_timezone = "Nao/Existe"
    site.save()
    schedule = PublicationSchedule.objects.create(
        site=site, weekdays=[0, 1, 2, 3, 4, 5, 6], times_of_day=["10:00"]
    )
    assert gerar_horarios(schedule, ate=timezone.now() + timedelta(days=3))


@pytest.mark.django_db
def test_um_horario_por_site_por_instante(site):
    """Restricao no banco: dois processos nao podem preencher o mesmo horario."""
    from django.db.utils import IntegrityError

    momento = timezone.now() + timedelta(days=1)
    PublicationSlot.objects.create(site=site, slot_at=momento)

    with pytest.raises(IntegrityError):
        PublicationSlot.objects.create(site=site, slot_at=momento)


# ---------------------------------------------------------------------------
# Reserva de conteudo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reserva_baixa_e_detectada(site):
    PublicationSchedule.objects.create(site=site, buffer_threshold=2)
    _artigo(status=Article.Status.APPROVED_SCHEDULED, scheduled_for=timezone.now())

    assert contar_reserva(site) == 1
    assert reserva_esta_baixa(site) is True


@pytest.mark.django_db
def test_reserva_suficiente_nao_alerta(site):
    PublicationSchedule.objects.create(site=site, buffer_threshold=2)
    for _ in range(3):
        _artigo(status=Article.Status.APPROVED_SCHEDULED, scheduled_for=timezone.now())

    assert reserva_esta_baixa(site) is False


@pytest.mark.django_db
def test_resposta_conta_na_reserva_quando_ocupa_horario(site):
    """Se a resposta nao ocupasse horario, o site publicaria mais do que o
    configurado e o calculo da reserva ficaria errado."""
    PublicationSchedule.objects.create(site=site, buffer_threshold=2, qa_consumes_slot=True)
    pergunta = Question.objects.create(
        site=site, remote_id="1", question_text="d", submitted_at=timezone.now()
    )
    Answer.objects.create(question=pergunta, status=Answer.Status.APPROVED_SCHEDULED)

    assert contar_reserva(site) == 1


@pytest.mark.django_db
def test_resposta_nao_conta_quando_configurado_assim(site):
    PublicationSchedule.objects.create(site=site, buffer_threshold=2, qa_consumes_slot=False)
    pergunta = Question.objects.create(
        site=site, remote_id="1", question_text="d", submitted_at=timezone.now()
    )
    Answer.objects.create(question=pergunta, status=Answer.Status.APPROVED_SCHEDULED)

    assert contar_reserva(site) == 0


@pytest.mark.django_db
def test_site_sem_cadencia_nao_alerta(site):
    assert reserva_esta_baixa(site) is False


# ---------------------------------------------------------------------------
# Teto de volume
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_teto_mensal_e_respeitado(site):
    """Volume alto e previsivel e o padrao que buscadores tratam como producao
    em escala para manipular resultado."""
    site.max_articles_per_month = 3
    site.save()

    for _ in range(3):
        _artigo(status=Article.Status.PUBLISHED, published_at=timezone.now())

    assert artigos_publicados_no_mes(site) == 3
    assert excedeu_o_teto_mensal(site) is True


@pytest.mark.django_db
def test_artigo_de_outro_mes_nao_conta(site):
    _artigo(
        status=Article.Status.PUBLISHED,
        published_at=timezone.now() - timedelta(days=70),
    )
    assert artigos_publicados_no_mes(site) == 0


# ---------------------------------------------------------------------------
# Perguntas: identificacao e retencao
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pergunta_nao_duplica_por_site(site):
    from django.db.utils import IntegrityError

    Question.objects.create(
        site=site, remote_id="42", question_text="d", submitted_at=timezone.now()
    )
    with pytest.raises(IntegrityError):
        Question.objects.create(
            site=site, remote_id="42", question_text="d", submitted_at=timezone.now()
        )


@pytest.mark.django_db
def test_expurgo_apaga_texto_mas_preserva_a_linha(site):
    """Apagar a linha faria a proxima coleta reimportar a mesma pergunta como
    se fosse nova."""
    from apps.integrations.tasks import purge_expired_questions

    pergunta = Question.objects.create(
        site=site,
        remote_id="1",
        question_text="informacao pessoal",
        author_pseudonym="Joao",
        submitted_at=timezone.now(),
        retention_until=timezone.now() - timedelta(days=1),
    )

    assert purge_expired_questions() == 1

    pergunta.refresh_from_db()
    assert pergunta.question_text == ""
    assert pergunta.author_pseudonym == ""
    assert pergunta.purged_at is not None
    assert Question.objects.filter(pk=pergunta.pk).exists()


@pytest.mark.django_db
def test_expurgo_nao_repete(site):
    from apps.integrations.tasks import purge_expired_questions

    Question.objects.create(
        site=site,
        remote_id="1",
        question_text="x",
        submitted_at=timezone.now(),
        retention_until=timezone.now() - timedelta(days=1),
    )
    assert purge_expired_questions() == 1
    assert purge_expired_questions() == 0


@pytest.mark.django_db
def test_pergunta_dentro_do_prazo_nao_e_expurgada(site):
    from apps.integrations.tasks import purge_expired_questions

    Question.objects.create(
        site=site,
        remote_id="1",
        question_text="ainda vale",
        submitted_at=timezone.now(),
        retention_until=timezone.now() + timedelta(days=30),
    )
    assert purge_expired_questions() == 0


# ---------------------------------------------------------------------------
# Sondas de saude
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_healthz_nao_depende_de_servico_externo(client):
    """Se dependesse do banco, uma indisponibilidade momentanea faria o
    orquestrador matar e recriar conteineres saudaveis — transformando um
    problema em dois."""
    from django.conf import settings

    resposta = client.get("/healthz/", HTTP_HOST=settings.ROOT_DOMAIN)
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ok"
    assert "release" in resposta.json()


@pytest.mark.django_db
def test_readyz_verifica_dependencias(client, public_tenant):
    from django.conf import settings

    resposta = client.get("/readyz/", HTTP_HOST=settings.ROOT_DOMAIN)
    dados = resposta.json()

    assert "postgres" in dados["checks"]
    assert dados["checks"]["postgres"] == "ok"


@pytest.mark.django_db
def test_request_id_volta_no_cabecalho(client):
    from django.conf import settings

    resposta = client.get("/healthz/", HTTP_HOST=settings.ROOT_DOMAIN)
    assert resposta["X-Request-ID"]


@pytest.mark.django_db
def test_request_id_do_proxy_e_reaproveitado(client):
    """O mesmo identificador precisa atravessar Nginx, aplicacao e worker."""
    from django.conf import settings

    resposta = client.get(
        "/healthz/", HTTP_HOST=settings.ROOT_DOMAIN, HTTP_X_REQUEST_ID="vindo-do-nginx"
    )
    assert resposta["X-Request-ID"] == "vindo-do-nginx"


@pytest.mark.django_db
def test_sonda_responde_mesmo_com_host_desconhecido(client):
    """O balanceador e o orquestrador consultam por IP ou nome interno, que
    nunca corresponde ao dominio de um cliente. Depois da resolucao de tenant,
    as sondas devolveriam 404 e a infraestrutura concluiria que a aplicacao
    esta morta — reiniciando conteineres saudaveis."""
    resposta = client.get("/healthz/", HTTP_HOST="10.0.0.7")
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ok"


def test_ritmo_e_calculado_dos_dias_e_horarios():
    semanal = PublicationSchedule(
        mode=PublicationSchedule.Mode.WEEKLY_SLOTS, weekdays=[0, 3], times_of_day=["09:00"]
    )
    assert semanal.ritmo == "2 publicacao(oes) por semana (cerca de 1 a cada 3,5 dia(s))"
    por_intervalo = PublicationSchedule(
        mode=PublicationSchedule.Mode.INTERVAL_DAYS, interval_days=10
    )
    assert por_intervalo.ritmo == "1 publicacao a cada 10 dia(s)"
    assert PublicationSchedule(mode=PublicationSchedule.Mode.WEEKLY_SLOTS).ritmo == ""
