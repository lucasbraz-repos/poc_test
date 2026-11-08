"""Job Silver: limpeza distribuída, deduplicação, qualidade e linhagem."""
import json

from cloud.contracts import fields, spark_schema
from cloud.runtime import Runtime
from pipeline.cleaning import Cleaner, fingerprint, stable_json


def clean_partition(rows):
    """Aplica as regras compartilhadas nas linhas de cada partição Spark."""
    for row in rows:
        data, issues = Cleaner(json.loads(row.raw_json), date_1904=row.date_1904).run()
        if json.loads(row.formulas_json):
            issues.append({"codigo": "FORMULA_NA_ORIGEM", "campo": "fonte", "severidade": "WARNING",
                           "detalhe": {"colunas": json.loads(row.formulas_json), "decisao": "valor_em_cache_nao_recalculado"}})
        flat_issues = [{"codigo": i["codigo"], "campo": i["campo"], "severidade": i["severidade"],
                        "detalhe_json": stable_json(i["detalhe"])} for i in issues]
        yield (fingerprint(data), stable_json(data), stable_json(flat_issues), row.source_sha256,
               row.source_bucket, row.source_key, row.source_version_id, row.sheet, row.source_row,
               fingerprint(json.loads(row.raw_json)))


def build_silver(bronze):
    """Constrói os DataFrames tratados, ocorrências, linhagem e quarentena."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType
    spark = bronze.sparkSession
    parsed = spark.createDataFrame(bronze.rdd.mapPartitions(clean_partition),
        "registro_id string, data_json string, issues_json string, source_sha256 string, source_bucket string, source_key string, source_version_id string, sheet string, source_row long, hash_linha_original string").persist()
    records = parsed.select("registro_id", F.from_json("data_json", spark_schema(fields())).alias("data")).select("registro_id", "data.*").dropDuplicates(["registro_id"]).persist()
    issue_columns = {"codigo": "string", "campo": "string", "severidade": "string", "detalhe_json": "string"}
    issues = parsed.select("registro_id", F.explode(F.from_json("issues_json", ArrayType(spark_schema(issue_columns)))).alias("issue")).select("registro_id", "issue.*")

    def key_issues(columns, code):
        valid = records
        for name in columns:
            valid = valid.filter(F.col(name).isNotNull())
        groups = valid.groupBy(*columns).agg(F.count("*").alias("n"), F.sort_array(F.collect_list("registro_id")).alias("registros_do_grupo")).filter("n > 1")
        return records.join(groups, columns, "inner").select("registro_id", F.lit(code).alias("codigo"),
            F.lit("chave").alias("campo"), F.lit("ERROR").alias("severidade"),
            F.to_json(F.struct("registros_do_grupo")).alias("detalhe_json"))

    issues = issues.unionByName(key_issues(["cnpj_cedente", "numero_contrato", "data_corte"], "CHAVE_NEGOCIO_CONFLITANTE"))
    issues = issues.unionByName(key_issues(["codigo_identificador", "data_corte"], "ID_ORIGEM_CONFLITANTE"))
    names = records.filter("documento_devedor IS NOT NULL").groupBy("tipo_pessoa", "documento_devedor").agg(F.countDistinct("nome_devedor").alias("nomes_distintos")).filter("nomes_distintos > 1")
    name_issues = records.join(names, ["tipo_pessoa", "documento_devedor"], "inner").select("registro_id",
        F.lit("NOMES_DIVERGENTES_MESMO_DOCUMENTO").alias("codigo"), F.lit("nome_devedor").alias("campo"),
        F.lit("WARNING").alias("severidade"), F.to_json(F.struct("nomes_distintos")).alias("detalhe_json"))
    issues = issues.unionByName(name_issues).dropDuplicates().withColumn("ocorrencia_id",
        F.sha2(F.concat_ws("|", "registro_id", "codigo", "campo", "severidade", "detalhe_json"), 256)).persist()
    levels = issues.groupBy("registro_id").agg(F.max(F.when(F.col("severidade") == "ERROR", 2).when(F.col("severidade") == "WARNING", 1).otherwise(0)).alias("nivel"),
        F.sum(F.when(F.col("severidade") != "INFO", 1).otherwise(0)).cast("long").alias("quantidade_alertas"))
    counts = parsed.groupBy("registro_id").agg(F.count("*").alias("linhas_origem"))
    records = records.join(counts, "registro_id").join(levels, "registro_id", "left").fillna({"nivel": 0, "quantidade_alertas": 0})
    records = records.withColumn("status_qualidade", F.when(F.col("nivel") == 2, "QUARENTENA").when(F.col("nivel") == 1, "REVISAO").otherwise("APTO")).drop("nivel").persist()
    lineage = parsed.select("source_sha256", "source_bucket", "source_key", "source_version_id", "sheet", "source_row", "hash_linha_original", "registro_id")
    return {"registros_tratados": records, "ocorrencias_qualidade": issues,
            "linhagem_origem": lineage, "quarentena": records.filter("status_qualidade='QUARENTENA'")}


def silver():
    """Lê a Bronze e grava os quatro conjuntos de saída da Silver."""
    rt = Runtime()
    if rt.props.get("skip_completed") != "true":
        frames = build_silver(rt.read("bronze", "linhas_originais"))
        for name, frame in frames.items():
            rt.write(frame, "silver", name)
    rt.finish()


if __name__ == "__main__":
    silver()
