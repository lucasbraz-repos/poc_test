"""Lambda SQS → Glue Workflow. Nenhum tratamento de dados ocorre na Lambda."""
import json
import logging
import os
from urllib.parse import unquote_plus

from cloud.contracts import source_id

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


def dispatch(event, s3, glue, *, bucket, workflow, release_id):
    failures = []
    for message in event.get("Records", []):
        try:
            body = json.loads(message["body"])
            if body.get("Event") == "s3:TestEvent":
                continue
            for record in body.get("Records", []):
                if not record.get("eventName", "").startswith("ObjectCreated:"):
                    continue
                obj = record["s3"]["object"]
                source_bucket = record["s3"]["bucket"]["name"]
                key = unquote_plus(obj["key"])
                if source_bucket != bucket or not key.startswith("landing/") or not key.endswith(".xlsx"):
                    raise ValueError("Evento fora do prefixo/tipo de entrada autorizado.")
                version = obj.get("versionId")
                identity = source_id(bucket, key, version, release_id)
                try:
                    s3.head_object(Bucket=bucket, Key=f"control/sources/{identity}.json")
                    continue
                except Exception as error:
                    if getattr(error, "response", {}).get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
                        raise
                result = glue.start_workflow_run(Name=workflow, RunProperties={
                    "source_bucket": bucket, "source_key": key, "source_version_id": version,
                    "source_id": identity, "release_id": release_id,
                })
                LOG.info("Workflow iniciado: %s", result["RunId"])
        except Exception as error:
            # Inclusive ConcurrentRunsExceededException: SQS mantém o arquivo até haver capacidade.
            LOG.warning("Entrada mantida na fila: %s", type(error).__name__)
            failures.append({"itemIdentifier": message["messageId"]})
    return {"batchItemFailures": failures}


def handler(event, context):
    import boto3
    return dispatch(event, boto3.client("s3"), boto3.client("glue"), bucket=os.environ["DATA_BUCKET"],
                    workflow=os.environ["WORKFLOW_NAME"], release_id=os.environ["RELEASE_ID"])
