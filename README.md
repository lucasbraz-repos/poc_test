# Pipeline de dados — carteira para análise e precificação

Pipeline executável construída a partir de `Enunciado_Teste_Tecnico.docx` e `BaseTeste.xlsx`. O fluxo principal recebe o Excel diretamente no **S3** e processa os dados com **Glue/Spark, Glue Data Catalog, Athena e Glue Workflow**. A execução local com Python/SQLite permanece disponível para desenvolvimento e conferência, com as mesmas regras de qualidade e somente Python 3.11+ e a biblioteca padrão.

O enunciado foi usado como requisito do exercício: explorar, tratar, justificar decisões e entregar a base final. A implementação e as propostas de consumo atendem ao pedido de montar uma pipeline.

Na AWS, os dados e relatórios ficam no S3 e as tabelas são consultadas no Athena. Na alternativa local, os resultados ficam em `outputs/<run_id>/`. O repositório contém código, testes e documentação; o Excel original, as bases individuais, o banco e o pacote ZIP não são versionados.

## Executar

### AWS: enviar o Excel diretamente pelo console do S3

Com a infraestrutura implantada, o processamento independe do sistema operacional do computador. Não é necessário instalar Python, AWS CLI ou Terraform para enviar uma carga pelo console.

1. Entre na conta AWS autorizada e abra o serviço S3 em `us-east-1`.
2. Abra o bucket de dados da instalação atual: **`poc-carteira-dev-909492399516-487d65-data`**. O nome também está no output Terraform `data_bucket` e na configuração da implantação.
3. Abra ou crie o prefixo **`landing/`** e faça upload da planilha, por exemplo `landing/BaseTeste.xlsx`. A extensão deve ser **`.xlsx` minúscula**, com o layout esperado de 79 colunas.
4. Acompanhe a execução em **AWS Glue → Workflows → `poc-carteira-dev`**. A fila SQS e a Lambda iniciam Bronze, Silver e Gold automaticamente; não é necessário iniciar os jobs manualmente.
5. Após a publicação, consulte as views no Athena, usando o workgroup `poc-carteira-dev` e o database `poc_carteira_dev_gold`. Para visualizar o relatório, baixe **`reports/<load_id>/relatorio.html`** do bucket de dados e abra no navegador. O `load_id` aparece nas propriedades da execução do workflow.

```text
Upload S3 landing/ → SQS → Lambda → Glue Bronze → Silver → Gold
                                                       ↓
                                     S3/Parquet + Catálogo → Athena
```

O upload pelo console não baixa nem abre o relatório automaticamente. Os dados tratados permanecem em Parquet no S3; SQLite e as exportações individuais em CSV pertencem à opção local. Cada arquivo representa uma fotografia completa da fonte. Para falhas, consulte os logs no CloudWatch e a fila DLQ, conforme [docs/aws.md](docs/aws.md).

Os scripts Python de envio, acompanhamento e exportação são alternativas ao console. A implantação e esses comandos verificam a conta de destino. Consulte [docs/aws.md](docs/aws.md) para operação e [docs/cicd.md](docs/cicd.md) para deploy e configuração da conta existente. As credenciais e os dados não são enviados ao GitHub.

A implantação e a execução real dos três jobs foram verificadas, incluindo consultas e reconciliação de saldos no Athena. Consulte [as evidências de validação](docs/validacao-aws.md).

O código de cada etapa está diretamente em `cloud/jobs/bronze.py`, `cloud/jobs/silver.py` e `cloud/jobs/gold.py`, incluindo as transformações PySpark de Silver e Gold. A organização está detalhada em [docs/aws.md](docs/aws.md).

### Alternativa local: desenvolvimento e conferência

Com Python 3.11+ instalado, execute no PowerShell, na raiz do projeto:

```powershell
python executar_pipeline.py "C:\caminho\BaseTeste.xlsx"
```

O comando executa todas as etapas locais e abre o relatório no navegador ao terminar. Sem o argumento do arquivo, solicita o caminho pelo terminal no Windows; no macOS, usa um seletor de arquivos. Os dados tratados, banco, relatórios, auditoria e quarentena ficam em `outputs/<run_id>/`. Essa execução é independente da AWS e não envia arquivos ao S3.

Para controlar pasta de saída e aba pelo terminal:

```powershell
python -m pipeline.run --input "C:\caminho\BaseTeste.xlsx" --output outputs
```

No macOS/Linux, use `python3` e o caminho correspondente. Para arquivos com várias abas, informe `--sheet "Nome da aba"`. O layout precisa corresponder às 79 colunas desta fonte; alterações interrompem a carga antes da publicação.

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
    rows = db.execute(
        """
        SELECT produto, quantidade AS contratos,
               saldo_contabil_centavos AS saldo_centavos,
               saldos_preenchidos, saldos_ausentes
        FROM mart_resumo_carteira
        WHERE data_corte = ? AND status_qualidade = 'APTO'
    """,
        ("2026-08-01",),
    ).fetchall()
    print(rows)
```

Outras oito consultas estão em [sql/consultas.sql](sql/consultas.sql); o parâmetro `:data_corte` deve ser vinculado a uma data ISO. Em consumo recorrente, filtre o corte e o estado de qualidade, preserve as unidades e acompanhe os alertas. Os arquivos com registros individuais devem ter acesso controlado.

## Validação

```bash
python3 -m unittest discover -s tests -v
```

A suíte local cobre conversões, qualidade, deduplicação, contratos de tipos, marts, comandos AWS e recuperação de publicações. `make test` executa os testes sem depender de uma conta AWS; os testes Spark exigem PySpark e são sinalizados como ignorados fora desse ambiente.

A integração dos jobs também foi executada na imagem oficial Glue 5: hashes nativos, somas acima de 64 bits, equivalência dos três marts com SQLite, Parquet, conflitos de chave e carteira inteiramente em quarentena. Para repetir:

```bash
docker run --rm --platform linux/amd64 --network none --memory 4g --cpus 2 \
  -v "$PWD:/workspace:ro" --workdir /workspace \
  -e SPARK_LOCAL_IP=127.0.0.1 --entrypoint /bin/bash \
  public.ecr.aws/glue/aws-glue-libs:5 \
  -lc 'export PYTHONPATH="/workspace:${PYTHONPATH}"; spark-submit --master local[2] tools/run_spark_tests.py'
```

Para testar a compatibilidade do SDK no mesmo contêiner, execute `python3 -m unittest discover -s tests_aws -p test_sdk_compat.py -v` no lugar do `spark-submit`.

Para verificar estilo: `python3 -m pip install -r requirements-dev.txt` e `make lint`. A publicação valida linhas, chaves, isolamento PF/PJ e reconciliação dos três valores financeiros. O comando `tools/verificar_aws.py` confere os dados publicados com consultas reais no Athena.

## Indicadores e disponibilização

Os três produtos agregados são `mart_resumo_carteira`, `mart_indicadores_carteira` e `mart_qualidade`. Incluem exposição, completude, sinal dos valores, atraso, disponibilidade de contato e indicadores declarados de garantia e ajuizamento. O relatório HTML permite escolher corte e universo e mostra os denominadores das medidas. Consulte [definições das métricas](docs/metricas.md) e [formas de consumo](docs/consumo.md).

Para exportar os agregados atuais da AWS:

```bash
python3 tools/exportar_aws.py --profile poc-aws --dataset todos
```

O comando grava CSV, JSON e dicionário em `outputs_aws/exportacoes/<load_id>/`. As views atuais no Athena selecionam uma única carga publicada. A infraestrutura inclui um workgroup de BI e uma política de leitura agregada, sem atribuição automática a usuários ou conexão automática com ferramentas de BI.

Para uma conferência rápida dos scripts, `tests_aws/test_medallion_syntax.py` compila e importa Bronze, Silver e Gold sem iniciar jobs. Na imagem Glue, execute `spark-submit --master local[2] tools/run_spark_tests.py --suite smoke` para exercitar os contratos Bronze, as transformações Silver/Gold e seus SQLs com três registros em memória. Esse smoke não lê Excel nem grava Parquet, não acessa AWS e não valida uma publicação final. Use `--layer bronze`, `--layer silver` ou `--layer gold` junto com `--suite smoke` para selecionar a camada. No GitHub Actions, o job **Validar Bronze, Silver e Gold** mostra um step separado para cada camada e é obrigatório para liberar o deploy. Use `--suite jobs` para os testes de integração existentes ou `--suite all` para ambos.

## CI/CD no GitHub

O [GitHub Actions](.github/workflows/cicd.yml) valida todos os pushes e PRs, inclusive alterações de documentação. Após os testes Python/PySpark e a validação Terraform, pushes na `main` com alterações fora de `docs/` e de arquivos `*.md` implantam automaticamente na AWS por OIDC, atualizam jobs e views e arquivam o commit completo no S3. Mudanças exclusivamente de documentação não fazem deploy. Uma execução manual na `main` permite repetir o deploy após as validações. Estado e lock do Terraform ficam em um bucket S3 privado; chaves permanentes não são armazenadas no GitHub.

Fluxo, configuração administrativa, atualização da configuração local e rollback estão em [docs/cicd.md](docs/cicd.md). Alterações de código não reenviam automaticamente os arquivos de dados.

## Organização do código

`pipeline/` reúne código local e regras compartilhadas com os jobs AWS; não deve ser removida ao usar somente a cloud. `cloud/jobs/` contém os pontos de entrada executados pelo Glue. Os scripts em `tools/` são executados no computador ou no GitHub Actions para operar a AWS, preparar pacotes e realizar verificações.

| Módulo | Responsabilidade |
|---|---|
| `pipeline/xlsx.py` | Leitura streaming de ZIP/XML com posições de célula. |
| `pipeline/schema.py` | Contrato das 79 colunas e versão das regras. |
| `pipeline/parsers.py` | Conversões e validações de tipos. |
| `pipeline/cleaning.py` | Regras de coerência, derivação e qualidade. |
| `pipeline/modeling.py` | Persistência SQLite e modelo dimensional. |
| `pipeline/metrics.py` | Definições e SQL compartilhados dos três marts. |
| `pipeline/dashboard.py` | Relatório interativo comum ao local e à AWS. |
| `pipeline/reporting.py` | Publicação local de HTML e Markdown agregados. |
| `pipeline/run.py` | Orquestração, deduplicação, exportações, controles e publicação. |

Investigações exploratórias adicionais estão em `analysis/`. Os números da execução e a trilha reproduzível da carga estão nos artefatos gerados.
