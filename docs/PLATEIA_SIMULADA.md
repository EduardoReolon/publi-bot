# Plateia simulada — trabalho futuro

**Situacao:** ideia aprovada para depois; **nada implementado**. Fazer quando a
conta tiver posts publicados e medidos (ver "Quando comecar").

Lugar: modulo de redes (`apps/social`), sem mexer no nucleo
([ADR-0023](adr/ADR-0023-redes-sociais-como-modulo-isolado.md)).

## A ideia, em uma frase

Antes de publicar, varias "pessoas sinteticas" — personas montadas a partir
de comentarios e dores **reais** do publico — olham as versoes de um post como
se estivessem rolando o feed, escolhem a que as faria parar e dizem a
objecao. O resultado vira um **palpite inicial** (nunca a decisao) e uma fonte
de **ideias de gancho**; o PubliBot mede depois se esse palpite acerta com o
publico de verdade e da a ele so o peso que ele merecer.

## O que a pesquisa ja sabe (e define o desenho)

Usar modelos de linguagem como amostras sinteticas de pessoas e um campo de
pesquisa ativo. O consenso pratico:

| Funciona razoavelmente | Funciona mal |
|---|---|
| Separar o claramente fraco do claramente bom | Prever qual post vai viralizar |
| Levantar objecoes e duvidas que ninguem pensou | Dar notas absolutas (tudo vira "8/10") |
| Gerar ganchos variados para o mesmo tema | Representar minorias e publicos de nicho sem dados |
| Comparar duas versoes (qual e melhor) | Substituir o teste com gente de verdade |

Os defeitos conhecidos — personas otimistas demais ("gostam de tudo"),
parecidas demais entre si, e confiantes onde nao deveriam — viram as regras
do desenho abaixo.

## Desenho

### 1. Personas de vozes reais (algoritmo, sem LLM para inventar)

Fontes, todas ja no PubliBot:

- comentarios dos posts (`social.Comentario`);
- comentarios do YouTube que o Radar agrupa (dores do publico);
- as dores e o publico do Negocio, e o publico de cada conta;
- legendas que engajam no nicho (`social.ReferenciaDoNicho`).

Montagem:

1. Vetorizar os textos e agrupar (o mesmo agrupamento por proximidade dos
   temas, `apps/social/temas.py`). Cada grupo e uma persona.
2. A persona guarda: um rotulo curto, o tamanho do grupo (peso na plateia),
   5 a 10 **frases reais** dela (como ela fala), as dores que mais aparecem e
   as objecoes tipicas (comentarios de duvida e reclamacao do grupo).
3. Uma persona por grupo com tamanho minimo (ex.: 5 comentarios); no maximo
   8–12 por conta. Recalcular uma vez por semana.
4. Privacidade: so o agregado. Nada de nome, @ ou foto; frases muito
   pessoais (doenca, relato familiar) saem pelo mesmo filtro dos relatos.

Modelo de dados (tenant, no modulo social):

```
Persona: destino (ou rede), rotulo, peso, falas (JSON), dores (JSON),
         objecoes (JSON), centro (vetor), ativa, atualizada_em
AvaliacaoSimulada: post, persona, escolheu (A|B|nenhum), parou (bool),
                   objecao, gancho_sugerido, modelo, criada_em
```

### 2. Comparar, nunca dar nota

Para cada persona, um pedido curto ao modelo (prompt novo, semeado e
editavel: `social_plateia`):

```
Voce e esta pessoa: <rotulo>. Voce fala assim: <3 falas reais>.
O que te incomoda: <dores>. Voce esta rolando o feed do <rede>.
Versao A: <gancho + 1a lamina/linha>   Versao B: <idem>
Responda em JSON: {"para_em": "A"|"B"|"nenhuma", "por_que": ..., "objecao": ...,
                   "o_que_me_faria_parar": ...}
```

- **Escolha forcada** (A, B ou nenhuma), nunca nota de 0 a 10.
- Ordem A/B **sorteada** por persona (modelos tem vies de posicao).
- "Nenhuma" e resposta valida e conta: e o sinal de "este tema nao para ninguem".
- Modelo pequeno basta (resposta curta, JSON); roda nas conexoes de
  inferencia que ja existem, com a mesma reserva de capacidade.

### 3. Como o resultado entra

- **Na revisao do post:** "6 de 8 personas preferiram A; 2 nenhuma. Objecao
  mais comum: 'parece propaganda'." Com as objecoes, a pessoa ajusta antes de
  aprovar.
- **No sorteio das abordagens** (`experimentos.escolher`): como mais um ponto
  de partida, igual ao placar coletivo — `alfa += peso_plateia * votos_a_favor`,
  `beta += peso_plateia * votos_contra` — com `peso_plateia` calibrado (abaixo).
- **Ideias novas:** as respostas "o que me faria parar" e as objecoes mais
  repetidas (agrupadas por algoritmo) viram **sugestoes de abordagem** na tela
  Configurar > Abordagens, para aprovar com um clique. Nada entra sozinho.

### 4. Calibracao: o simulador so vale o que acertar

O PubliBot ja mede o resultado real de cada post (`experimentos.valor`,
"funcionou" contra a mediana da conta). Entao:

1. Guardar a previsao da plateia de todo post que ela avaliou.
2. Quando o post for julgado (7 dias), comparar: a versao que a plateia
   preferiu foi a que funcionou? Em posts soltos: a fracao de personas que
   "parariam" se correlaciona com a taxa real?
3. Medida de acerto: concordancia nos pares A/B (testes pagos sao os melhores
   juizes) e correlacao de postos (Spearman) nos posts soltos, numa janela
   movel dos ultimos N posts.
4. `peso_plateia` = funcao do acerto: perto de zero com acerto de moeda
   (~50% nos pares), subindo devagar ate um teto (ex.: o mesmo peso do
   placar coletivo) com acerto consistente. Novo parametro ajustavel em
   `apps/social/parametros.py` para o teto.
5. Mostrar na pagina Estrategia: "a plateia simulada acertou 14 de 20 testes
   desta conta (70%)" — ou "ainda sem dados para confiar nela".

Isto e o que torna a ideia segura: ninguem precisa acreditar no simulador;
ele ganha voz so se provar, conta por conta.

## Quando comecar

- As **personas** ja podem nascer dos comentarios do YouTube (Radar) e das
  dores, mesmo sem posts.
- A **calibracao** precisa de ~30 posts julgados na conta (ou ~10 testes A/B
  pagos) para dizer algo.
- Sugestao: implementar personas + avaliacao + exibicao primeiro (peso zero,
  so informativo), e ligar o peso no sorteio quando a calibracao tiver dados.

## Custo

Por post com duas versoes: personas (8–12) × 1 chamada curta ≈ 8–12 chamadas
de ~300 tokens de saida. Num modelo pequeno local, desprezivel; num pago, da
ordem de centavos por post. Recalcular personas: uma vez por semana, so
vetores (sem LLM).

## Riscos e cuidados

- **Otimismo e homogeneidade:** mitigados por falas reais, escolha forcada,
  "nenhuma" como opcao e calibracao.
- **Eco do proprio publico:** personas de quem ja comenta puxam para o que ja
  funciona; manter as referencias do nicho e as dores do Radar como fontes
  para nao fechar a bolha.
- **Nicho pequeno:** poucos comentarios = poucas personas (ou nenhuma). Sem
  persona com tamanho minimo, a plateia nao roda e a tela diz por que.
- **Conselho profissional:** as personas avaliam o gancho, nao a correcao
  clinica; a conferencia de numeros e as regras que nunca se quebram
  continuam valendo antes de tudo.
- **Privacidade:** so agregados; nenhum dado pessoal entra em prompt.

## Onde mexer (mapa para quem for implementar)

| O que | Onde |
|---|---|
| Personas (agrupar, recalcular semanal) | novo `apps/social/plateia.py`, reaproveitando o agrupamento de `temas.py` |
| Comentarios do YouTube do Radar | nova funcao em `apps/social/fontes.py` (a unica porta para o nucleo) |
| Prompt `social_plateia` | `apps/social/prompts.py` (semeado como os outros) |
| Avaliar as versoes de um post | depois de `redacao.escrever`, para posts com variante (A/B) |
| Peso no sorteio | `experimentos._alfa_beta` (mais um termo, como o coletivo) |
| Calibracao | `experimentos.avaliar` / `avaliar_testes` (ja julgam os posts) |
| Telas | revisao do post (`_post.html`), Estrategia (acerto da plateia), Abordagens (sugestoes) |
| Testes | `apps/social/tests/test_plateia.py`, com modelo e vetores simulados |
