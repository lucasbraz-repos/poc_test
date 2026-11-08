"""Contrato posicional da fonte; cabeçalhos repetidos nunca viram chaves de dict."""

COLUMNS = {
    "A": "Código Identificador", "B": "CNPJ Cedente", "C": "CPF Devedor",
    "D": "Número do Contrato (Número identificador)", "E": "Nome Devedor",
    "F": "Data de Nascimento", "G": "Empresa", "H": "Sexo", "I": "Estado Civil",
    "J": "Profissão", "K": "Nome da Mãe", "L": "Óbito (S/N)", "M": "Renda",
    "N": "CNPJ Cedente", "O": "CNPJ Devedor", "P": "Número do Contrato (Número Identificador)",
    "Q": "Razão Social", "R": "Data abertura empresa", "S": "Situação da Empresa (Ativa/Baixada)",
    "T": "CPF do Sócio 1", "U": "Nome do Sócio 1", "V": "CPF do Sócio 2", "W": "Nome do Sócio 2",
    "X": "CPF/CNPJ Devedor", "Y": "Tipo de Pessoa (PF/PJ)", "Z": "Nome do Devedor",
    "AA": "Rua", "AB": "Número", "AC": "Complemento", "AD": "Bairro", "AE": "Cidade",
    "AF": "Estado", "AG": "CEP", "AH": "País",
    "AI": "Tipo de telefone (comercial, residencial, celular?) Sócio? Da empresa? Do avalista?",
    "AJ": "Código área (DDD)", "AK": "Nº Telefone",
    "AL": "Tipo de telefone (comercial, residencial, celular?) Sócio? Da empresa? Do avalista?",
    "AM": "Código área DDD", "AN": "Nº Telefone", "AO": "e-mail 1", "AP": "e-mail 2",
    "AQ": "CPF/CNPJ", "AR": "Número do Contrato (Número Identificador)",
    "AS": "Produto (categoria)", "AT": "Subproduto (marca)",
    "AU": "Data Contratação ou Data da Venda", "AV": "Data de Vencimento (1 pcl vcida)",
    "AW": "Valor Divida Original", "AX": "Valor Contábil", "AY": "Valor da divida no Vencimento (Valor de Face)",
    "AZ": "Origem do Crédito ou Nº Nota Fiscal", "BA": "Ajuizado (Sim/Não)", "BB": "Protesto (Sim/Não)",
    "BC": "Negativado no SCPC Boa vista e/ou Serasa (Sim/Não)", "BD": "Serasa Limpa Nome (Sim/Não)",
    "BE": "Programa Desenrola (Sim/Não)", "BF": "Quantidade dias em atraso", "BG": "Qtde de Parcelas (Plano)",
    "BH": "Valor da Parcela", "BI": "Qtde de Parcelas Pagas", "BJ": "Valor Total Pago", "BK": "Percelas Restantes",
    "BL": "Total Parcelas", "BM": "Possui Avalista (Sim/Não)", "BN": "CPF/CNPJ do Avalista",
    "BO": "Possui Garantia (Sim/Não)", "BP": "Tipo de Garantia", "BQ": "Registro da Garantia (Sim/Não)",
    "BR": "CPF/CNPJ Devedor", "BS": "Número do Contrato (Número identificador)", "BT": "Nome Devedor",
    "BU": "Numero Processo", "BV": "Comarca", "BW": "Tipo Ação", "BX": "Valor Ação", "BY": "Data Ajuizamento",
    "BZ": "Ultimo andamento processual", "CA": "Data de Corte da Base de Dados",
}

RULE_VERSION = "1.0.0"

# Tipos e projeção compartilhados entre SQLite local e Spark/Parquet na AWS.
INTEGER_FIELDS = set("dias_atraso_informado dias_atraso_calculado parcelas_plano parcelas_pagas parcelas_restantes parcelas_total obito ajuizado protesto negativado serasa_limpa_nome desenrola possui_avalista possui_garantia garantia_registrada documento_devedor_valido cnpj_cedente_valido linhas_origem quantidade_alertas".split())
FACT_FIELDS = "codigo_identificador numero_contrato tipo_pessoa produto subproduto uf data_corte data_contratacao data_vencimento data_ajuizamento data_nascimento data_abertura sexo estado_civil profissao situacao_empresa obito renda_centavos valor_original_centavos valor_contabil_centavos valor_face_centavos valor_parcela_centavos valor_pago_centavos valor_acao_centavos dias_atraso_informado dias_atraso_calculado faixa_atraso parcelas_plano parcelas_pagas parcelas_restantes parcelas_total ajuizado protesto negativado serasa_limpa_nome desenrola possui_avalista possui_garantia tipo_garantia garantia_registrada".split()


def validate_headers(headers):
    normalized = lambda value: " ".join(str(value or "").split()).casefold()
    differences = {k: {"esperado": v, "recebido": headers.get(k)} for k, v in COLUMNS.items()
                   if normalized(v) != normalized(headers.get(k))}
    extra = sorted(set(headers) - set(COLUMNS))
    if differences or extra:
        raise ValueError(f"Layout da planilha alterado; revise o contrato: {differences}; extras={extra}")
