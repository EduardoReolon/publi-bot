# Estudos de mercado

Uma bancada do dono para estudar as buscas do Google numa regiao, em rodadas
com outra IA. Aparece **so no tenant marcado** (`Tenant.estudos`, no admin do
dominio raiz) e **so para superusuario**; nos outros, a rota responde 404.

## O fluxo

1. **Novo estudo**: nome, a descricao da empresa (vai inteira no pedido), a
   cidade do centro e o raio. As cidades do raio saem das coordenadas dos
   municipios (`apps/estudos/dados/municipios.csv`, do projeto aberto
   kelvins/municipios-brasileiros, licenca MIT) e a populacao do Censo do IBGE
   (SIDRA, tabela 4709), lida uma vez por estado.
2. **Copiar o pedido**: leva a empresa, as cidades (distancia e populacao),
   como os numeros sao medidos e quanto custam, o funil e tudo o que as rodadas
   anteriores acharam (ate 150 palavras, com volume, custo por clique,
   concorrencia, tendencia, pico e o valor real das cidades medidas).
3. **Colar a resposta** (blocos `ANALISE`, `MODO`, `PALAVRAS`, `SEMENTES`,
   `SITE`, `CIDADES`, `CAMPANHA`, `NEGATIVAS`): vira uma rodada para conferir.
   A pessoa escolhe "quero estimativa" ou "quero o valor mais real", ajusta
   palavras e cidades e ve o custo estimado.
4. **Buscar no Google**: na fila (aparece na Operacao).

## Estimativa e valor real

- **Estimativa**: um pedido de volume por estado da area (ate 1000 palavras).
  Cada cidade recebe a fatia dela na populacao do estado; a area e a soma.
  Supoe busca proporcional a populacao: serve para explorar e comparar.
- **Real**: mais um pedido por cidade marcada. Cidade pequena volta muitas
  vezes "sem dado" (o Google so informa acima de um minimo), e a tela mostra
  isso, nao zero. A pagina da palavra mostra o fator real/estimado de cada
  cidade.
- **Sementes ou site**: um pedido de ideias do Google no estado do centro, ja
  com volume; as ideias entram na estimativa das outras cidades.

O Google nao mede volume num raio: com coordenadas, a DataForSEO devolve o
pais. Por isso o raio e feito aqui.

## As contas (`funil.py`)

- tendencia: os ultimos 12 meses contra os 12 anteriores (dois anos de
  historico vem na mesma chamada do volume);
- sazonalidade: o indice de cada mes do ano; pico quando passa de 15% acima da
  media;
- funil da campanha: buscas x cliques por busca x contatos por clique x
  clientes por contato, com o custo dos cliques (o Google informa em dolar; a
  cotacao fica no estudo). Sai por mes medio e mes a mes.

Toda chamada vai para o livro-caixa do Radar (finalidade "Estudo de mercado")
e respeita o teto mensal do tenant. A conta da DataForSEO e a do tenant
(Radar > Contas externas).

## Virar cliente (`passagem.py`)

Quando a empresa quiser artigos e trafego organico, "Criar o tenant" (no fim
da tela do estudo) cria um tenant novo, ja com os primeiros passos feitos:

1. "Copiar o pedido dos primeiros passos": um pedido so, com a descricao e as
   buscas do estudo, nos mesmos blocos dos primeiros passos do Negocio e do
   Guia (`apps/editorial/primeiros_passos.py`, lidos pelos mesmos leitores);
2. cole a resposta, escolha o nome e o subdominio e crie. O registro sai como
   no cadastro (quem esta logado e o dono); uma tarefa na fila (Operacao) cria
   o schema e preenche o Negocio (tema, publico, oferta, dores, frentes, onde
   atende, valor do cliente, cotacao), o Radar (sementes, dores, a cidade do
   centro como regiao) e o Guia editorial. Opcional: as mesmas contas externas
   (copiadas cifradas, como `copiar_contas_externas`).

O site do cliente (endereco e chave) fica para ele, em "Site e cadencia".
