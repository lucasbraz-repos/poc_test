# Investigação independente da fonte

A planilha tem uma aba (`Base_Randon_Over90`), 79 colunas e 10.740 registros. Os números desta nota distinguem registros originais de entidades distintas por código identificador.

## Duplicidades e chaves

- Existem 10.500 identificadores únicos: 6.400 PF e 4.100 PJ.
- Há 240 excedentes em 235 grupos de identificadores repetidos; 137 excedentes são cópias exatas. As demais diferenças limitam-se à capitalização do primeiro e-mail.
- O contrato aparece em D/P/AR/BS, e o documento principal em X/AQ/BR e C ou O. Essas redundâncias não apresentam divergências quando preenchidas. Cedentes B/N também são consistentes.
- Após recuperar valores redundantes, nenhum contrato ou documento principal fica ausente; 13 identificadores ainda não têm cedente.
- O contrato isolado tem oito colisões entre identificadores. Cedente+contrato+corte ainda tem três colisões, sempre entre devedores diferentes. Incluir documento do devedor na chave é uma alternativa que exige homologação. A pipeline mantém a chave conservadora e coloca esses seis registros em quarentena, preservando o identificador de origem.

## PF/PJ e documentos

- Não há contaminação real entre blocos PF e PJ: campos C:M não aparecem em PJ, e O:W não aparecem em PF.
- N e P não são atributos exclusivos de PJ: repetem cedente e contrato. G é empresa empregadora de PF.
- Todos os CPFs e CNPJs presentes têm comprimentos corretos e dígitos verificadores válidos. Isso não comprova existência ou titularidade.
- Ausências explícitas são representadas por S/D, N/A, NULL, vazios e espaços; faltas estruturais de PF/PJ, segundo sócio, contatos opcionais, processo e garantia não são defeitos por si só.

## Datas

Existem oito representações: ISO, serial Excel, dia/mês/ano com dois ou quatro dígitos no ano, dia.mês.ano, dia-Mon-ano, texto em português e mês-dia-ano numérico.

A interpretação de hífen numérico como MM-DD-YYYY é sustentada em todas as colunas: nenhum caso inequívoco tem primeiro componente acima de 12; 2.630 ocorrências inequívocas têm segundo componente acima de 12 (F 443, R 298, AU 760, AV 767, BY 362). A fonte usa `08-01-2026` para a mesma data de corte representada pelos outros formatos como 1º de agosto de 2026. Deve-se documentar essa regra da fonte, não usar seleção automática genérica de formato.

A fonte tem nascimento com anos abreviados 55..99, além de 00..05, e abertura com 98/99 e 00..22. O pivot padrão de `%y` gera datas futuras para nascimento 55..68; é necessário pivot explícito (por exemplo, 30).

## Coerência, valores e preservação

Entre os 10.500 identificadores deduplicados:

- 9.497 de 9.500 atrasos informados comparáveis divergem de corte menos vencimento. Preservar o declarado e disponibilizar atraso calculado separadamente.
- 511 vencimentos são posteriores ao corte; portanto o nome da aba Over90 não deve determinar o filtro sem cálculo e validação.
- 321 aberturas de PJ ocorrem após contratação e 1.442 ajuizamentos antecedem contratação. Sinalizar para revisão, sem ajustar silenciosamente datas.
- Plano de parcelas = pagas + restantes em todos os 8.984 casos completos, e plano = total em todos os 9.481 casos comparáveis.
- Existem 157 indicações de avalista sem documento e seis indicações de garantia sem tipo nem registro.
- Negativos reais em parênteses ocorrem em renda (616), original (1.073), contábil (1.131), face (1.137), parcela (1.044), total pago (825) e ação (603). Interpretar o sinal corretamente e sinalizar; `abs` alteraria a informação de origem.
- Valores monetários misturam convenções brasileira e americana, além de `R$`. Numerais XML trazem por vezes ruído binário de ponto flutuante; a pipeline recupera centavos via Decimal somente para resíduos de até `1E-8` real e seis ou mais casas, registrando cada ajuste.
- Todos os 10.500 primeiros e-mails têm um @ e nenhum espaço, mas 3.334 usam Unicode; 1.002 dos 3.099 segundos e-mails presentes também usam Unicode. Não remover acentos do endereço automaticamente.

Os arquivos `profile_source.json` e `relationships_source.json` são evidências exploratórias; contêm valores da fonte e não devem ser publicados como relatórios anônimos.
