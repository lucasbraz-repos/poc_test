# Validação executada na AWS

Em **08/11/2026**, a pipeline foi implantada em `us-east-1` e executada com a fonte `BaseTeste.xlsx`. O upload em `landing/` iniciou o processamento sem chamada manual de cada job.

## Evidências de funcionamento

- Terraform validado e aplicado; a atualização acrescenta dois marts, um workgroup de BI e uma política de leitura agregada. O estado local não é publicado no GitHub.
- Glue Workflow: execução da fonte com estado `COMPLETED`, três ações bem-sucedidas e nenhuma ação com falha.
- Jobs Bronze, Silver e Gold: `SUCCEEDED`.
- Manifesto e recibo da fonte gravados no S3, após registro da carga no Glue Data Catalog.
- Relatório agregado gerado no S3 e recuperado pelo comando de envio.
- Consultas no Athena executadas sobre as tabelas/views publicadas, comparadas ao manifesto da execução.

| Medida consultada no Athena | Resultado |
|---|---:|
| Bronze: linhas de origem | 10.740 |
| Silver: registros únicos | 10.500 |
| Gold: carteira publicada | 10.481 |
| APTO | 3.067 |
| REVISAO | 7.414 |
| QUARENTENA | 19 |

As somas de dívida original, saldo contábil e valor de face foram reconciliadas no Athena: **Silver = Gold + quarentena**, com diferença zero nas três medidas, em centavos inteiros.

## Produtos analíticos da refatoração

A execução completa com a refatoração publicou os três marts e o relatório interativo. A verificação real no Athena confirmou as contagens acima, os três universos e a reconciliação financeira dos marts com a fato. As exportações CSV/JSON foram executadas; todos os campos dos três datasets foram comparados com a execução local da mesma fonte:

| Produto | Linhas | Conferência SQLite × Athena |
|---|---:|---|
| Indicadores por universo e corte | 3 | Todos os campos equivalentes. |
| Resumo por corte, PF/PJ, produto, atraso e qualidade | 240 | Todos os campos equivalentes. |
| Qualidade por regra/campo/severidade | 38 | Todos os campos equivalentes. |

Na carteira publicada, a dívida original soma **R$ 312.410.390,81**, o saldo contábil **R$ 248.274.207,82** e o valor de face **R$ 366.412.396,81**. São três medidas diferentes, preservando valores negativos. Há **512** saldos contábeis ausentes e **1.125** negativos entre os **9.969** preenchidos.

As três views atuais foram consultadas e selecionaram exclusivamente a nova carga. A leitura dos saldos das partições históricas do resumo também foi conferida após a evolução do schema. Novos indicadores não são retrocalculados automaticamente em cargas antigas.

## Verificações complementares

- **45 testes locais** de conversão, limpeza, integração, contratos, métricas, relatório e entrada simplificada.
- **29 testes AWS sem serviços externos**, cobrindo contratos, roteamento, idempotência, falhas, ordem de publicação e comandos de operação.
- Teste com a imagem oficial `public.ecr.aws/glue/aws-glue-libs:5`, Spark `3.5.4-amzn-0` e Java 17: leitura das 10.740 linhas, mesmas contagens finais e validação dos 13 controles distribuídos.
- **5 testes de integração Spark** executados separadamente na imagem oficial Glue 5: hashes nativos, valores acima de 64 bits, equivalência Spark/SQLite dos três marts, Parquet, conflitos, overflow e universos vazios. Na execução Python sem PySpark, esses cinco testes aparecem como ignorados; foram executados com sucesso no contêiner.
- **1 teste de compatibilidade do SDK real** na imagem Glue 5: header condicional assinado, resposta 412 preservada e remoção do hook após a chamada. Esse teste adicional é ignorado quando boto3 não está instalado.
- Terraform validado com provider AWS fixado pelo arquivo `.terraform.lock.hcl`.

## Repetir a conferência

```bash
make test
python3 tools/status_aws.py --profile poc-aws
python3 tools/verificar_aws.py --profile poc-aws
```

O último comando consulta a última carga publicada no Athena, compara o resultado com o manifesto do S3 e salva a evidência local em `outputs_aws/verificacao/<load_id>/athena.json`. Os identificadores de execução, nomes dos buckets, conta e manifestos individuais permanecem nos artefatos locais e na conta AWS, fora deste repositório público.

Os números documentam esta fonte e esta versão de regras; arquivos diferentes podem ter outras quantidades e estados de qualidade. O relatório não determina preço de aquisição ou recuperação financeira.
