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
| Pedir a foto do autor so quando falta | `views.py::_precisa_da_foto` |
| Receber arquivo por multipart, com assinatura | `views.py::author_photos` |
| Guardar a foto pela referencia estavel do autor | `models.py::AuthorPhoto` |

## Duas armadilhas ao implementar a rota de fotos

**O limite de corpo do framework.** `DATA_UPLOAD_MAX_MEMORY_SIZE` no Django e
2,5 MB por padrao, e ler `request.body` — necessario para conferir a assinatura
— levanta `RequestDataTooBig` **antes** de a view rodar. Suba o valor se for
aceitar fotos maiores.

**A ordem de leitura.** Leia `request.body` ANTES de tocar em `request.POST` ou
`request.FILES`. O Django guarda o corpo bruto na primeira leitura e reusa; na
ordem inversa, `body` levanta `RawPostDataException` e a assinatura nao tem o
que conferir. O `conferir_assinatura` deste app ja faz isso na ordem certa.

## Testes

Os testes de contrato exercitam os DOIS lados — o cliente do PubliBot fala com
este app por HTTP real, com assinatura real:

```bash
pytest tests_contrato/ --ds=core.settings.test_contract
```

Testar so um lado deixaria passar a classe de defeito que mais importa aqui: os
dois lados calcularem a assinatura de forma diferente, ou discordarem sobre o
que conta como idempotencia.
