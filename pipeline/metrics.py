"""Definições e SQL dos produtos analíticos compartilhados entre Spark e SQLite.

As consultas recebem dados de uma única carga. Os marts preservam centavos,
nulos e sinais; o consumidor escolhe o corte e o estado de qualidade.
"""

from __future__ import annotations

import re
from decimal import Decimal, localcontext

# origem, soma publicada e prefixo dos contadores de disponibilidade/sinal
_MONEY = (
    ("valor_original_centavos", "valor_original_centavos", "original"),
    ("valor_contabil_centavos", "saldo_contabil_centavos", "saldos"),
    ("valor_face_centavos", "valor_face_centavos", "face"),
)
_FINANCIAL_SCHEMA = {}
for _source, _amount, _prefix in _MONEY:
    _FINANCIAL_SCHEMA[_amount] = "decimal(38,0)"
    _FINANCIAL_SCHEMA.update(
        {f"{_prefix}_{suffix}": "bigint" for suffix in ("preenchidos", "ausentes", "negativos")}
    )

_INDICATOR_COUNTS = (
    "quantidade",
    "devedores_distintos",
    "cedentes_distintos",
    "aptos",
    "revisao",
    "quarentena",
    "pessoas_fisicas",
    "pessoas_juridicas",
    "com_email",
    "com_telefone",
    "atraso_calculavel",
    "atraso_sem_calculo",
    "atraso_acima_90",
    "vencimento_apos_corte",
    "garantia_sim",
    "garantia_nao",
    "garantia_sem_informacao",
    "ajuizado_sim",
    "ajuizado_nao",
    "ajuizado_sem_informacao",
)
MART_SCHEMAS = {
    "mart_resumo_carteira": {
        **{
            name: "string"
            for name in ("data_corte", "tipo_pessoa", "produto", "faixa_atraso", "status_qualidade")
        },
        "quantidade": "bigint",
        **_FINANCIAL_SCHEMA,
    },
    "mart_indicadores_carteira": {
        "universo": "string",
        "data_corte": "string",
        **{name: "bigint" for name in _INDICATOR_COUNTS},
        **_FINANCIAL_SCHEMA,
    },
    "mart_qualidade": {
        "codigo": "string",
        "campo": "string",
        "severidade": "string",
        "ocorrencias": "bigint",
        "registros_afetados": "bigint",
        "total_registros": "bigint",
    },
}


def _definition(text, unit="registros", denominator="quantidade do mesmo universo e corte"):
    return {"definicao": text, "unidade": unit, "denominador": denominator}


METRIC_DEFINITIONS = {
    "universo": _definition(
        "BASE_TRATADA inclui todos os registros únicos; CARTEIRA_PUBLICADA exclui QUARENTENA; APTOS inclui somente APTO.",
        "categoria",
        "não se aplica",
    ),
    "data_corte": _definition(
        "Data da fotografia informada na fonte; diferente do horário de publicação da carga.",
        "data ISO",
        "não se aplica",
    ),
    "tipo_pessoa": _definition(
        "Classificação normalizada PF/PJ do devedor no contrato.", "categoria", "não se aplica"
    ),
    "produto": _definition(
        "Categoria de produto normalizada da fonte; ausência permanece NULL.",
        "categoria",
        "não se aplica",
    ),
    "faixa_atraso": _definition(
        "Faixa do atraso calculado no corte; sem cálculo quando falta data utilizável.",
        "categoria",
        "não se aplica",
    ),
    "status_qualidade": _definition(
        "APTO, REVISAO ou QUARENTENA conforme a maior severidade das ocorrências do registro.",
        "categoria",
        "não se aplica",
    ),
    "codigo": _definition(
        "Identificador da regra de qualidade que gerou a ocorrência.", "categoria", "não se aplica"
    ),
    "campo": _definition(
        "Campo tratado ao qual a ocorrência se refere.", "categoria", "não se aplica"
    ),
    "severidade": _definition(
        "INFO, WARNING ou ERROR; determina a classificação de qualidade do registro.",
        "categoria",
        "não se aplica",
    ),
    "quantidade": _definition(
        "Registros únicos no grão da carteira, após deduplicação; não é quantidade de pessoas.",
        denominator="não se aplica",
    ),
    "devedores_distintos": _definition(
        "Identidades documentais distintas por tipo PF/PJ e documento preenchido; validação local não comprova titularidade.",
        "identidades documentais",
        "não se aplica; não aditiva entre grupos",
    ),
    "cedentes_distintos": _definition(
        "Documentos de cedente distintos e preenchidos no universo.",
        "cedentes documentais",
        "não se aplica; não aditiva entre grupos",
    ),
    "aptos": _definition(
        "Registros APTO: sem ocorrência WARNING ou ERROR; não determina preço, recuperação ou aprovação."
    ),
    "revisao": _definition("Registros REVISAO: com WARNING e sem ERROR."),
    "quarentena": _definition("Registros QUARENTENA: com ERROR, excluídos da fato publicada."),
    "pessoas_fisicas": _definition(
        "Registros classificados como PF; uma pessoa com vários contratos aparece várias vezes."
    ),
    "pessoas_juridicas": _definition(
        "Registros classificados como PJ; uma empresa com vários contratos aparece várias vezes."
    ),
    "com_email": _definition(
        "Registros com ao menos um e-mail normalizado e utilizável sintaticamente. Não comprova entrega, titularidade nem consentimento."
    ),
    "com_telefone": _definition(
        "Registros com ao menos um telefone normalizado e utilizável sintaticamente. Não comprova contato ou titularidade."
    ),
    "atraso_calculavel": _definition(
        "Registros com atraso derivado disponível: MAX(data_corte − data_vencimento, 0)."
    ),
    "atraso_sem_calculo": _definition(
        "Registros sem atraso derivado por falta de data utilizável de corte ou vencimento."
    ),
    "atraso_acima_90": _definition(
        "Registros cujo atraso calculado no corte é estritamente maior que 90 dias.",
        denominator="atraso_calculavel do mesmo universo e corte",
    ),
    "vencimento_apos_corte": _definition(
        "Registros com vencimento posterior ao corte; não são classificados como atrasados pelo cálculo."
    ),
    "ocorrencias": _definition(
        "Ocorrências da combinação código/campo/severidade. Um registro pode produzir várias ocorrências.",
        "ocorrências",
        "não interpretar como pessoas ou percentual de registros",
    ),
    "registros_afetados": _definition(
        "Registros distintos com a combinação código/campo/severidade; não somar entre regras para obter pessoas.",
        denominator="total_registros",
    ),
    "total_registros": _definition(
        "Registros únicos de toda a BASE_TRATADA da carga, incluindo quarentena; repetido como denominador em cada linha do mart de qualidade.",
        denominator="não se aplica; não somar entre linhas do mart",
    ),
}
for _name, _source in (("garantia", "possui_garantia"), ("ajuizado", "ajuizado")):
    for _suffix, _value in (
        ("sim", "1"),
        ("nao", "0"),
        ("sem_informacao", "nenhum valor utilizável 0/1"),
    ):
        METRIC_DEFINITIONS[f"{_name}_{_suffix}"] = _definition(
            f"Registros com {_source} = {_value}. Indicador informado, sem confirmação externa."
        )
for _source, _amount, _prefix in _MONEY:
    METRIC_DEFINITIONS[_amount] = _definition(
        f"Soma dos {_source} preenchidos, preservando negativos; NULL quando nenhum valor está disponível.",
        "centavos BRL",
        "não se aplica; não somar cortes ou versões da mesma carteira",
    )
    for _suffix, _condition in (
        ("preenchidos", "preenchido, incluindo zero"),
        ("ausentes", "ausente"),
        ("negativos", "menor que zero"),
    ):
        METRIC_DEFINITIONS[f"{_prefix}_{_suffix}"] = _definition(
            f"Registros com {_source} {_condition}."
        )


class CentSum:
    """Soma inteira exata para SQLite sem overflow de SUM(INTEGER).

    O retorno é texto decimal integral, pois o adaptador SQLite limita inteiros
    a 64 bits. Consumidores convertem esse texto para Decimal, nunca float.
    """

    def __init__(self):
        self.total = 0
        self.seen = False

    def step(self, value):
        if value is None:
            return
        if not isinstance(value, (int, str)) or isinstance(value, bool):
            raise ValueError("Centavos devem ser inteiros ou texto decimal integral.")
        self.total += int(value)
        self.seen = True

    def finalize(self):
        return str(self.total) if self.seen else None


def register_sqlite_aggregates(connection):
    """Registra a soma exata na conexão usada para materializar os marts."""
    connection.create_aggregate("sum_centavos", 1, CentSum)


def safe_ratio(numerator, denominator):
    """Retorna fração Decimal; ausência ou denominador não positivo vira None."""
    if numerator is None or denominator is None:
        return None
    if isinstance(numerator, float) or isinstance(denominator, float):
        raise TypeError("Use inteiros, strings decimais ou Decimal para calcular razões.")
    with localcontext() as context:
        context.prec = 50
        base = Decimal(denominator)
        return Decimal(numerator) / base if base > 0 else None


def metric_dictionary():
    """Dicionário exportável com tipo, unidade, definição e denominador."""
    for table, columns in MART_SCHEMAS.items():
        for name, kind in columns.items():
            yield {"dataset": table, "campo": name, "tipo": kind, **METRIC_DEFINITIONS[name]}


def _financial_expressions(engine, alias="", present=None):
    expressions = []
    for source, amount, prefix in _MONEY:
        field = f"{alias}{source}"
        total = (
            f"SUM(CAST({field} AS DECIMAL(38,0)))"
            if engine == "spark"
            else f"sum_centavos({field})"
        )
        expressions.append(f"{total} AS {amount}")
        for suffix, condition in (
            ("preenchidos", f"{field} IS NOT NULL"),
            ("ausentes", f"{field} IS NULL"),
            ("negativos", f"{field} < 0"),
        ):
            if present:
                condition = f"{present} AND ({condition})"
            expressions.append(f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END) AS {prefix}_{suffix}")
    return expressions


def analytics_queries(
    engine="spark", records="_metrics_records", facts="_metrics_facts", issues="_metrics_issues"
):
    """SQL determinístico para materializar os três marts de uma carga.

    SQLite requer register_sqlite_aggregates antes de executar as consultas.
    Os nomes recebidos são identificadores de tabelas/views, não SQL livre.
    """
    if engine not in {"spark", "sqlite"}:
        raise ValueError("Engine de métricas deve ser spark ou sqlite.")
    for name in (records, facts, issues):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", name):
            raise ValueError(f"Identificador de origem inválido: {name!r}")

    grain = "data_corte, tipo_pessoa, produto, faixa_atraso, status_qualidade"
    summary_select = [grain, "COUNT(*) AS quantidade", *_financial_expressions(engine)]
    summary = f"SELECT {', '.join(summary_select)} FROM {facts} GROUP BY {grain}"

    conditions = {
        "aptos": "r.status_qualidade = 'APTO'",
        "revisao": "r.status_qualidade = 'REVISAO'",
        "quarentena": "r.status_qualidade = 'QUARENTENA'",
        "pessoas_fisicas": "r.tipo_pessoa = 'PF'",
        "pessoas_juridicas": "r.tipo_pessoa = 'PJ'",
        "com_email": "NULLIF(TRIM(r.email_1), '') IS NOT NULL OR NULLIF(TRIM(r.email_2), '') IS NOT NULL",
        "com_telefone": "NULLIF(TRIM(r.telefone_1), '') IS NOT NULL OR NULLIF(TRIM(r.telefone_2), '') IS NOT NULL",
        "atraso_calculavel": "r.dias_atraso_calculado IS NOT NULL",
        "atraso_sem_calculo": "r.dias_atraso_calculado IS NULL",
        "atraso_acima_90": "r.dias_atraso_calculado > 90",
        "vencimento_apos_corte": "r.data_vencimento > r.data_corte",
    }
    for prefix, field in (("garantia", "possui_garantia"), ("ajuizado", "ajuizado")):
        conditions.update(
            {
                f"{prefix}_sim": f"r.{field} = 1",
                f"{prefix}_nao": f"r.{field} = 0",
                f"{prefix}_sem_informacao": f"r.{field} IS NULL OR r.{field} NOT IN (0, 1)",
            }
        )
    present = "r.registro_id IS NOT NULL"
    indicators_select = [
        "u.universo",
        "u.data_corte",
        "COUNT(r.registro_id) AS quantidade",
        "COUNT(DISTINCT (r.tipo_pessoa || ':' || r.documento_devedor)) AS devedores_distintos",
        "COUNT(DISTINCT r.cnpj_cedente) AS cedentes_distintos",
        *[
            f"SUM(CASE WHEN {present} AND ({condition}) THEN 1 ELSE 0 END) AS {name}"
            for name, condition in conditions.items()
        ],
        *_financial_expressions(engine, "r.", present),
    ]
    indicators = f"""WITH universos AS (
        SELECT 'BASE_TRATADA' AS universo UNION ALL SELECT 'CARTEIRA_PUBLICADA' UNION ALL SELECT 'APTOS'
    ), cortes AS (SELECT DISTINCT data_corte FROM {records}),
    escopos AS (SELECT universo, data_corte FROM universos CROSS JOIN cortes)
    SELECT {", ".join(indicators_select)}
    FROM escopos u LEFT JOIN {records} r
      ON (r.data_corte = u.data_corte OR (r.data_corte IS NULL AND u.data_corte IS NULL))
     AND (u.universo = 'BASE_TRATADA'
          OR (u.universo = 'CARTEIRA_PUBLICADA' AND r.status_qualidade <> 'QUARENTENA')
          OR (u.universo = 'APTOS' AND r.status_qualidade = 'APTO'))
    GROUP BY u.universo, u.data_corte"""

    quality = f"""SELECT q.codigo, q.campo, q.severidade, COUNT(*) AS ocorrencias,
        COUNT(DISTINCT q.registro_id) AS registros_afetados, t.total_registros
    FROM {issues} q CROSS JOIN (SELECT COUNT(*) AS total_registros FROM {records}) t
    GROUP BY q.codigo, q.campo, q.severidade, t.total_registros"""
    return {
        "mart_resumo_carteira": summary,
        "mart_indicadores_carteira": indicators,
        "mart_qualidade": quality,
    }
