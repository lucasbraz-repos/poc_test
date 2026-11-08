# Pipeline de dados — carteira para análise e precificação

Pipeline executável construída a partir de `Enunciado_Teste_Tecnico.docx` e `BaseTeste.xlsx`. Oferece dois ambientes com as mesmas regras de qualidade: **AWS com S3, Glue/Spark, Glue Data Catalog, Athena e Glue Workflow**, e execução local com Python/SQLite. A execução local usa somente Python 3.11+ e a biblioteca padrão.

O enunciado foi usado como requisito do exercício: explorar, tratar, justificar decisões e entregar a base final. A implementação e as propostas de consumo atendem ao pedido de montar uma pipeline.

Os resultados são gerados localmente em `outputs/<run_id>/` após selecionar a planilha. O repositório contém código, testes e documentação; o Excel original, as bases individuais, o banco e o pacote ZIP não são versionados.

## Executar

### AWS: selecionar o Excel e processar na nuvem

Consulte [docs/aws.md](docs/aws.md) para implantar a infraestrutura com Terraform. Depois da implantação, dê dois cliques em **`Iniciar Pipeline AWS.command`** e escolha o Excel. O arquivo será enviado ao S3; a fila aciona o Glue Workflow, que processa Bronze, Silver e Gold, valida os dados e publica as tabelas para consulta no Athena. O relatório agregado abre ao concluir.

```bash
# Implantação única; exige um perfil AWS autenticado na conta indicada.
python3 tools/deploy_aws.py --profile poc-aws --account-id SEU_ID_DE_12_DIGITOS

# Nas próximas cargas, só é necessário informar o arquivo.
python3 tools/enviar_aws.py "/caminho/BaseTeste.xlsx" --profile poc-aws
```

Os dados na AWS ficam em Parquet no S3. SQLite e CSV pertencem à opção local. A conta de destino é conferida antes de qualquer implantação ou upload. As credenciais e os dados não são enviados ao GitHub.

A implantação e a execução real dos três jobs foram verificadas, incluindo consultas e reconciliação de saldos no Athena. Consulte [as evidências de validação](docs/validacao-aws.md).

O código de cada etapa está diretamente em `cloud/jobs/bronze.py`, `cloud/jobs/silver.py` e `cloud/jobs/gold.py`, incluindo as transformações PySpark de Silver e Gold. A organização está detalhada em [docs/aws.md](docs/aws.md).

### Selecionar o arquivo e deixar tudo automático

No Finder, abra a pasta do projeto e dê **dois cliques em `Iniciar Pipeline.command`**. Escolha o arquivo `.xlsx` na janela que aparecer. A pipeline executa todas as etapas e abre o relatório no navegador ao terminar. Não é preciso informar pastas de saída nem iniciar etapas separadamente.

Os dados tratados, banco, relatórios, auditoria e quarentena ficam em `outputs/<run_id>/`. Se você selecionar novamente o mesmo arquivo, a pipeline verifica e reutiliza os resultados existentes. A seleção pode ser cancelada sem iniciar uma carga. Essa entrada foi preparada para macOS; o Python continua sendo o único requisito de instalação.

### Execução pelo terminal, se preferir

Na raiz deste projeto:

```bash
python3 -m pipeline.run --input "/caminho/BaseTeste.xlsx" --output outputs
```

Em outra máquina, substitua o caminho de entrada pelo local do XLSX. Para arquivos com várias abas, informe `--sheet "Nome da aba"`. O layout precisa corresponder às 79 colunas desta fonte; alterações interrompem a carga antes da publicação.

Disponibilize sua cópia de `BaseTeste.xlsx` no computador e informe seu caminho ou use o seletor de arquivos. A pipeline preserva uma cópia na camada Bronze; o arquivo de entrada não acompanha o repositório.

A CLI imprime a pasta publicada, `outputs/<run_id>/`. `outputs/latest.json` aponta para a carga mais recente com caminhos relativos, válidos também depois de mover a pasta. O relatório abre no navegador sem servidor. Para localizar o caminho pelo terminal:

```bash
python3 -c 'import json,pathlib; p=pathlib.Path("outputs"); print(p/json.loads((p/"latest.json").read_text())["report"])'
```

Uma segunda execução com o mesmo arquivo, código, versão de regras e seleção de aba verifica os hashes e reutiliza a carga. Uma alteração nesses elementos cria outra pasta. Artefatos existentes alterados causam falha explícita; use outro `--output` para reconstruir sem sobrescrevê-los. A publicação acontece após as validações, por renomeação da pasta temporária no mesmo sistema de arquivos. Falhas preservam uma pasta oculta `.tmp` para diagnóstico e não substituem a última carga válida.

## Resultado confirmado nesta base

| Indicador | Quantidade |
|---|---:|
| Linhas de dados recebidas | 10.740 |
| Registros únicos tratados | 10.500 |
| Duplicatas removidas | 240 |
| Duplicatas exatas | 137 |
| Duplicatas equivalentes após normalizar e-mail | 103 |
| APTO: sem alertas impeditivos ou de revisão | 3.067 |
| REVISAO: preservados com alertas | 7.414 |
| QUARENTENA: chave/identificação pendente | 19 |
| Registros na fato analítica, APTO + REVISAO | 10.481 |

O corte é **01/08/2026**, lido da fonte. Há 6.400 registros PF e 4.100 PJ após a deduplicação. Os documentos presentes têm dígitos verificadores válidos; os blocos PF/PJ já estavam separados. Essas verificações foram mantidas como proteções da pipeline.

Os 19 registros em quarentena correspondem a 13 cedentes ausentes e 6 registros em três grupos de contrato repetido para devedores diferentes no mesmo cedente/corte. A chave conservadora exige validação com o cedente; os registros permanecem integralmente na Silver e na exportação de quarentena.

Os principais tratamentos são padronização de ausências, identificação por posição de coluna, conciliação de campos repetidos, normalização de formatos, recuperação de centavos com resíduos mínimos do Excel, deduplicação e cálculo do atraso no corte. Valores negativos são preservados e sinalizados. Há 9.497 divergências entre atraso informado e calculado nos 9.500 registros únicos comparáveis; o atraso declarado continua disponível.

`APTO` significa que o registro passou pelas regras locais de qualidade. A base permite preparar análises de precificação; estimar recuperação ou preço exige recebimentos históricos, custos de cobrança e validação das premissas de negócio.

## Camadas e modelagem

A implementação segue a arquitetura **Medallion**. Na AWS, Bronze/Silver/Gold usam Parquet no S3 e databases separados no Glue Data Catalog. Na execução local, Bronze usa arquivos e Silver/Gold são camadas lógicas no SQLite.

```text
XLSX -> Bronze: original + linhas originais + SHA-256
     -> Silver: tipagem, limpeza, deduplicação, ocorrências e linhagem
     -> Gold: dimensões + fato de carteira + visões e agregados
     -> CSV / SQLite / relatório HTML e Markdown
```

A fato é uma fotografia por **cedente + contrato + data de corte**, com chaves para as dimensões de devedor, cedente, produto e data. Não se somam saldos de cortes diferentes como se fossem dívidas distintas. O detalhe dos modelos e alternativas está em [docs/arquitetura.md](docs/arquitetura.md); os critérios de tratamento estão em [docs/decisoes.md](docs/decisoes.md).

| Arquivo da carga | Conteúdo |
|---|---|
| `relatorio.html` / `relatorio.md` | Apresentação agregada com indicadores, ocorrências, exposição e validações. |
| `base_tratada.csv` | Todos os 10.500 registros únicos, incluindo estado de qualidade. |
| `base_precificacao.csv` | 3.067 registros APTO segundo as regras implementadas. |
| `quarentena.csv` | Os 19 registros bloqueados para saneamento. |
| `carteira.sqlite` | Silver, dimensões, fato, visões, ocorrências, perfil e linhagem. |
| `carteira_agregada.csv` | Resumo por corte, tipo, produto, atraso e qualidade. |
| `carteira_sem_identificacao_direta.csv` | Exportação sem nome, documento e contatos; não equivale a anonimização. |
| `ocorrencias_qualidade.csv` | Regra, campo, gravidade e justificativa por registro. |
| `linhagem.csv` | Relação entre arquivo, aba, linha original e registro tratado. |
| `dicionario_dados.csv` | Tipos, unidades, colunas de origem e definições dos campos. |
| `qualidade.json` | Métricas estruturadas para monitoramento. |
| `manifest.json` | Identificação da carga, versões, controles e SHA-256 de todos os artefatos. |
| `bronze/` | Cópia original, protegida contra escrita acidental, layout e linhas brutas em JSONL. |

CSVs usam UTF-8 com BOM, separador `;` e monetários em **centavos inteiros**. Importe documentos/CEP como texto para preservar zeros. Textos iniciados por `=`, `+`, `-`, `@`, tab ou retorno recebem apóstrofo para evitar execução de fórmulas em planilhas; o valor textual canônico está no SQLite. Campos ausentes continuam nulos, sem preenchimento artificial com zero.

## Consultar e disponibilizar

Use o SQLite local ou importe os CSVs em sua ferramenta analítica. O HTML apresenta os agregados sem dados individuais. A proposta de evolução para warehouse, BI, API e orquestração está documentada com critérios de adoção; esses serviços não são dependências da entrega.

Exemplo de consulta, usando um caminho real de `outputs/<run_id>/carteira.sqlite`:

```python
import sqlite3

with sqlite3.connect("file:outputs/SEU_RUN_ID/carteira.sqlite?mode=ro", uri=True) as db:
    rows = db.execute("""
        SELECT produto, COUNT(*) AS contratos,
               SUM(valor_contabil_centavos) AS saldo_centavos
        FROM vw_precificacao
        WHERE data_corte = ?
        GROUP BY produto
    """, ("2026-08-01",)).fetchall()
    print(rows)
```

Outras oito consultas estão em [sql/consultas.sql](sql/consultas.sql); o parâmetro `:data_corte` deve ser vinculado a uma data ISO. Em consumo recorrente, filtre o corte e o estado de qualidade, preserve as unidades e acompanhe os alertas. Os arquivos com registros individuais devem ter acesso controlado.

## Validação

```bash
python3 -m unittest discover -s tests -v
```

São 32 testes locais de conversão, regras de negócio, integração e entrada simplificada, além de 11 testes de contratos e orquestração AWS em `tests_aws/`. Incluem seleção e cancelamento do arquivo, abertura do relatório, limites do calendário Excel, datas de dois dígitos, negativos, CPF/CNPJ, e-mail Unicode, isolamento PF/PJ, colisões de chave, quarentena, deduplicação, linhagem, falha de layout sem publicação e reexecução idempotente com detecção de alteração dos artefatos.

Cada carga também executa 13 controles de publicação: integridade SQLite e chaves estrangeiras, reconciliação de linhas, unicidade, isolamento PF/PJ, coerência documental, ausência de erros bloqueantes na Gold, elegibilidade e reconciliação dos três valores monetários entre Silver, Gold e quarentena. As oito consultas de consumo foram executadas na base real.

## Organização do código

| Módulo | Responsabilidade |
|---|---|
| `pipeline/xlsx.py` | Leitura streaming de ZIP/XML com posições de célula. |
| `pipeline/schema.py` | Contrato das 79 colunas e versão das regras. |
| `pipeline/parsers.py` | Conversões e validações de tipos. |
| `pipeline/cleaning.py` | Regras de coerência, derivação e qualidade. |
| `pipeline/modeling.py` | Persistência SQLite e modelo dimensional. |
| `pipeline/reporting.py` | HTML offline e Markdown agregados. |
| `pipeline/run.py` | Orquestração, deduplicação, exportações, controles e publicação. |

Investigações exploratórias adicionais estão em `analysis/`. Os números da execução e a trilha reproduzível da carga estão nos artefatos gerados.
