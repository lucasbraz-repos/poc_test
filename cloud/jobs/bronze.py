"""Job Bronze: versão exata do Excel, evidência original e Parquet bruto."""
import hashlib
import tempfile
from pathlib import Path

from cloud.contracts import BRONZE, load_id, spark_schema, source_id
from cloud.runtime import Runtime, now
from pipeline.cleaning import stable_json
from pipeline.schema import COLUMNS, validate_headers
from pipeline.xlsx import Workbook


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
    if obj["ContentLength"] > maximum:
        raise ValueError("Excel excede o limite configurado para a leitura não distribuída.")
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "source.xlsx"
        digest, size = hashlib.sha256(), 0
        with source.open("wb") as dest:
            for block in obj["Body"].iter_chunks(1024*1024):
                size += len(block)
                if size > maximum:
                    raise ValueError("Excel excede o limite de bytes.")
                digest.update(block)
                dest.write(block)
        obj["Body"].close()
        book = Workbook(source, rt.props.get("sheet"))
        validate_headers(book.headers)
        identifier = load_id(digest.hexdigest(), rt.args["RELEASE_ID"], book.sheet)
        rt.update(load_id=identifier)
        # Uma carga concluída com o mesmo conteúdo/release/aba é reaproveitada.
        if rt.exists(f"control/commits/{identifier}.json"):
            rt.update(skip_completed="true")
            rt.finish()
            return
        rt.s3.copy_object(Bucket=rt.bucket, Key=f"bronze/originals/{digest.hexdigest()}.xlsx",
                          CopySource={"Bucket": rt.bucket, "Key": key, "VersionId": version}, ServerSideEncryption="AES256")
        rows = []
        for number, raw, formulas in book.rows():
            if number == 1:
                continue
            if len(rows) >= int(rt.args["MAX_SOURCE_ROWS"]):
                raise ValueError("Excel excede o limite configurado de linhas.")
            rows.append((digest.hexdigest(), rt.bucket, key, version, book.sheet, number,
                         stable_json({c: raw.get(c) for c in COLUMNS}), stable_json(formulas), book.date_1904))
        if not rows:
            raise ValueError("Planilha sem registros.")
        rt.write(rt.spark.createDataFrame(rows, spark_schema(BRONZE)), "bronze", "linhas_originais")
        rt.json(f"{rt.prefix}/meta/bronze.json", {"source_sha256": digest.hexdigest(), "source_rows": len(rows),
                "sheet": book.sheet, "source_key": key, "source_version_id": version, "ingested_at": now()})
    rt.finish()


if __name__ == "__main__":
    bronze()
