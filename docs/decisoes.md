# Decisões de tratamento e limites

Este documento descreve os critérios da pipeline. As contagens efetivas de cada ocorrência pertencem ao relatório gerado na execução. O enunciado é a referência do teste; os critérios abaixo são decisões de implementação para produzir um resultado reproduzível e explicar ambiguidades.

## Princípios

1. Preservar o original e a posição de origem antes de alterar um valor.
2. Corrigir formatos quando a interpretação for suficiente; sinalizar quando houver escolha ou dúvida.
3. Não completar documentos, nomes, rendas, saldos ou datas com valores inventados.
4. Distinguir ausência legítima, dado inválido e campo que não se aplica ao tipo de pessoa.
5. Disponibilizar métricas com estado de qualidade visível e uma visão de aptos separada.

## Regras e justificativas

| Problema | Decisão | Motivo e limite |
|---|---|---|
| Cabeçalhos repetidos | Mapear as colunas pela posição e pelo bloco; preservar os títulos originais. | O título isolado não identifica a função da coluna. |
| Espaços, caixa e caracteres equivalentes | Remover espaços excedentes, normalizar Unicode e padronizar caixa conforme o campo. | Uniformiza representação; não prova que textos parecidos representam a mesma entidade. |
| Marcadores de ausência | Converter sentinelas documentadas para nulo. | `NULL`, `N/A` e célula vazia não são categorias de negócio. Um nulo não se transforma automaticamente em zero ou “não”. |
| CPF, CNPJ e CEP formatados | Manter como texto e retirar a formatação admitida. | Preserva zeros à esquerda. Não preencher com zeros um documento truncado. |
| Documento com tamanho ou dígito verificador inválido | Registrar o problema e preservar o original na Bronze. Formato/comprimento inválido pode resultar em nulo na Silver; documento legível com DV inválido permanece com indicador de validação reprovado. | A validação é local e não consulta cadastro oficial. Identidade não resolvida é impeditiva; DV inválido gera aviso de revisão. Não trocar ou completar dígitos para obter um documento válido. |
| Tipo PF/PJ em formatos diferentes | Padronizar aliases documentados para PF ou PJ e confrontar o tipo com os documentos. | Uma classificação textual pode estar errada; informação conflitante não deve ser resolvida apenas pela ordem das colunas. |
| Dados de PF em registro PJ e vice-versa | Na representação tratada, manter somente os atributos aplicáveis ao tipo conciliado; registrar a ocorrência e preservar o conteúdo original na Bronze. | Evita atribuir nascimento, sexo ou renda de pessoa física à empresa, e razão social ou sócios ao devedor PF. A empresa do bloco PF pode ser empregador, sem se tornar razão social do devedor. |
| Documento, contrato ou cedente repetido em blocos | Comparar os valores após normalização e registrar divergências. | Colunas repetidas são evidências cruzadas; discordâncias de identidade não são meras diferenças de formato. |
| Nomes repetidos | Normalizar para comparação e usar precedência explícita: E/Z/BT para PF e Q/Z/BT para PJ, registrando discordâncias. | Não corrigir por proximidade textual e não usar nome como chave. Para nomes diferentes do mesmo documento entre registros, gerar aviso e manter os valores por registro na Silver; a dimensão mantém o nome quando existe um único nome não nulo e deixa-o nulo em caso de conflito. |
| Valores monetários BR/US | Interpretar os separadores quando houver regra suficiente e converter por aritmética decimal para centavos inteiros. | Evita erros de ponto flutuante. Um separador isolado ambíguo deve gerar ocorrência em vez de um palpite silencioso. |
| Resíduos numéricos do Excel | Após falhar a leitura estrita, aceitar somente decimal com ponto e ao menos seis casas cuja distância ao centavo seja de no máximo `1E-8` real. Registrar `RESIDUO_NUMERICO_EXCEL_AJUSTADO` como INFO. | Corrige ruído mínimo da representação numérica sem arredondar valores com precisão real adicional. A comparação é decimal e o valor original continua rastreável. |
| Valores entre parênteses ou com sinal negativo | Preservar o sinal e sinalizar valor incompatível com o uso esperado. | `(123,45)` significa valor negativo na leitura contábil. Aplicar `abs()` mudaria a exposição sem evidência. |
| Booleanos em várias grafias | Padronizar aliases reconhecidos para 1/0 e manter ausência como nulo. | “Não informado” não significa “não”. Grafia desconhecida exige revisão. |
| Datas em vários formatos | Converter para ISO `AAAA-MM-DD`; considerar o calendário Excel indicado pelo arquivo. | Datas devem ser comparáveis e a origem precisa continuar auditável. |
| Datas numéricas ambíguas | No contrato desta fonte, interpretar hífen numérico como mês-dia-ano (MDY), barras e pontos como dia-mês-ano (DMY), e ISO como ano-mês-dia (YMD). | A análise encontrou 2.630 datas com hífen cuja ordem só pode ser MDY. Essa evidência fundamenta uma configuração explícita da fonte; não há inferência por maioria durante cada carga. Assim, `08-01-2026` representa 1º de agosto. A convenção precisa ser reavaliada se mudar a origem. |
| Anos com dois dígitos | Usar pivô fixo 30: 00–29 representam 2000–2029; 30–99 representam 1930–1999. | Garante reprodução futura, sem depender do ano atual. A data resultante continua sujeita às verificações de coerência. |
| Data impossível ou incompatível com a cronologia | Registrar a inconsistência e direcionar para revisão conforme a criticidade. | Não inverter dia/mês ou alterar o ano apenas para fazer a cronologia funcionar. |
| Atraso informado divergente das datas | Manter o atraso da fonte e calcular outra coluna a partir de corte e vencimento. | O consumidor consegue comparar as duas medidas; o cálculo depende da validade dessas datas. |
| Ausência de renda ou saldo | Manter nulo e expor completude. | Média, mediana e zero são hipóteses diferentes e afetariam análises financeiras. |
| Total e restantes de parcelas ausentes | Se o total faltar e o plano for não negativo, copiar o plano; se restantes faltar e `0 ≤ pagas ≤ plano`, derivar `plano − pagas`. Registrar ambas as recuperações como INFO. | São relações aritméticas explícitas, não imputação estatística. Quando as condições não forem atendidas, manter nulo. Valores já preenchidos não são substituídos. |
| Quantidades de parcelas incoerentes | Verificar totais, pagas e restantes quando os componentes estiverem disponíveis; sinalizar conflitos. | Não ajustar a prestação ou saldo para forçar igualdade. Parcelamento e pagamentos podem obedecer regras ausentes da fonte. |
| Total pago versus quantidade paga × parcela | Não impor essa igualdade e não substituir valores informados. A implementação não marca a diferença isolada como erro. | Juros, descontos, renegociação e pagamentos parciais podem explicar diferenças; o cronograma financeiro não está disponível. |
| Telefone e e-mail | Normalizar formatos reconhecidos e validar estrutura. | Formato válido não prova titularidade nem capacidade de contato. Não acrescentar dígito de celular por suposição. |
| E-mail com Unicode e caixa diferente | Preservar letras Unicode da parte local e registrar `EMAIL_REQUER_SMTPUTF8` como INFO; normalizar o domínio por IDNA. Aplicar lowercase ao endereço como convenção desta fonte para comparação e deduplicação. | Não remover acentos. O lowercase da parte local é uma decisão do contrato desta fonte, não uma equivalência universal entre caixas postais. O aviso indica requisito técnico de envio, não comprovação de entrega. |
| Estado, cidade e CEP | Padronizar formato; sinalizar inconsistências identificáveis sem inventar localidade. | Sem cadastro geográfico de referência, não afirmar que CEP e município foram confirmados. |
| Avalista, garantia e dados judiciais | Verificar coerência entre indicadores e detalhes; preservar evidências e marcar conflitos. | Ter número de processo não autoriza reescrever silenciosamente o indicador “ajuizado”. Ausência de detalhe não comprova inexistência. |

## Duplicidades

A detecção deve distinguir repetição exata da linha original, repetição que se torna idêntica após normalização e colisão da chave de negócio com informações incompatíveis. As duas primeiras podem ser reduzidas a uma representação, mantendo o vínculo com as linhas de origem e a contagem das repetições.

Nesta fonte, a expectativa conferida na exploração e na carga é de 10.740 linhas para 10.500 registros tratados: 240 repetições removidas. A diferença de caixa do e-mail faz parte da normalização adotada. Os detalhes de repetições exatas e por normalização pertencem ao resumo executado.

Quando a mesma combinação de cedente, contrato e corte possui versões incompatíveis, não há informação temporal ou de autoridade suficiente para eleger uma delas. O grupo deve ir para quarentena e sair da fato publicada. Escolher o primeiro, o último ou o registro mais completo poderia alterar um saldo ou uma identidade sem justificativa. O código identificador da planilha também não deve ser considerado prova de unicidade sem validação.

A exploração identificou três grupos dessa chave com devedores diferentes, envolvendo seis identificadores. Uma alternativa seria acrescentar o devedor à chave, mas ela depende de homologação do cedente; a implementação adota a hipótese conservadora e torna a colisão visível.

## Achados versus regras preventivas

Os documentos CPF/CNPJ presentes na fonte passaram pela validação local dos dígitos verificadores. Isso não comprova existência cadastral nem titularidade. Não foi encontrada contaminação dos blocos específicos PF/PJ na exploração. As verificações desses problemas permanecem na pipeline para proteger cargas futuras; não devem ser apresentadas como defeitos efetivamente observados nesta base.

Datas e valores misturados, ausências, repetições, saldos negativos e inconsistências entre campos relacionados são tratados segundo as regras acima. O atraso recalculado é registrado como informação derivada; a divergência mantém o atraso original disponível. Valores monetários negativos mantêm o sinal e geram aviso de revisão.

## Estados de qualidade

| Estado | Significado operacional | Consumo |
|---|---|---|
| `APTO` | Não possui WARNING nem ERROR; pode ter ocorrências INFO. | Pode integrar `vw_precificacao`; ainda depende de validação de negócio antes de uma decisão financeira. |
| `REVISAO` | Possui WARNING e nenhum ERROR. | Manter o estado exposto em agregações; investigar antes de usar os campos afetados. |
| `QUARENTENA` | Possui ao menos um ERROR: identidade/chave não resolvida, conflito de versões ou outra falha impeditiva. | Disponível para saneamento e auditoria; excluído da fato publicada. |

A quantidade de aptos pode ser baixa ou zero. Isso é um resultado informativo do contrato de qualidade, e não um motivo para relaxar regras ou declarar uma base pronta para precificação sem evidências.

O atraso recalculado, a recuperação consistente de coluna redundante, as derivações de parcelas, o pequeno resíduo numérico do Excel e o requisito SMTPUTF8 são INFO. Valor monetário negativo é WARNING. Divergência de chave/identidade é ERROR. Essa distinção evita tratar uma transformação explicada como se fosse necessariamente um bloqueio.

## Exportação e interpretação

Os CSVs são gravados em UTF-8 com BOM e delimitador `;`. Textos que começam com `=`, `+`, `-`, `@`, tabulação ou retorno de carro recebem apóstrofo inicial para não serem executados como fórmulas em planilhas. O SQLite guarda o texto canônico, sem esse prefixo. Essa convenção afeta somente textos na exportação; números monetários negativos mantêm seu sinal.

O relatório HTML mostra apenas agregados, não nomes, documentos ou contatos dos devedores. Arquivos individuais vinculados ao relatório e a linhagem devem ser compartilhados com acesso controlado.

## Reconciliação e evidência

A execução deve permitir explicar a diferença entre quantidade de linhas recebidas, repetições removidas, registros únicos, registros em quarentena e linhas publicadas. Métricas de qualidade precisam informar o denominador: “linhas com problema” e “ocorrências” são medidas diferentes, pois uma linha pode violar várias regras.

As somas de saldos devem considerar separadamente nulos, valores negativos e estados de qualidade. A soma de uma coluna não valida o significado do saldo, e uma linha duplicada não deve aumentar a exposição. Para comparar Bronze e Silver, separar efeitos de parsing, deduplicação, quarentena e eventual exclusão do consumo.

O relatório gerado mostra os resultados observados. Este documento registra o porquê das decisões; qualquer exceção futura deve atualizar a regra, a documentação e os casos de verificação associados.
