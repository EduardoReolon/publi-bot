from celery import shared_task


@shared_task
def varrer_dados_do_acervo() -> dict:
    """Uma vez por dia: o que as fontes de todos os clientes citam de dados."""
    from apps.dados.acervo import varrer_todos

    return varrer_todos()
