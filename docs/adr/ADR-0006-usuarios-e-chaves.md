# ADR-0006 — Modelo de usuario e chaves primarias

**Status:** Aceito
**Data:** 2026-08-25

## Contexto

Nao havia `AUTH_USER_MODEL` definido e nenhum `migrate` tinha sido aplicado —
a janela para trocar o modelo de usuario estava totalmente aberta. Depois do
primeiro migrate com `auth.User`, mudar exige cirurgia manual em
`django_content_type` e `auth_permission`, ou recriar o banco.

O dominio exige usuario proprio porque toda a especificacao e revisada por
humano: quem aprovou a pauta, quem confirmou os metadados, quem revisou o
artigo, quem revisou a resposta — com papeis distintos entre revisor tecnico e
editor.

Com schema por tenant, havia ainda a pergunta de onde os usuarios moram.

## Decisao

**Usuarios no schema `public`, compartilhados, com `TenantMembership`
ligando pessoa e tenant.**

```python
class User(AbstractUser):
    id = UUIDField(primary_key=True, default=uuid4, editable=False)
    email = EmailField(unique=True)          # USERNAME_FIELD
    full_name = CharField(max_length=150)
    role = CharField(choices=Role.choices)   # owner|editor|reviewer|viewer
    is_technical_reviewer = BooleanField(default=False)
    external_auth_provider = CharField(blank=True)
    external_subject_id = CharField(unique=True, null=True)
```

Tres razoes:

1. **O cadastro na home cria o usuario antes do schema existir.** Com usuarios
   por schema isso seria um problema de ovo e galinha.
2. Uma pessoa dona de varios sites loga uma vez, nao uma vez por site.
3. O Zitadel, previsto para depois, e um diretorio central com Organizations,
   que mapeia um-para-um para Tenant. Usuarios por schema tornariam esse
   encaixe torto.

Isso funciona porque o `search_path` dentro de um tenant e
`<schema>, public, extensions`: as tabelas compartilhadas continuam visiveis, e
o PostgreSQL aceita chave estrangeira entre schemas.

**Chaves primarias:**

- **UUID** nas entidades expostas externamente ou de alto valor: `User`,
  `Tenant`, `TenantMembership`, `Site`, `Document`, `SuperChunk`, `Article`,
  `Answer`, `Question`, `GenerationJob`.
- **BigAutoField** nas tabelas internas de alto volume de insercao:
  `NodeApiCall`, `PublishAttempt`, `RetrievalHit` — onde a localidade de indice
  importa mais que a opacidade do identificador.

Ids de `Article` e `Answer` aparecem no payload trocado com sites de terceiros;
identificadores sequenciais vazariam o volume de producao por cliente.

## Consequencias

- `USERNAME_FIELD = "email"`; o campo `username` do `AbstractUser` e removido.
- Todo campo de aprovacao referencia `settings.AUTH_USER_MODEL`.
- `external_subject_id` ja existe e e unico quando preenchido, entao a migracao
  para o Zitadel nao exigira migration destrutiva — apenas um backend de
  autenticacao adicional.
- Comprimento minimo de senha elevado para 10 caracteres.
