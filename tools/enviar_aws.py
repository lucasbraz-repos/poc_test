"""Selecionar o Excel é suficiente: upload S3 aciona o workflow inteiro."""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.aws_cli import aws, identity
from cloud.contracts import source_id


def main(argv=None):
    parser = argparse.ArgumentParser(description="Enviar Excel ao S3 e aguardar toda a pipeline Glue.")
    parser.add_argument("arquivo", nargs="?", type=Path)
    parser.add_argument("--profile", default="poc-aws")
    parser.add_argument("--config", type=Path, default=ROOT / "build/aws/deployment.json")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--sem-abrir", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    identity(args.profile, config["region"], config["account_id"])
    if args.arquivo is None:
        from executar_pipeline import select_file
        args.arquivo = select_file()
    if args.arquivo is None:
        print("Seleção cancelada.")
        return 0
    source = args.arquivo.expanduser().resolve()
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise ValueError("Selecione um arquivo .xlsx existente.")
    if source.stat().st_size > 67108864:
        raise ValueError("Arquivo excede 64 MiB; ajuste o limite de ingestão antes de enviar.")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    key = "landing/" + digest + ".xlsx"
    result = aws(args.profile, config["region"], "s3api", "put-object", "--bucket", config["data_bucket"],
                 "--key", key, "--body", str(source), "--server-side-encryption", "AES256", "--metadata", json.dumps({"sha256": digest}))
    version = result["VersionId"]
    sid = source_id(config["data_bucket"], key, version, config["release_id"])
    receipt_key = f"control/sources/{sid}.json"
    print(f"Arquivo enviado: s3://{config['data_bucket']}/{key}", flush=True)
    print("Aguardando Glue Workflow: Bronze → Silver → Gold → Athena.", flush=True)
    output = ROOT / "outputs_aws" / sid
    output.mkdir(parents=True, exist_ok=True)
    limit = time.monotonic() + args.timeout
    while time.monotonic() < limit:
        try:
            aws(args.profile, config["region"], "s3api", "get-object", "--bucket", config["data_bucket"], "--key", receipt_key, str(output / "receipt.json"))
            receipt = json.loads((output / "receipt.json").read_text())
            report = output / "relatorio.html"
            aws(args.profile, config["region"], "s3api", "get-object", "--bucket", config["data_bucket"], "--key", receipt["report_key"], str(report))
            print("Pipeline concluída. Load ID: " + receipt["load_id"])
            print("Relatório: " + str(report))
            if not args.sem_abrir:
                webbrowser.open(report.as_uri())
            return 0
        except subprocess.CalledProcessError as error:
            if "NoSuchKey" not in (error.stderr or "") and "404" not in (error.stderr or ""):
                raise
        runs = aws(args.profile, config["region"], "glue", "get-workflow-runs", "--name", config["workflow_name"], "--max-results", "20")
        for run in runs.get("Runs", []):
            if run.get("WorkflowRunProperties", {}).get("source_id") != sid:
                continue
            if run["Status"] in {"ERROR", "STOPPED"} or run.get("Statistics", {}).get("FailedActions", 0) > 0:
                raise RuntimeError("Workflow falhou. Consulte o Glue/CloudWatch; RunId=" + run["WorkflowRunId"])
        time.sleep(15)
    raise TimeoutError("A espera terminou; a execução AWS pode continuar. Consulte o Glue Workflow " + config["workflow_name"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
