# Implementacao de referencia — Django

App instalavel que implementa o contrato `/api/v1`.

**E um exemplo, nao um requisito.** O contrato e agnostico: qualquer linguagem
serve. Este codigo existe para mostrar como as regras normativas se traduzem em
codigo real, e para permitir testar o contrato de ponta a ponta.

## Instalacao

```python
# settings.py
INSTALLED_APPS = [..., "publibot_node"]

PUBLIBOT_API_KEY = env("PUBLIBOT_API_KEY")
PUBLIBOT_SIGNING_SECRET = env("PUBLIBOT_SIGNING_SECRET")

# Durante uma rotacao, o valor anterior continua aceito por 48 horas.
PUBLIBOT_API_KEY_PREVIOUS = env("PUBLIBOT_API_KEY_PREVIOUS", default="")
```

```python
# urls.py
urlpatterns = [..., path("api/v1/", include("publibot_node.urls"))]
```

```bash
python manage.py migrate publibot_node
```

## O que este codigo demonstra

| Regra | Onde |
|---|---|
| Assinatura sobre o corpo bruto | `auth.py::conferir_assinatura` |
| Comparacao em tempo constante | `auth.py`, via `hmac.compare_digest` |
| Janela de tempo e nonce usado uma vez | `auth.py::_conferir_frescor` |
| Mesma resposta para todo erro de credencial | `auth.py::RESPOSTA_DE_NEGACAO` |
| Idempotencia com indice unico | `models.py::ReceivedPublication` |
| Sanitizacao antes de gravar | `sanitize.py` |
| Limite de requisicoes | `throttle.py` |
