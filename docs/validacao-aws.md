# Validação executada na AWS

Em **08/11/2026**, a pipeline foi implantada em `us-east-1` e executada com a fonte `BaseTeste.xlsx`. O upload em `landing/` iniciou o processamento sem chamada manual de cada job.

## Evidências de funcionamento

- Terraform aplicado: 59 recursos criados. Dois ajustes de integração foram aplicados durante a validação; o estado final está em `infra/terraform/`.
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

## Verificações complementares

- **32 testes locais** de conversão, limpeza, integração e entrada simplificada.
- **11 testes AWS** de contratos, roteamento, idempotência, falhas e ordem de publicação.
- Teste com a imagem oficial `public.ecr.aws/glue/aws-glue-libs:5`, Spark `3.5.4-amzn-0` e Java 17: leitura das 10.740 linhas, mesmas contagens finais e validação dos 13 controles distribuídos.
- Escrita e releitura Parquet verificadas para os conjuntos Silver e Gold no mesmo teste.
- Terraform validado com provider AWS fixado pelo arquivo `.terraform.lock.hcl`.

## Repetir a conferência

```bash
make test
python3 tools/status_aws.py --profile poc-aws
python3 tools/verificar_aws.py --profile poc-aws
```

O último comando consulta a última carga publicada no Athena, compara o resultado com o manifesto do S3 e salva a evidência local em `outputs_aws/verificacao/athena.json`. Os identificadores de execução, nomes dos buckets, conta e manifestos individuais permanecem nos artefatos locais e na conta AWS, fora deste repositório público.

Os números documentam esta fonte e esta versão de regras; arquivos diferentes podem ter outras quantidades e estados de qualidade. O relatório não determina preço de aquisição ou recuperação financeira.
