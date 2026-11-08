"""Transformações distribuídas; mesmas regras de negócio da execução local."""
import json

from pipeline.cleaning import Cleaner, fingerprint, stable_json
from cloud.contracts import fields, spark_schema, FACT


def clean_partition(rows):
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


def build_gold(silver):
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType
    identity = F.udf(lambda role, a: fingerprint([role, a]), StringType())
    debtor = F.udf(lambda person, doc: fingerprint(["devedor", person, doc]), StringType())
    product = F.udf(lambda a, b: fingerprint(["produto", a, b]), StringType())
    base = silver.filter("status_qualidade <> 'QUARENTENA'").withColumn("cedente_id", identity(F.lit("cedente"), F.col("cnpj_cedente"))).withColumn("devedor_id", debtor("tipo_pessoa", "documento_devedor")).withColumn("produto_id", product("produto", "subproduto")).persist()
    fact = base.select(*FACT).persist()
    cedente = base.select("cedente_id", F.col("cnpj_cedente").alias("cnpj"), F.coalesce("cnpj_cedente_valido", F.lit(0)).cast("long").alias("documento_valido")).dropDuplicates()
    devedor = base.groupBy("devedor_id", "documento_devedor", "tipo_pessoa").agg(
        F.when(F.countDistinct("nome_devedor") == 1, F.max("nome_devedor")).alias("nome"),
        F.coalesce(F.max("documento_devedor_valido"), F.lit(0)).cast("long").alias("documento_valido")).withColumnRenamed("documento_devedor", "documento")
    produto = base.select("produto_id", "produto", "subproduto").dropDuplicates()
    dates = None
    for name in fields():
        if name.startswith("data_"):
            part = base.select(F.col(name).alias("data")).filter("data IS NOT NULL")
            dates = part if dates is None else dates.unionByName(part)
    dates = dates.distinct().withColumn("ano", F.year("data").cast("long")).withColumn("mes", F.month("data").cast("long")).withColumn("dia", F.dayofmonth("data").cast("long")).withColumn("trimestre", F.quarter("data").cast("long"))
    mart = fact.groupBy("data_corte", "tipo_pessoa", "produto", "faixa_atraso", "status_qualidade").agg(
        F.count("*").alias("quantidade"), F.sum("valor_contabil_centavos").alias("saldo_contabil_centavos"),
        F.sum(F.col("valor_contabil_centavos").isNull().cast("long")).alias("saldos_ausentes"),
        F.sum(F.when(F.col("valor_contabil_centavos") < 0, 1).otherwise(0)).alias("saldos_negativos"))
    return {"fato_carteira": fact, "dim_cedente": cedente, "dim_devedor": devedor, "dim_produto": produto,
            "dim_data": dates, "mart_resumo_carteira": mart}


def validate(silver, issues, lineage, gold, source_rows):
    from pyspark.sql import functions as F
    counts = {r.status_qualidade: r["count"] for r in silver.groupBy("status_qualidade").count().collect()}
    total = sum(counts.values())
    fact = gold["fato_carteira"]
    fact_count = fact.count()
    quarantine = silver.filter("status_qualidade='QUARENTENA'")
    checks = {
        "linhagem_completa": lineage.count() == source_rows,
        "reconciliacao_linhas": (silver.agg(F.sum("linhas_origem")).first()[0] or 0) == source_rows,
        "reconciliacao_carteira": total == fact_count + counts.get("QUARENTENA", 0),
        "grao_unico": fact.groupBy("cedente_id", "numero_contrato", "data_corte").count().filter("count>1").count() == 0,
        "sem_erro_na_gold": fact.join(issues.filter("severidade='ERROR'"), "registro_id").count() == 0,
        "aptos_sem_alertas": fact.filter("status_qualidade='APTO'").join(issues.filter("severidade<>'INFO'"), "registro_id").count() == 0,
        "pf_pj_coerente": silver.filter("(tipo_pessoa='PF' AND (data_abertura IS NOT NULL OR socio_1_documento IS NOT NULL OR socio_2_nome IS NOT NULL)) OR (tipo_pessoa='PJ' AND (data_nascimento IS NOT NULL OR sexo IS NOT NULL OR renda_centavos IS NOT NULL OR nome_mae IS NOT NULL))").count() == 0,
    }
    for key, dim, foreign in [("cedente", "dim_cedente", "cedente_id"), ("devedor", "dim_devedor", "devedor_id"), ("produto", "dim_produto", "produto_id")]:
        checks[f"referencia_{key}"] = fact.join(gold[dim], foreign, "left_anti").count() == 0
    for name in ["valor_original_centavos", "valor_contabil_centavos", "valor_face_centavos"]:
        summed = lambda df: df.agg(F.sum(name)).first()[0] or 0
        checks[f"saldo_{name}"] = summed(silver) == summed(fact) + summed(quarantine)
    if not all(checks.values()):
        raise ValueError("Validações de publicação falharam: " + ", ".join(k for k, v in checks.items() if not v))
    return {"counts": {"linhas_origem": source_rows, "registros_unicos": total, "linhas_gold": fact_count,
                       "duplicatas_removidas": source_rows-total, **{s: counts.get(s, 0) for s in ["APTO", "REVISAO", "QUARENTENA"]}}, "controls": checks}
