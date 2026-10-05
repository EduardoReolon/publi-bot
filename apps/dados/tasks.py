from celery import shared_task


@shared_task
def varrer_dados_do_acervo() -> dict:
    """Uma vez por dia: o que as fontes de todos os clientes citam de dados, e
    (so na primeira vez de cada instituicao) as series iniciais dos nichos."""
    from apps.dados.acervo import varrer_todos
    from apps.dados.catalogo import explorar_nichos_iniciais

    resultado = varrer_todos()
    resultado["series_iniciais"] = explorar_nichos_iniciais()
    return resultado
