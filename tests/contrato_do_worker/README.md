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

## Quando atualizar

Quando o worker subir a versão do contrato:

```bash
curl -s http://<worker>:8090/health/ | jq -r .contrato_versao
```

Se for diferente do `_contrato_versao` destes arquivos, copie os novos e leia
o `INTEGRACAO.md` de lá — versão maior diferente significa mudança
incompatível.
