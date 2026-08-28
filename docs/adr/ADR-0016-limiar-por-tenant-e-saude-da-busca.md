# ADR-0016 — Limiar por tenant, e observabilidade em vez de auto-ajuste

**Status:** Aceito
**Data:** 2026-08-28

## Contexto

O ADR-0014 fixou `RAG_MAX_COSINE_DISTANCE` por medicao, mas o valor vivia no
`settings` — uma constante do processo, igual para toda a instalacao. Com mais
de um cliente em producao isso deixa de servir: a distancia que separa "sustenta
o texto" de "so fala do mesmo assunto" e propriedade do **corpus**, nao do
deploy.

O efeito e concreto. Num acervo de tema unico (digamos, so cardiologia) todo
trecho fala da mesma coisa, todas as distancias encolhem, e o limiar que filtrava
bem num acervo diverso passa a aceitar tudo. O contrario tambem acontece. Um
unico numero global erra nos dois sentidos ao mesmo tempo, em clientes
diferentes.

## Decisao

### 1. O limiar passa a viver no schema do tenant

`knowledge.RetrievalSettings` — linha unica por schema, com `max_cosine_distance`
e `top_k`. O `settings` deixa de ser o valor vigente e vira **o padrao com que um
tenant novo nasce** (via callable, para nao congelar o numero dentro da
migration).

A linha guarda tambem **quando, por quem e com que modelo** o valor foi medido.
Isso nao e auditoria por formalidade: sem o modelo registrado, uma troca de
`EMBEDDING_MODEL` deixa para tras um numero que parece conferido e nao e. Como
familias diferentes de modelo comprimem a similaridade de formas diferentes, o
limiar orfao nao erra por pouco — ele passa a nao querer dizer nada.

### 2. Observabilidade sim, auto-ajuste nao

A tentacao obvia e uma rotina que reajusta o limiar sozinha. Foi recusada, por
duas razoes:

**Falta gabarito.** Calibrar exige alguem dizendo "esta pauta *deveria* achar o
documento X, e uma receita de bolo *nao deveria* achar nada". Sem rotulos, um
ajustador automatico nao tem contra o que otimizar; ele so pode olhar para o que
o sistema ja faz, o que equivale a chamar o comportamento atual de correto.

**Falharia na direcao perigosa.** Um ajustador que tentasse "manter resultados
chegando" iria **afrouxar** o limiar quando o acervo piorasse. Mas a funcao do
limiar e exatamente dizer *"nao ha nada aqui que sustente isto"*. Um limiar que
se auto-ajusta converge para sempre responder — que e precisamente a falha que o
produto existe para evitar.

O que o sistema faz, entao, e **medir e avisar**, deixando a decisao com uma
pessoa:

- fracao de buscas que nao acharam nenhuma fonte, comparada com o periodo
  anterior. A comparacao so vira alerta sob tres condicoes: cresceu metade,
  passou de 10% **e** ha pelo menos tres buscas vazias. As duas primeiras
  sozinhas nao bastam — com vinte consultas no periodo, uma busca vazia virando
  duas ja salta de 5% para 10% e cruzaria o piso percentual;
- buscas que acharam fonte, mas menos do que pediram: nao e falha, e o sinal de
  que o acervo esta raso para o tema;
- mediana e p90 das distancias aceitas, e a folga que sobra ate o corte;
- histograma das distancias em relacao ao limiar;
- documentos curados que nunca sustentaram nada;
- limiar nunca calibrado, calibrado com outro modelo, ou indice com vetores de
  modelos misturados.

### 3. Sem rotina periodica

Nada disso e pesado, e isso foi medido, nao presumido. O painel inteiro sao ~15
agregacoes sobre uma janela de 30 dias, com indice em `RetrievalQuery.created_at`:

| Volume no schema | Tempo do painel |
|---|---|
| 39 consultas, 108 resultados | 21 ms |
| 5.039 consultas, 15.089 resultados | 77 ms |

Cinco mil consultas sao, para um cliente pequeno, alguns anos de geracao. Uma
rotina em segundo plano so trocaria esses 77 ms por um numero velho na tela sem
que ninguem soubesse de quando ele e.

O problema real nao era custo, era **alcance**: um painel so ajuda quem o abre.
Por isso os alertas da busca entram tambem no painel do tenant, que e a tela que
as pessoas ja abrem. O limiar errado nao produz sintoma na tela de busca — produz
artigo ruim do outro lado do sistema.

## Consequencias

- Cada cliente precisa calibrar o proprio limiar. A tela **Documentos >
  Qualidade da busca** faz a medicao no navegador: digite uma consulta como uma
  pauta real chegaria, veja as distancias de todo o corpus marcadas como "entra"
  ou "fora", escolha o corte.
- A medicao de calibracao **nao** passa por `recuperar()` e nao grava
  `RetrievalQuery`. Fosse o contrario, calibrar poluiria exatamente as metricas
  que a tela existe para mostrar.
- `manage.py tenant_command calibrate_retrieval` continua valendo e passou a
  exibir o limiar do tenant e a marcar cada linha; o valor escolhido se grava
  pela tela, que e o unico caminho que registra tambem o modelo.
- `RAG_MAX_COSINE_DISTANCE` no `.env` continua existindo, agora com o papel de
  padrao de fabrica. Mudar o `.env` nao altera nenhum tenant que ja exista.
- Uma rotina passa a fazer sentido no dia em que houver **para onde mandar o
  aviso** — e-mail, Slack, o que for. Ai o valor nao esta em calcular fora da
  requisicao, e sim em alcancar quem nao abriu o painel. Enquanto o unico canal
  for a propria tela, uma rotina nao entrega nada que a visita nao entregue.
