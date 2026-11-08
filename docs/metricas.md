# Métricas e limites da análise

A fonte representa uma fotografia da carteira por data de corte. Ela sustenta segmentação de contratos, saldos informados, atraso calculado e qualidade dos dados. A implementação das definições e das consultas comuns ao Spark e ao SQLite está em `pipeline/metrics.py`; os marts são calculados novamente em cada carga.

## Universos e grão

| Universo | Registros incluídos | Uso |
|---|---|---|
| `BASE_TRATADA` | Todos os registros únicos após deduplicação, incluindo quarentena. | Avaliar a qualidade e reconciliar a entrada. |
| `CARTEIRA_PUBLICADA` | `APTO` e `REVISAO`. | Analisar a carteira com alertas visíveis. |
| `APTOS` | Somente `APTO`. | Analisar o subconjunto que passou pelas regras locais de qualidade. |

APTO significa ausência de ocorrências WARNING e ERROR; ocorrências INFO são permitidas. Não determina preço, aprovação de crédito, probabilidade de recuperação ou confirmação cadastral. A quarentena permanece disponível para saneamento e não entra na fato publicada.

A fato tem o grão contrato + cedente + corte dentro de uma carga. Um devedor pode ter vários contratos: contar linhas não é contar pessoas. A chave é uma hipótese conservadora a homologar com o cedente; grupos conflitantes não são resolvidos por escolha arbitrária de devedor.

## Produtos analíticos

| Mart | Uma linha representa | Conteúdo |
|---|---|---|
| `mart_resumo_carteira` | Corte, PF/PJ, produto, faixa de atraso e estado de qualidade na carteira publicada. | Quantidade de contratos, três somas monetárias e sua disponibilidade/sinal. |
| `mart_indicadores_carteira` | Universo e corte da carga. | Volumes, identidades distintas, qualidade, contatos, atraso, flags e valores. |
| `mart_qualidade` | Código, campo e severidade na carga inteira. | Ocorrências, registros afetados e denominador. |

Na AWS, `load_id` também identifica a versão de cada mart. No SQLite, cada banco pertence a uma carga. Não somar arquivos ou partições de reprocessamentos como se fossem novas dívidas.

## Valores monetários

As somas preservam os sinais da fonte e ignoram somente valores ausentes. Um grupo inteiramente ausente tem soma NULL; saldo informado igual a zero continua zero. As três medidas têm significados distintos e não devem ser somadas entre si.

| Soma do mart | Campo de origem tratado | Contadores publicados |
|---|---|---|
| `valor_original_centavos` | `valor_original_centavos` | `original_preenchidos`, `original_ausentes`, `original_negativos` |
| `saldo_contabil_centavos` | `valor_contabil_centavos` | `saldos_preenchidos`, `saldos_ausentes`, `saldos_negativos` |
| `valor_face_centavos` | `valor_face_centavos` | `face_preenchidos`, `face_ausentes`, `face_negativos` |

Todos esses valores estão em centavos BRL. Dividir por 100 somente para apresentação. Spark acumula as somas em `DECIMAL(38,0)`. SQLite materializa somas como texto decimal integral por meio de `sum_centavos`, evitando overflow de `SUM(INTEGER)` e perda de precisão em ponto flutuante. Consumidores devem converter o texto para Decimal ou tipo decimal equivalente; não ordenar texto monetário como número nem usar float como representação canônica.

Ticket contábil médio = `saldo_contabil_centavos / saldos_preenchidos`, quando o denominador for positivo. Dividir pela quantidade total de contratos trataria ausências como zero. Negativos participam dessa média; apresentar também sua quantidade. Uma exposição somente positiva seria outra medida e exigiria nome e filtro explícitos.

Os totais são aditivos entre grupos disjuntos do mesmo universo, carga e corte. Saldos não são aditivos entre cortes nem entre os três universos sobrepostos.

## Indicadores da carteira

| Indicador | Definição e leitura |
|---|---|
| `quantidade` | Registros únicos do universo no corte. |
| `devedores_distintos` | Combinações distintas PF/PJ + documento preenchido. Não comprova identidade/titularidade; não somar entre grupos. |
| `cedentes_distintos` | Documentos de cedente distintos e preenchidos. Não somar entre grupos. |
| `aptos`, `revisao`, `quarentena` | Quantidades em cada estado. Percentual = quantidade do estado / `quantidade` no universo escolhido. |
| `pessoas_fisicas`, `pessoas_juridicas` | Contratos classificados PF/PJ, não pessoas distintas. |
| `com_email`, `com_telefone` | Registros com ao menos um contato normalizado e utilizável sintaticamente. Dois contatos no registro contam uma vez. Não medem entrega, titularidade ou contato efetivo. |
| `atraso_calculavel` | Registros com `MAX(data_corte − data_vencimento, 0)` disponível. |
| `atraso_sem_calculo` | Registros sem data utilizável de corte ou vencimento para derivar atraso. |
| `atraso_acima_90` | Registros com atraso calculado estritamente maior que 90 dias. Percentual entre calculáveis = esse indicador / `atraso_calculavel`. |
| `vencimento_apos_corte` | Registros com vencimento posterior ao corte, sinalizados para revisão. O atraso derivado é zero. |
| `garantia_sim`, `garantia_nao`, `garantia_sem_informacao` | Flag de garantia 1, 0 ou sem valor utilizável 0/1. A flag não comprova validade, registro ou valor econômico da garantia. |
| `ajuizado_sim`, `ajuizado_nao`, `ajuizado_sem_informacao` | Flag de ajuizamento 1, 0 ou sem valor utilizável 0/1. Conta contratos, não processos distintos. |

Para cada medida monetária, preenchidos + ausentes = quantidade; negativos são um subconjunto dos preenchidos. Sim + não + sem informação também deve reconciliar com quantidade para cada flag. Universos sem registros em um corte têm contagens zero e somas monetárias NULL.

Uma data de execução mais recente não altera o atraso do contrato: o cálculo usa o corte do arquivo. O atraso informado permanece disponível para comparação. O nome da aba não substitui esse cálculo nem garante que a carteira inteira esteja acima de 90 dias.

## Qualidade e completude

`mart_qualidade` distingue `ocorrencias` de `registros_afetados`. Um contrato pode ter vários problemas no mesmo campo e em vários campos. Percentual afetado por uma regra = `registros_afetados / total_registros`, onde o denominador inclui toda a BASE_TRATADA da carga. O denominador é repetido nas linhas do mart e não pode ser somado.

Não somar registros afetados entre regras para obter contratos ou pessoas com problemas. Para obter esse total, contar registros distintos na tabela de ocorrências. Evitar juntar ocorrências à fato antes de somar saldos: a relação de um para muitos multiplicaria o dinheiro.

Completude monetária = preenchidos / quantidade no mesmo universo e corte. Para métricas futuras de nascimento/renda, o denominador aplicável é PF; para abertura/sócios, PJ. Ausência estrutural de um campo que não se aplica não é erro. O catálogo `METRIC_DEFINITIONS` fornece definição, unidade e denominador usados pela implementação.

## O que esta fonte não permite concluir

`valor_pago_centavos` é um acumulado informado, sem eventos de recebimento datados. Não pode ser tratado como arrecadação do mês, fluxo de caixa, taxa de recuperação ou diferença obrigatória entre dívida original e saldo. Quantidades de parcelas também não criam transações de pagamento inexistentes.

Sem histórico vinculado de pagamentos, custos, ações de cobrança, resultados e verificação jurídica/garantias, a fotografia não fundamenta preço de aquisição, retorno esperado, probabilidade de recuperação ou modelo preditivo validado. Uma única data de corte não fornece uma tendência temporal. Reprocessar o mesmo arquivo com código novo gera outra versão técnica, não outra observação econômica.

## Validação das definições

`tests/test_metrics.py` usa dados sintéticos para verificar nulos versus zero, preservação de negativos, soma acima de 64 bits, distinção contrato/devedor, limites de atraso, flags desconhecidas, universos vazios e registros distintos afetados. Execute `python3 -m unittest discover -s tests -p test_metrics.py -v`.
