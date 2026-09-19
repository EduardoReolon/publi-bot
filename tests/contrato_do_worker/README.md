# Cópia do contrato do worker-gpu

Estes arquivos **não são fonte** — são cópia. O original vive no repositório
do `worker-gpu`, em `contrato/`.

Eles existem porque os dois lados moram em repositórios diferentes. Antes da
separação havia um teste que rodava o cliente de verdade deste projeto contra
o app do worker, no mesmo processo, e era ele que impedia os dois de
divergirem num nome de campo. Esse teste não pode mais existir, e sem
substituto a divergência voltaria a ser possível com as duas suítes verdes.

A substituição é esta: o worker confere que as respostas dele têm esta forma;
`tests/test_contrato_do_worker.py` confere que os adaptadores daqui leem esta
forma. Uma mudança num lado só quebra alguém.

## `saude-resposta.json` é meio-caminho

Os outros quatro são cópia fiel de `contrato/` do worker. Esse não: o
`/health/` ainda **não** está publicado lá, e este exemplo foi derivado do
`app.py` do worker.

A diferença importa. Um exemplo que só existe deste lado prende só este lado:
se o worker mudar a forma do `/health/`, os testes daqui continuam verdes
contra uma cópia que ninguém atualizou. Foi exatamente assim que os dois
comandos de configuração passaram a ler `None` em tudo quando o estado de
cada rota virou aninhado — sem erro, só sem os avisos.

Peça ao worker para publicar `contrato/saude-resposta.json` e conferir a
resposta real contra ele. Aí este arquivo volta a ser cópia, como os outros.

## Quando atualizar

Quando o worker subir a versão do contrato:

```bash
curl -s http://<worker>:8090/health/ | jq -r .contrato_versao
```

Se for diferente do `_contrato_versao` destes arquivos, copie os novos e leia
o `INTEGRACAO.md` de lá — versão maior diferente significa mudança
incompatível.
