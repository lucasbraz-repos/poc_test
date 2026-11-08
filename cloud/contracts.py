"""Contratos físicos comuns ao Spark, Glue Data Catalog e Terraform."""
from pipeline.cleaning import Cleaner, fingerprint
from pipeline.schema import FACT_FIELDS, INTEGER_FIELDS


def fields():
    return {name: ("bigint" if name.endswith("_centavos") or name in INTEGER_FIELDS else "string")
            for name in Cleaner({}).run()[0]}


BRONZE = {"source_sha256": "string", "source_bucket": "string", "source_key": "string",
          "source_version_id": "string", "sheet": "string", "source_row": "bigint",
          "raw_json": "string", "formulas_json": "string", "date_1904": "boolean"}
SILVER = {"registro_id": "string", **fields(), "status_qualidade": "string",
          "linhas_origem": "bigint", "quantidade_alertas": "bigint"}
ISSUES = {"ocorrencia_id": "string", "registro_id": "string", "codigo": "string", "campo": "string",
          "severidade": "string", "detalhe_json": "string"}
LINEAGE = {"source_sha256": "string", "source_bucket": "string", "source_key": "string",
           "source_version_id": "string", "sheet": "string", "source_row": "bigint",
           "hash_linha_original": "string", "registro_id": "string"}
FACT = {"registro_id": "string", "cedente_id": "string", "devedor_id": "string", "produto_id": "string",
        **{name: fields()[name] for name in FACT_FIELDS}, "status_qualidade": "string"}
TABLES = {
    "linhas_originais": ("bronze", BRONZE),
    "registros_tratados": ("silver", SILVER),
    "ocorrencias_qualidade": ("silver", ISSUES),
    "linhagem_origem": ("silver", LINEAGE),
    "quarentena": ("silver", SILVER),
    "fato_carteira": ("gold", FACT),
    "dim_cedente": ("gold", {"cedente_id": "string", "cnpj": "string", "documento_valido": "bigint"}),
    "dim_devedor": ("gold", {"devedor_id": "string", "documento": "string", "tipo_pessoa": "string", "nome": "string", "documento_valido": "bigint"}),
    "dim_produto": ("gold", {"produto_id": "string", "produto": "string", "subproduto": "string"}),
    "dim_data": ("gold", {"data": "string", "ano": "bigint", "mes": "bigint", "dia": "bigint", "trimestre": "bigint"}),
    "mart_resumo_carteira": ("gold", {"data_corte": "string", "tipo_pessoa": "string", "produto": "string", "faixa_atraso": "string", "status_qualidade": "string", "quantidade": "bigint", "saldo_contabil_centavos": "bigint", "saldos_ausentes": "bigint", "saldos_negativos": "bigint"}),
    "cargas_publicadas": ("control", {"source_sha256": "string", "release_id": "string", "workflow_run_id": "string", "published_at": "string", "source_key": "string", "source_version_id": "string", "registros_total": "bigint", "registros_gold": "bigint", "aptos": "bigint", "revisao": "bigint", "quarentena": "bigint"}),
}


def spark_schema(columns):
    from pyspark.sql.types import StructType, StructField, StringType, LongType, BooleanType
    types = {"string": StringType(), "bigint": LongType(), "boolean": BooleanType()}
    return StructType([StructField(name, types[kind], True) for name, kind in columns.items()])


def catalog():
    return {name: {"layer": layer, "columns": [{"name": field, "type": kind} for field, kind in columns.items()]}
            for name, (layer, columns) in TABLES.items()}


def source_id(bucket, key, version_id, release_id):
    if not version_id or version_id == "null":
        raise ValueError("O objeto de entrada deve ter uma versão S3 imutável.")
    return fingerprint([bucket, key, version_id, release_id])


def load_id(source_sha256, release_id, sheet):
    return fingerprint([source_sha256, release_id, sheet])[:32]
