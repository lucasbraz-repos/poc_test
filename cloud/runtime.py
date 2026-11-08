"""I/O e estado por execução; imports de Glue/Spark somente no ambiente do job."""
import copy
import json
import re
import sys
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Runtime:
    def __init__(self):
        import boto3
        from awsglue.utils import getResolvedOptions
        from awsglue.context import GlueContext
        from awsglue.job import Job
        from pyspark.context import SparkContext
        names = ["JOB_NAME", "WORKFLOW_NAME", "WORKFLOW_RUN_ID", "DATA_BUCKET", "RELEASE_ID",
                 "BRONZE_DATABASE", "SILVER_DATABASE", "GOLD_DATABASE", "CONTROL_DATABASE", "MAX_SOURCE_BYTES", "MAX_SOURCE_ROWS"]
        self.args = getResolvedOptions(sys.argv, names)
        self.s3, self.glue = boto3.client("s3"), boto3.client("glue")
        self.bucket = self.args["DATA_BUCKET"]
        self.run_id = self.args["WORKFLOW_RUN_ID"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.run_id):
            raise ValueError("Identificador de execução inválido.")
        self.props = self.glue.get_workflow_run_properties(Name=self.args["WORKFLOW_NAME"], RunId=self.run_id)["RunProperties"]
        if self.props.get("source_bucket") != self.bucket or self.props.get("release_id") != self.args["RELEASE_ID"]:
            raise ValueError("Conta de origem/release do workflow incompatível com o job.")
        self.prefix = f"runs/{self.run_id}"
        self.context = GlueContext(SparkContext.getOrCreate())
        self.spark = self.context.spark_session
        self.spark.conf.set("spark.sql.session.timeZone", "UTC")
        self.spark.conf.set("spark.sql.shuffle.partitions", "4")
        self.job = Job(self.context)
        self.job.init(self.args["JOB_NAME"], self.args)

    def update(self, **properties):
        self.glue.put_workflow_run_properties(Name=self.args["WORKFLOW_NAME"], RunId=self.run_id, RunProperties=properties)
        self.props.update(properties)

    def uri(self, layer, name):
        return f"s3://{self.bucket}/{self.prefix}/{layer}/{name}/"

    def read(self, layer, name):
        from cloud.contracts import TABLES, spark_schema
        return self.spark.read.schema(spark_schema(TABLES[name][1])).parquet(self.uri(layer, name))

    def write(self, frame, layer, name):
        from cloud.contracts import TABLES
        frame.select(*TABLES[name][1]).coalesce(2).write.mode("overwrite").option("compression", "snappy").parquet(self.uri(layer, name))

    def json(self, key, value):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(value, ensure_ascii=False, sort_keys=True).encode(), ContentType="application/json", ServerSideEncryption="AES256")

    def read_json(self, key):
        return json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def exists(self, key):
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def partition(self, table):
        from cloud.contracts import TABLES
        layer = TABLES[table][0]
        database = self.args[layer.upper() + "_DATABASE"]
        original = self.glue.get_table(DatabaseName=database, Name=table)["Table"]
        storage = copy.deepcopy(original["StorageDescriptor"])
        storage["Location"] = self.uri(layer, table)
        partition = {"Values": [self.props["load_id"]], "StorageDescriptor": storage, "Parameters": {"classification": "parquet"}}
        try:
            self.glue.create_partition(DatabaseName=database, TableName=table, PartitionInput=partition)
        except self.glue.exceptions.AlreadyExistsException:
            # Recupera uma publicação interrompida. As views só expõem cargas com o controle final registrado.
            self.glue.update_partition(DatabaseName=database, TableName=table, PartitionValueList=[self.props["load_id"]], PartitionInput=partition)

    def source_receipt(self):
        self.json(f"control/sources/{self.props['source_id']}.json", {
            "load_id": self.props["load_id"], "workflow_run_id": self.run_id,
            "report_key": f"reports/{self.props['load_id']}/relatorio.html",
        })

    def finish(self):
        self.job.commit()
