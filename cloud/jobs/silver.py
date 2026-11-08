"""Job Silver: limpeza distribuída, deduplicação, qualidade e linhagem."""

import json

from cloud.contracts import fields, spark_schema
from cloud.runtime import Runtime
from pipeline.cleaning import Cleaner, fingerprint, stable_json


def clean_partition(rows):
    """Aplica as regras compartilhadas nas linhas de cada partição Spark."""
    for row in rows:
        raw = json.loads(row.raw_json)
        formulas = json.loads(row.formulas_json)
        data, issues = Cleaner(raw, date_1904=row.date_1904).run()
        if formulas:
            issues.append(
                {
                    "codigo": "FORMULA_NA_ORIGEM",
                    "campo": "fonte",
                    "severidade": "WARNING",
                    "detalhe": {"colunas": formulas, "decisao": "valor_em_cache_nao_recalculado"},
                }
            )
        flat_issues = [
            {
                "codigo": i["codigo"],
                "campo": i["campo"],
                "severidade": i["severidade"],
                "detalhe_json": stable_json(i["detalhe"]),
            }
            for i in issues
        ]
        yield (
            fingerprint(data),
            stable_json(data),
            stable_json(flat_issues),
            row.source_sha256,
            row.source_bucket,
            row.source_key,
            row.source_version_id,
            row.sheet,
            row.source_row,
            fingerprint(raw),
        )


def build_silver(bronze, cached=None):
    """Constrói os DataFrames tratados, ocorrências, linhagem e quarentena."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType

    def cache(frame):
        frame.persist()
        if cached is not None:
            cached.append(frame)
        return frame

    spark = bronze.sparkSession
    parsed = cache(
        spark.createDataFrame(
            bronze.rdd.mapPartitions(clean_partition),
            "registro_id string, data_json string, issues_json string, source_sha256 string, source_bucket string, source_key string, source_version_id string, sheet string, source_row long, hash_linha_original string",
        )
    )
    records = cache(
        parsed.select(
            "registro_id",
            F.from_json("data_json", spark_schema(fields()), {"mode": "FAILFAST"}).alias("data"),
        )
        .select("registro_id", "data.*")
        .dropDuplicates(["registro_id"])
    )
    issue_columns = {
        "codigo": "string",
        "campo": "string",
        "severidade": "string",
        "detalhe_json": "string",
    }
    issues = parsed.select(
        "registro_id",
        F.explode(
            F.from_json("issues_json", ArrayType(spark_schema(issue_columns)), {"mode": "FAILFAST"})
        ).alias("issue"),
    ).select("registro_id", "issue.*")

    def key_issues(columns, code):
        valid = records
        for name in columns:
            valid = valid.filter(F.col(name).isNotNull())
        groups = (
            valid.groupBy(*columns)
            .agg(
                F.count("*").alias("quantidade_registros"),
                F.min("registro_id").alias("primeiro_registro"),
                F.max("registro_id").alias("ultimo_registro"),
            )
            .filter("quantidade_registros > 1")
        )
        return records.join(groups, columns, "inner").select(
            "registro_id",
            F.lit(code).alias("codigo"),
            F.lit("chave").alias("campo"),
            F.lit("ERROR").alias("severidade"),
            F.to_json(
                F.struct(*columns, "quantidade_registros", "primeiro_registro", "ultimo_registro")
            ).alias("detalhe_json"),
        )

    issues = issues.unionByName(
        key_issues(["cnpj_cedente", "numero_contrato", "data_corte"], "CHAVE_NEGOCIO_CONFLITANTE")
    )
    issues = issues.unionByName(
        key_issues(["codigo_identificador", "data_corte"], "ID_ORIGEM_CONFLITANTE")
    )
    names = (
        records.filter("documento_devedor IS NOT NULL")
        .groupBy("tipo_pessoa", "documento_devedor")
        .agg(F.countDistinct("nome_devedor").alias("nomes_distintos"))
        .filter("nomes_distintos > 1")
    )
    name_issues = records.join(names, ["tipo_pessoa", "documento_devedor"], "inner").select(
        "registro_id",
        F.lit("NOMES_DIVERGENTES_MESMO_DOCUMENTO").alias("codigo"),
        F.lit("nome_devedor").alias("campo"),
        F.lit("WARNING").alias("severidade"),
        F.to_json(F.struct("nomes_distintos")).alias("detalhe_json"),
    )
    issues = cache(
        issues.unionByName(name_issues)
        .dropDuplicates()
        .withColumn(
            "ocorrencia_id",
            F.sha2(
                F.concat_ws("|", "registro_id", "codigo", "campo", "severidade", "detalhe_json"),
                256,
            ),
        )
    )
    levels = issues.groupBy("registro_id").agg(
        F.max(
            F.when(F.col("severidade") == "ERROR", 2)
            .when(F.col("severidade") == "WARNING", 1)
            .otherwise(0)
        ).alias("nivel"),
        F.sum(F.when(F.col("severidade") != "INFO", 1).otherwise(0))
        .cast("long")
        .alias("quantidade_alertas"),
    )
    counts = parsed.groupBy("registro_id").agg(F.count("*").alias("linhas_origem"))
    records = (
        records.join(counts, "registro_id")
        .join(levels, "registro_id", "left")
        .fillna({"nivel": 0, "quantidade_alertas": 0})
    )
    records = cache(
        records.withColumn(
            "status_qualidade",
            F.when(F.col("nivel") == 2, "QUARENTENA")
            .when(F.col("nivel") == 1, "REVISAO")
            .otherwise("APTO"),
        ).drop("nivel")
    )
    lineage = parsed.select(
        "source_sha256",
        "source_bucket",
        "source_key",
        "source_version_id",
        "sheet",
        "source_row",
        "hash_linha_original",
        "registro_id",
    )
    return {
        "registros_tratados": records,
        "ocorrencias_qualidade": issues,
        "linhagem_origem": lineage,
        "quarentena": records.filter("status_qualidade='QUARENTENA'"),
    }


def silver():
    """Lê a Bronze e grava os quatro conjuntos de saída da Silver."""
    rt = Runtime()
    cached = []
    try:
        if rt.props.get("skip_completed") != "true" and not rt.props.get("resume_prefix"):
            frames = build_silver(rt.read("bronze", "linhas_originais"), cached)
            for name, frame in frames.items():
                rt.write(frame, "silver", name)
        rt.finish()
    finally:
        for frame in reversed(cached):
            frame.unpersist()


if __name__ == "__main__":
    silver()
