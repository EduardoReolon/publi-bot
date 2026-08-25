# ADR-0011 — Django 5.2 LTS

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

O scaffold veio com Django 6.1. Tres fatos verificados diretamente contra os
metadados dos pacotes:

1. **`django-celery-beat` 2.9.0 declara `Django<6.1,>=2.2`.** O pip recusa
   instala-lo junto com o Django 6.1. Sem ele nao existe `DatabaseScheduler`, e
   portanto nao existe cadencia por cliente configuravel sem deploy — a
   cadencia viraria `beat_schedule` estatico em codigo.
2. `django-tenants` 3.14.0 declara `django<6.2,>=5.2`: compativel com ambos.
3. Django 6.1 exige Python 3.12 ou superior; Django 5.2 aceita 3.10.

Horizontes de suporte:

| Versao | Fim do suporte estendido |
|---|---|
| Django 5.2 LTS | abril de 2028 |
| Django 6.0 | abril de 2027 |
| Django 6.1 | dezembro de 2027 |

## Decisao

**Django 5.2.17 (LTS).**

## Consequencias

- Custo de adotar: zero. Nenhuma migration tinha sido aplicada, nenhuma linha
  de codigo de dominio existia.
- A stack inteira foi instalada e verificada em conjunto: Django 5.2.17,
  django-tenants 3.14.0, django-celery-beat 2.9.0, tenant-schemas-celery 5.0.0,
  celery 5.6.3, redis 8.1.0, psycopg 3.3.4.
- Subir para a serie 6.x depois e barato; descobrir a incompatibilidade no meio
  da construcao do motor de cadencia nao seria.
- O README declarava "Python 3.10+". Com Django 5.2 isso passa a ser verdade
  (com 6.1 estava incorreto), mas o projeto adota **Python 3.12** como alvo,
  refletido em `requires-python` e no `target-version` do Ruff.
