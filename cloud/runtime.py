"""I/O e estado por execução; imports de Glue/Spark somente no ambiente do job."""

import copy
import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal


def now():
    return datetime.now(UTC).isoformat()


def json_bytes(value):
    """Preserva valores decimais exatos em metadados e relatórios JSON."""

    def encode(item):
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(f"Tipo não serializável em JSON: {type(item).__name__}")

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=encode
    ).encode("utf-8")


def positive_option(arguments, name, default):
    """Lê opções de paralelismo sem torná-las obrigatórias em releases antigos."""
    option = f"--{name}"
    if option not in arguments:
        return default
    position = arguments.index(option)
    if position + 1 >= len(arguments):
        raise ValueError(f"{option} exige um inteiro positivo.")
    value = int(arguments[position + 1])
    if value < 1:
        raise ValueError(f"{option} exige um inteiro positivo.")
    return value


def put_if_absent(client, **parameters):
    """Escrita condicional também com o modelo S3 antigo incluído no Glue 5.

    O header é inserido antes da assinatura SigV4. A condição continua sendo
    verificada pelo S3; não é substituída por uma leitura seguida de escrita.
    """
    members = client.meta.service_model.operation_model("PutObject").input_shape.members
    if "IfNoneMatch" in members:
        return client.put_object(**parameters, IfNoneMatch="*")

    def add_condition(params, **kwargs):
        params["headers"]["If-None-Match"] = "*"

    event, identifier = "before-call.s3.PutObject", "pipeline-conditional-put"
    client.meta.events.register(event, add_condition, unique_id=identifier)
    try:
        return client.put_object(**parameters)
    finally:
        client.meta.events.unregister(event, unique_id=identifier)


class Runtime:
    def __init__(self):
        import boto3
        from awsglue.context import GlueContext
        from awsglue.job import Job
        from awsglue.utils import getResolvedOptions
        from pyspark.context import SparkContext

        names = [
            "JOB_NAME",
            "WORKFLOW_NAME",
            "WORKFLOW_RUN_ID",
            "DATA_BUCKET",
            "RELEASE_ID",
            "BRONZE_DATABASE",
            "SILVER_DATABASE",
            "GOLD_DATABASE",
            "CONTROL_DATABASE",
            "MAX_SOURCE_BYTES",
            "MAX_SOURCE_ROWS",
        ]
        self.args = getResolvedOptions(sys.argv, names)
        self.s3, self.glue = boto3.client("s3"), boto3.client("glue")
        self.bucket = self.args["DATA_BUCKET"]
        self.run_id = self.args["WORKFLOW_RUN_ID"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.run_id):
            raise ValueError("Identificador de execução inválido.")
        self.props = self.glue.get_workflow_run_properties(
            Name=self.args["WORKFLOW_NAME"], RunId=self.run_id
        )["RunProperties"]
        if (
            self.props.get("source_bucket") != self.bucket
            or self.props.get("release_id") != self.args["RELEASE_ID"]
        ):
            raise ValueError("Conta de origem/release do workflow incompatível com o job.")
        self.prefix = f"runs/{self.run_id}"
        self.output_partitions = positive_option(sys.argv, "OUTPUT_PARTITIONS", 2)
        self.context = GlueContext(SparkContext.getOrCreate())
        self.spark = self.context.spark_session
        self.spark.conf.set("spark.sql.session.timeZone", "UTC")
        self.spark.conf.set(
            "spark.sql.shuffle.partitions", str(positive_option(sys.argv, "SHUFFLE_PARTITIONS", 4))
        )
        self.spark.conf.set("spark.sql.ansi.enabled", "true")
        self.job = Job(self.context)
        self.job.init(self.args["JOB_NAME"], self.args)

    def update(self, **properties):
        self.glue.put_workflow_run_properties(
            Name=self.args["WORKFLOW_NAME"], RunId=self.run_id, RunProperties=properties
        )
        self.props.update(properties)

    def uri(self, layer, name):
        return f"s3://{self.bucket}/{self.prefix}/{layer}/{name}/"

    def read(self, layer, name):
        from cloud.contracts import TABLES, spark_schema

        return self.spark.read.schema(spark_schema(TABLES[name][1])).parquet(self.uri(layer, name))

    def write(self, frame, layer, name):
        from cloud.contracts import TABLES

        frame.select(*TABLES[name][1]).coalesce(self.output_partitions).write.mode(
            "overwrite"
        ).option("compression", "snappy").parquet(self.uri(layer, name))

    def json(self, key, value):
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json_bytes(value),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def read_json(self, key):
        body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            return json.loads(body.read())
        finally:
            body.close()

    def exists(self, key):
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return False
            raise

    def partition(self, table):
        from cloud.contracts import TABLES

        layer = TABLES[table][0]
        database = self.args[layer.upper() + "_DATABASE"]
        original = self.glue.get_table(DatabaseName=database, Name=table)["Table"]
        storage = copy.deepcopy(original["StorageDescriptor"])
        storage["Location"] = self.uri(layer, table)
        partition = {
            "Values": [self.props["load_id"]],
            "StorageDescriptor": storage,
            "Parameters": {"classification": "parquet"},
        }
        try:
            self.glue.create_partition(
                DatabaseName=database, TableName=table, PartitionInput=partition
            )
        except self.glue.exceptions.AlreadyExistsException:
            existing = self.glue.get_partition(
                DatabaseName=database, TableName=table, PartitionValues=[self.props["load_id"]]
            )["Partition"]
            if existing["StorageDescriptor"]["Location"] != storage["Location"]:
                raise ValueError(f"Partição imutável de {table} já aponta para outra execução.")

    def publication_state(self, identifier):
        """Localiza commits, preparações e publicações anteriores ao protocolo prepared."""
        if self.exists(f"control/commits/{identifier}.json"):
            return {"skip_completed": "true"}
        prepared_key = f"control/prepared/{identifier}.json"
        if self.exists(prepared_key):
            prepared = self.read_json(prepared_key)
            self.validate_prepared(prepared, identifier)
            return {"resume_prefix": prepared["prefix"]}
        try:
            partition = self.glue.get_partition(
                DatabaseName=self.args["CONTROL_DATABASE"],
                TableName="cargas_publicadas",
                PartitionValues=[identifier],
            )["Partition"]
        except self.glue.exceptions.EntityNotFoundException:
            return {}
        location = partition["StorageDescriptor"]["Location"]
        match = re.fullmatch(
            re.escape(f"s3://{self.bucket}/")
            + r"(runs/[A-Za-z0-9_-]{1,128})/control/cargas_publicadas/",
            location,
        )
        if not match:
            raise ValueError("Localização da publicação anterior não reconhecida.")
        prefix = match.group(1)
        # A versão anterior já escrevia ready antes do registro de partições.
        summary = self.read_json(f"{prefix}/meta/ready.json")
        prepared = self.prepare(summary, prefix=prefix)
        return {"resume_prefix": prepared["prefix"]}

    def validate_prepared(self, prepared, identifier=None):
        expected = identifier or self.props["load_id"]
        prefix = prepared.get("prefix", "")
        if (
            prepared.get("load_id") != expected
            or prepared.get("release_id") != self.args["RELEASE_ID"]
            or not re.fullmatch(r"runs/[A-Za-z0-9_-]{1,128}", prefix)
            or prepared.get("ready_key") != f"{prefix}/meta/ready.json"
        ):
            raise ValueError("Manifesto de publicação incompatível com esta carga.")

    def prepare(self, summary, *, prefix=None):
        """Escolhe uma única execução física antes de registrar qualquer partição."""
        prefix = prefix or self.prefix
        manifest = {
            "load_id": summary["load_id"],
            "release_id": summary["release_id"],
            "prefix": prefix,
            "ready_key": f"{prefix}/meta/ready.json",
        }
        self.validate_prepared(manifest)
        key = f"control/prepared/{manifest['load_id']}.json"
        try:
            put_if_absent(
                self.s3,
                Bucket=self.bucket,
                Key=key,
                Body=json_bytes(manifest),
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") not in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }:
                raise
            manifest = self.read_json(key)
            self.validate_prepared(manifest)
        return manifest

    def source_receipt(self):
        self.json(
            f"control/sources/{self.props['source_id']}.json",
            {
                "load_id": self.props["load_id"],
                "workflow_run_id": self.run_id,
                "report_key": f"reports/{self.props['load_id']}/relatorio.html",
            },
        )

    def finish(self):
        self.job.commit()
