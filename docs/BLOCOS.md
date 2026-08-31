# Blocos de contexto — explicar o projeto a uma IA pequena

Um modelo de contexto pequeno nao cabe este repositorio inteiro, e um arquivo
solto nao se explica: `capas.py` sem `imagens.py` ao lado nao diz por que a
imagem vira WebP, e sem o ADR nao diz por que sao tres opcoes.

O meio-termo util e o **bloco**: os arquivos que respondem juntos por uma
capacidade do sistema, empacotados num unico `.md` com orientacao de leitura na
frente. Um bloco cabe numa janela pequena e se explica sozinho.

```bash
python scripts/gerar_blocos.py
```

Escreve um `.md` por bloco em `docs/blocos/`, mais um `README.md` com o indice e
o tamanho de cada um. Leva menos de um segundo. Abra o indice, escolha o bloco,
mande o arquivo inteiro para a IA.

---

## Comandos

| Comando | O que faz |
|---|---|
| `python scripts/gerar_blocos.py` | Gera todos os blocos em `docs/blocos/`. |
| `python scripts/gerar_blocos.py capas publicacao` | Gera so os blocos nomeados. O indice continua listando todos. |
| `python scripts/gerar_blocos.py --com-testes` | Inclui os arquivos de teste. |
| `python scripts/gerar_blocos.py --listar` | So mostra a tabela de tamanhos, sem escrever nada. |
| `python scripts/gerar_blocos.py --conferir` | So verifica a cobertura. Sai com `1` se algum arquivo ficou de fora. |
| `python scripts/gerar_blocos.py --saida /tmp/x` | Escreve em outra pasta. |

Nao precisa de banco, de `venv` ativado nem de variavel de ambiente — so de
Python 3.12 e do `git` (a lista de arquivos vem de `git ls-files`, o que faz o
`.gitignore` ser respeitado de graca).

### `--com-testes`: quando vale

Sem a opcao, os testes ficam de fora e os blocos ficam com cerca de metade do
tamanho.

Ligue quando a pergunta for sobre **comportamento** — "o que acontece se o
modelo nao citar fonte?", "isso e retentavel?". Neste projeto os testes sao a
especificacao mais precisa que existe: cada um tem uma docstring dizendo qual
defeito real ele impede.

Deixe desligado quando a pergunta for sobre **estrutura** — "onde eu ligo um
campo novo?", "por onde passa a publicacao?".

---

## A pasta `docs/blocos/` nao e versionada

De proposito. Os blocos sao derivados do codigo, entao envelhecem a cada commit
— e um bloco velho e pior que bloco nenhum: alguem manda para uma IA e recebe
uma resposta confiante sobre codigo que nao existe mais.

Regerar custa menos de um segundo. Regere antes de usar.

---

## Como um arquivo novo entra sozinho no bloco certo

Esta e a parte que exige cuidado, e o desenho gira em torno dela.

Se os blocos fossem listas de arquivos escritas a mao, todo modulo novo ficaria
de fora **em silencio**. O sintoma seria o pior possivel: um bloco que parece
completo e nao esta.

Por isso a atribuicao e por **padrao de caminho**, em
[`scripts/blocos.toml`](../scripts/blocos.toml), com tres regras:

**1. O primeiro padrao que casar leva o arquivo.** Blocos sao avaliados na
ordem em que aparecem no arquivo, entao os especificos vem antes dos gerais.

**2. Cada area termina num curinga.** O bloco `conteudo-modelos` casa
`apps/content/**`. E ele que cumpre a promessa: um modulo novo em
`apps/content/` cai ali sem ninguem editar configuracao nenhuma.

**3. Nao ha curinga global.** Um diretorio inteiramente novo — digamos
`apps/faturamento/` — nao casa com nada, e o comando **falha** dizendo o nome do
arquivo:

```
ERRO: 1 arquivo(s) fora de qualquer bloco:
  apps/faturamento/models.py

Acrescente um padrao em scripts/blocos.toml — antes do curinga da area.
```

Um curinga global transformaria isso em silencio, e o codigo novo iria parar num
bloco onde ninguem o procuraria. Falhar alto e a escolha certa: acrescentar um
bloco leva um minuto, descobrir meses depois que a IA respondia sem ver metade
do assunto nao tem conserto.

`tests/test_blocos_de_contexto.py` roda essa mesma verificacao na suite, entao a
cobertura nao se perde entre uma geracao e outra.

---

## Acrescentar ou mudar um bloco

Edite [`scripts/blocos.toml`](../scripts/blocos.toml). Um bloco e:

```toml
[[bloco]]
nome = "capas"
titulo = "Imagem de capa: tres opcoes, escolhidas por uma pessoa"
resumo = """
Gera lotes de tres opcoes de capa, permite pedir mais exemplos sem descartar as
anteriores, e exige que alguem escolha.
"""
entrada = ["apps/content/capas.py"]
relacionados = ["inferencia", "revisao-telas"]
padroes = ["apps/content/capas.py", "apps/content/imagens.py"]
```

| Campo | Para que serve |
|---|---|
| `nome` | Vira o nome do arquivo (`capas.md`). |
| `titulo` | Primeira linha do `.md`. |
| `resumo` | O paragrafo que orienta a IA antes do codigo. E o campo que mais muda a qualidade da resposta — vale escrever com cuidado. |
| `entrada` | "Comece por aqui". Precisa apontar para arquivos que estao no proprio bloco (ha teste). |
| `relacionados` | O que esta **fora**. O `.md` instrui a IA a dizer que falta contexto em vez de supor. |
| `padroes` | Globs. `**` casa a pasta inteira, inclusive os arquivos na raiz dela. |

**Ponha o bloco novo antes do curinga da area a que ele pertence.** Depois, e
invisivel: o curinga ja levou os arquivos.

Depois de editar, confira:

```bash
python scripts/gerar_blocos.py --listar
```

A tabela mostra quantos arquivos e quantos tokens cada bloco tem. Um bloco
`<- vazio` significa padrao que nao casa mais nada (arquivo renomeado). Um
`<- grande` passou de 30 mil tokens estimados e talvez precise ser dividido.

---

## O que sai de fora

`ignorar` no topo do `blocos.toml` lista o que existe no repositorio e nao
ajuda a explicar nada: migrations (sao geradas, e longas — o model que as
originou diz a mesma coisa em menos linhas), `__init__.py` vazios, caches,
locale, fixtures.

Arquivo ignorado nao conta como orfao. Se voce quiser que algo volte a
aparecer, tire a linha correspondente.

---

## O que o `.md` gerado contem

Nesta ordem:

1. Titulo e tamanho estimado.
2. O `resumo` do bloco.
3. **Como ler** — instrucoes para a IA, inclusive a de que os comentarios
   explicam o *porque* e sao a parte mais informativa.
4. **Comece por** — os arquivos de entrada.
5. **Fora deste bloco** — os blocos relacionados, com a instrucao de admitir
   falta de contexto em vez de supor.
6. A lista de arquivos com tamanho.
7. Cada arquivo inteiro, com o caminho real como cabecalho.

Nenhum arquivo e truncado. Cortar codigo no meio produz respostas erradas com a
mesma confianca das certas — se um bloco nao couber, divida-o em dois em vez de
encurta-lo.

Arquivos `.md` dentro de um bloco (ADRs, o contrato) recebem cerca de codigo
mais longa que qualquer sequencia de crases que tenham dentro. Sem isso, a
primeira cerca interna fecharia a externa e o resto do arquivo vazaria como
texto solto — sem nenhum sinal de que isso aconteceu.

---

## Os blocos de hoje

Rode `--listar` para os tamanhos atuais. Em linhas gerais:

| Bloco | Assunto |
|---|---|
| `visao-geral` | Por onde comecar: o produto e o ciclo de vida do conteudo. |
| `acervo-extracao` | Leitura de PDF, deteccao de secoes, metadados. |
| `acervo-busca` | Embeddings, limiar de recuperacao, saude da busca. |
| `acervo-telas` | Envio de documento, curadoria, calibracao. |
| `geracao-texto` | O artigo escrito em rodadas curtas, e a regra de embasamento. |
| `capas` | As tres opcoes de imagem e a escolha humana. |
| `revisao-telas` | A tela que aprova o conteudo. |
| `inferencia` | Conexoes de modelo, reserva de capacidade, adaptadores. |
| `conteudo-modelos` | As tabelas de artigo, secao, autor, pergunta e resposta. |
| `publicacao` | Payload, assinatura, idempotencia, cadencia. |
| `contrato` | O que um site precisa implementar, e o no de referencia. |
| `orquestracao` | O motor de trabalhos e o painel de operacao. |
| `tenancy` | Multi-tenancy, cadastro, isolamento. |
| `fundacao` | Settings, rotas, layout, deploy. |
| `decisoes` | Os ADRs. |
