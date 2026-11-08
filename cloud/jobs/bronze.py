"""Job Bronze: versão exata do Excel, evidência original e Parquet bruto."""

import hashlib
import tempfile
from pathlib import Path

from cloud.contracts import BRONZE, load_id, source_id, spark_schema
from cloud.runtime import Runtime, now
from pipeline.cleaning import stable_json
from pipeline.schema import COLUMNS, validate_headers
from pipeline.xlsx import Workbook


def write_bronze_jsonl(book, destination, metadata, maximum_rows):
    """Converte o XML em JSONL sem acumular as linhas da planilha no driver."""
    count = 0
    with destination.open("w", encoding="utf-8") as output:
        for number, raw, formulas in book.rows():
            if number == 1:
                continue
            if count >= maximum_rows:
                raise ValueError("Excel excede o limite configurado de linhas.")
            output.write(
                stable_json(
                    {
                        **metadata,
                        "source_row": number,
                        "raw_json": stable_json({column: raw.get(column) for column in COLUMNS}),
                        "formulas_json": stable_json(formulas),
                        "date_1904": book.date_1904,
                    }
                )
                + "\n"
            )
            count += 1
    if not count:
        raise ValueError("Planilha sem registros.")
    return count


def bronze():
    """Preserva a fonte e converte suas linhas para a camada Bronze."""
    rt = Runtime()
    key, version = rt.props["source_key"], rt.props["source_version_id"]
    if not key.startswith("landing/") or not key.endswith(".xlsx"):
        raise ValueError("Fonte deve estar em landing/ e ter extensão .xlsx.")
    if source_id(rt.bucket, key, version, rt.args["RELEASE_ID"]) != rt.props["source_id"]:
        raise ValueError("Identificador da fonte não confere.")
    maximum = int(rt.args["MAX_SOURCE_BYTES"])
    # Ler a versão do evento mantém a carga ligada ao arquivo selecionado.
    obj = rt.s3.get_object(Bucket=rt.bucket, Key=key, VersionId=version)
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "source.xlsx"
        digest, size = hashlib.sha256(), 0
        try:
            if obj["ContentLength"] > maximum:
                raise ValueError(
                    "Excel excede o limite configurado para a leitura não distribuída."
                )
            with source.open("wb") as dest:
                for block in obj["Body"].iter_chunks(1024 * 1024):
                    size += len(block)
                    if size > maximum:
                        raise ValueError("Excel excede o limite de bytes.")
                    digest.update(block)
                    dest.write(block)
        finally:
            obj["Body"].close()
        book = Workbook(source, rt.props.get("sheet"))
        validate_headers(book.headers)
        identifier = load_id(digest.hexdigest(), rt.args["RELEASE_ID"], book.sheet)
        rt.update(load_id=identifier)
        state = rt.publication_state(identifier)
        if state:
            rt.update(**state)
            rt.finish()
            return
        rt.s3.copy_object(
            Bucket=rt.bucket,
            Key=f"bronze/originals/{digest.hexdigest()}.xlsx",
            CopySource={"Bucket": rt.bucket, "Key": key, "VersionId": version},
            ServerSideEncryption="AES256",
        )
        jsonl = Path(temp) / "bronze.jsonl"
        metadata = {
            "source_sha256": digest.hexdigest(),
            "source_bucket": rt.bucket,
            "source_key": key,
            "source_version_id": version,
            "sheet": book.sheet,
        }
        count = write_bronze_jsonl(book, jsonl, metadata, int(rt.args["MAX_SOURCE_ROWS"]))
        temporary_key = f"temporary/{rt.run_id}/bronze.jsonl"
        # O arquivo local do driver não é compartilhado com os executores do Glue.
        try:
            rt.s3.upload_file(
                str(jsonl), rt.bucket, temporary_key, ExtraArgs={"ServerSideEncryption": "AES256"}
            )
            frame = (
                rt.spark.read.schema(spark_schema(BRONZE))
                .option("mode", "FAILFAST")
                .json(f"s3://{rt.bucket}/{temporary_key}")
            )
            rt.write(frame, "bronze", "linhas_originais")
        finally:
            rt.s3.delete_object(Bucket=rt.bucket, Key=temporary_key)
        rt.json(
            f"{rt.prefix}/meta/bronze.json",
            {
                "source_sha256": digest.hexdigest(),
                "source_rows": count,
                "sheet": book.sheet,
                "source_key": key,
                "source_version_id": version,
                "ingested_at": now(),
            },
        )
    rt.finish()


if __name__ == "__main__":
    bronze()
