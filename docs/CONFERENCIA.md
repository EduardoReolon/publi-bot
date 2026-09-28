# Conferência depois de atualizar

A lógica de cada função é conferida pelos testes automáticos (`pytest`). Sobra
o que só o servidor de verdade mostra (banco, fila, contas, o seu site) e o
que só uma pessoa julga: se o resultado ficou bom. Este roteiro cobre só essas
duas partes.

## 1. Atualizar

```bash
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate_schemas
venv/bin/python manage.py semear_prompts --todos
sudo systemctl restart publibot celery-publibot celery-beat-publibot
```

## 2. Um comando confere o ambiente

```bash
venv/bin/python manage.py conferir_instalacao
```

Cada linha sai com `OK`, `FALHA` (e o que fazer) ou `--` (não configurado).

| Confere | Como |
|---|---|
| Migrações | do `public` e de cada cliente |
| Fila, worker e beat | o broker responde, um worker responde ao ping, e um agendamento rodou nos últimos 30 min |
| Pasta de mídia | grava e apaga um arquivo em `MEDIA_ROOT` |
| Prompts | nenhum faltando em cada cliente |
| Seu site | a rota `/health/` do contrato, com assinatura: endereço, chave e relógio certos |
| DataForSEO | a conta responde, e mostra o saldo (rota gratuita) |
| YouTube | a chave é aceita (1 unidade da cota diária de 10.000) |
| SearXNG | devolve resultado em JSON |
| Search Console | a conta de serviço gera o token e tem acesso à propriedade |

O comando não gasta nada nem grava no livro-caixa. O modelo de texto e o de
imagem têm um comando próprio: `manage.py conferir_worker`.

## 3. O que só você julga (uma vez, uns 15 minutos)

- [ ] **Negócio.** Siga os passos 1 a 4. Os campos que o pedido 1 preenche
      fazem sentido para o seu negócio? As sementes do pedido 2 aparecem em
      *Radar › Configuração › Sementes sugeridas*?
- [ ] **Radar.** Rode uma rodada. Os temas de *Demanda e pautas* têm a ver com
      o que você vende? Em *Rendimento das sementes*, qual semente não trouxe
      nada? Troque-a.
- [ ] **Fontes sugeridas.** Abra *Ver o texto capturado* em duas ou três
      páginas. Se aparecer menu, rodapé, propaganda ou comentário no meio,
      recuse (ou recuse a área do site).
- [ ] **Artigos científicos.** Com a chave do OpenAlex cadastrada, rode o
      radar. Os artigos sugeridos (selo "artigo científico") são do seu tema?
      Aprove um com "PDF aberto" e um sem: o primeiro vai para a curadoria, o
      segundo para *Artigos aguardando o PDF*.
- [ ] **Artigo.** Gere um pelo caminho de sempre e um por *Artigo com outra
      IA* (numa pauta com o selo "vale o modelo grande", se houver). Na
      revisão, olhe:
      - se o link de saída leva à fonte certa;
      - se a chamada está onde faz sentido;
      - se as perguntas frequentes respondem ao que alguém buscaria;
      - se o tom segue o Guia editorial.
- [ ] **No seu site.** Publique um e confira na página publicada:
      - se a chamada virou o bloco do site;
      - se o autor e a foto aparecem;
      - se o *Leia também* aparece.
- [ ] **Parceiros.** Se marcou algum site como *Pode ser parceiro*, copie a
      proposta para um modelo grande. Leia o e-mail antes de mandar.

## Se algo não bater

| Onde olhar | O que mostra |
|---|---|
| Radar › Rodadas | situação de cada rodada, erros com o motivo |
| Configuração e custos › Últimas chamadas externas | cada chamada externa, custo e erro |
| Operação | trabalhos de geração de artigo |
| `journalctl -u celery-publibot -n 100` | erro que não chegou à tela |
| `journalctl -u celery-beat-publibot -n 50` | se os agendamentos estão disparando |

Contas externas (onde criar cada uma): `docs/CONTAS_EXTERNAS.md`. Dados de
exemplo para ver as telas do radar sem conta paga:
`manage.py tenant_command radar_exemplo --schema=<schema>`.
