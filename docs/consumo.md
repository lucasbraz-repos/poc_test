# Disponibilização e consumo dos dados

A entrega oferece dados detalhados para análise controlada e agregados para acompanhamento da carteira. Comece pelas views atuais no Athena e pelo relatório agregado. A tabela física histórica serve à auditoria; cada consulta precisa escolher uma carga e um corte.

## Interfaces

| Interface | Uso |
|---|---|
| Relatório HTML e JSON da carga | Ver volumes, saldos, qualidade e metadados sem consultar registros individuais. |
| Athena: `vw_carteira_atual` | Consultar a última carga publicada com dimensões e estado de qualidade. Inclui identificação direta; usar somente quando necessária. |
| Athena: `vw_precificacao_atual` | Subconjunto APTO da carga atual. O nome é mantido por compatibilidade; não representa uma precificação calculada. |
| Athena: `vw_carteira_mascarada_atual` | Análises de contratos sem nome, documento ou contato direto. Identificadores e combinações de atributos ainda podem permitir associação com outras bases. |
| Athena: `vw_resumo_carteira_atual` | Segmentação agregada da carteira publicada por produto, PF/PJ, atraso e qualidade. |
| Athena: `vw_indicadores_carteira_atual` | Indicadores por universo e corte, com disponibilidade e sinal dos valores. |
| Athena: `vw_qualidade_atual` | Problemas por código/campo/severidade, com ocorrências e registros afetados. |
| SQLite local | Consulta offline e exportações da carga, com as mesmas definições dos marts. |

As views de consumo devem incluir somente cargas liberadas por `cargas_publicadas`. O relatório e os metadados mostram o corte da fonte e a publicação UTC separadamente. “Atual” significa última carga publicada; um arquivo de corte antigo reenviado depois de outro não se torna economicamente mais recente.

Os nomes de database/workgroup e bucket vêm da implantação, em `build/aws/deployment.json`. O fluxo de envio e operação está em [aws.md](aws.md); as definições dos indicadores estão em [metricas.md](metricas.md).

## Painéis e modelo de BI

Um painel inicial pode ter três páginas:

1. **Carteira:** contratos, identidades documentais distintas, saldo contábil e completude; distribuições por produto, PF/PJ, atraso e qualidade. Exibir as três medidas financeiras separadamente.
2. **Qualidade:** APTO/REVISAO/QUARENTENA, campos monetários ausentes/negativos e principais regras por registros afetados. Tornar visível o universo e o denominador dos percentuais.
3. **Carga:** arquivo/carga selecionada, corte, publicação, versão de regras/código e reconciliações. Uma execução técnica recente pode publicar uma fotografia antiga.

Usar filtros de carga, corte e universo antes de interpretar indicadores. A versão atual pode conter vários cortes se o próprio arquivo os trouxer. Não somar os três universos: APTOS está contido na carteira publicada, que está contida na base tratada. Os marts de carteira publicada não contêm quarentena.

Para o primeiro painel, conectar as views atuais reduz o risco de reunir versões históricas. Para um modelo dimensional com histórico, relacionar fato e dimensões pela combinação de `load_id` e chave da dimensão. Relacionar somente `devedor_id`, `cedente_id` ou `produto_id` encontra várias versões da dimensão e multiplica resultados. As consultas Athena da pipeline já fazem o vínculo por carga.

Dimensões filtram/agrupam e fatos fornecem medidas; manter grão consistente e relacionamentos de um para muitos segue a [orientação oficial de modelagem estrela do Power BI](https://learn.microsoft.com/en-us/power-bi/guidance/star-schema). Um modelo semântico deve definir medidas explícitas para médias, percentuais e distintos, evitando sua soma automática. As somas monetárias precisam de tipo decimal e conversão de centavos para reais na apresentação.

Amazon Quick Sight suporta [Athena como fonte](https://docs.aws.amazon.com/quick/latest/userguide/supported-data-sources.html). A conexão de uma ferramenta de BI é uma etapa de configuração e acesso; a existência dos marts não significa que um painel externo já esteja publicado.

## Exportações

Exportar somente a carga, corte, universo e campos necessários. Para análises gerais, preferir os marts agregados. Quando o detalhe por contrato for necessário, preferir a visão sem identificação direta e conceder acesso ao detalhe identificado somente a quem usa esses campos.

Athena disponibiliza resultados de `SELECT` em CSV e permite exportar consultas com [UNLOAD para formatos como Parquet e JSON](https://docs.aws.amazon.com/athena/latest/ug/unload.html). Usar um prefixo de destino novo por exportação; UNLOAD comum exige destino vazio e pode gerar vários arquivos. Manter os identificadores da carga/corte junto à exportação para que sua interpretação não dependa do painel.

Os CSVs locais usam UTF-8 com BOM e `;`, com identificadores como texto e valores em centavos. Textos que poderiam iniciar fórmula recebem proteção de planilha; números negativos mantêm o sinal. Essa convenção está documentada em [arquitetura.md](arquitetura.md). Para integrações automatizadas, SQLite/Parquet preservam melhor os tipos e evitam interpretação automática de documentos, datas e números.

Para baixar os três marts da última carga da release implantada:

```bash
python3 tools/exportar_aws.py --profile poc-aws --dataset todos
```

As opções `indicadores`, `resumo` e `qualidade` exportam apenas um produto. CSV/JSON e dicionário ficam em `outputs_aws/exportacoes/<load_id>/`, com carga e publicação identificadas. Somas decimais são texto exato no JSON; não converter para ponto flutuante em integrações financeiras.

## Acesso e evolução

Bucket privado e view reduzida não substituem autorização: consumidores precisam de permissões coerentes para catálogo, Athena e objetos S3. Se o mesmo usuário puder ler a tabela identificada, a existência de uma view mascarada não restringe esse acesso. As dimensões identificadas, Bronze e Silver têm finalidade distinta dos agregados de consumo.

Para um perfil que só precisa dos agregados, a configuração de BI deve usar um workgroup próprio com resultados em `results/bi/` e permitir leitura somente dos marts publicados e do controle necessário à escolha da carga. Leitura de fato, dimensões identificadas, Bronze ou Silver exige outro perfil de acesso. A política de consumo pode ser disponibilizada sem ser anexada automaticamente a um usuário: aplicar essa política, como configurar o conector de BI, é uma ação administrativa explícita. Os resultados das consultas também precisam desse controle de acesso.

Há duas configurações possíveis: um leitor apenas dos marts agregados, ou um analista autorizado ao detalhe reduzido/identificado. Uma permissão ampla nas tabelas-base não deve ser usada para simular o primeiro perfil. Uma chave derivada ou uma view sem identificação direta é pseudonimização e redução de campos, não uma garantia de anonimização.

Uma API só é necessária quando houver um sistema consumidor com contrato de acesso e consulta definido. O cenário atual já permite consumir pelo Athena, por BI, por relatório e por exportação; não requer abrir acesso público ao lake. A adoção de histórico incremental, tabelas de pagamentos ou outros marts deve partir de fontes e necessidades novas, mantendo o grão e a reconciliação explícitos.
