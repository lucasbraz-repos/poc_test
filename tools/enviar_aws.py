"""Selecionar o Excel é suficiente: upload S3 aciona o workflow inteiro."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloud.contracts import source_id
from tools.aws_cli import aws, format_error, identity, missing_object
from tools.config import DEFAULT_CONFIG, load_config, positive_int

POLL_INTERVAL_SECONDS = 15
RECEIPT_GRACE_SECONDS = 30
FAILURE_GRACE_SECONDS = 180
FAILURE_COUNTERS = ("FailedActions", "TimeoutActions", "StoppedActions", "ErroredActions")


def validate_source(path, max_source_bytes):
    """Valida o arquivo antes de iniciar qualquer upload."""
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise ValueError("Selecione um arquivo .xlsx existente.")
    size = source.stat().st_size
    if size == 0:
        raise ValueError("O arquivo Excel está vazio.")
    if size > max_source_bytes:
        raise ValueError(f"Arquivo excede o limite da implantação: {max_source_bytes:,} bytes.")
    return source


def check_release(config, profile):
    """Evita esperar por recibos com uma release local diferente da Lambda."""
    workflow = aws(
        profile, config["region"], "glue", "get-workflow", "--name", config["workflow_name"]
    )
    release = workflow.get("Workflow", {}).get("DefaultRunProperties", {}).get("release_id")
    if release != config["release_id"]:
        raise ValueError(
            "A release local diverge da implantação AWS. Atualize deployment.json pela implantação "
            "antes de enviar o arquivo; nenhum upload foi realizado."
        )


def upload_source(source, config, profile):
    """Envia uma versão S3 e retorna os identificadores necessários para acompanhá-la."""
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    checksum = digest.hexdigest()
    key = f"landing/{checksum}.xlsx"
    result = aws(
        profile,
        config["region"],
        "s3api",
        "put-object",
        "--bucket",
        config["data_bucket"],
        "--key",
        key,
        "--body",
        str(source),
        "--server-side-encryption",
        "AES256",
        "--metadata",
        json.dumps({"sha256": checksum}),
    )
    version = result["VersionId"]
    return {
        "source_id": source_id(config["data_bucket"], key, version, config["release_id"]),
        "bucket": config["data_bucket"],
        "key": key,
        "version_id": version,
    }


def find_workflow_run(config, profile, sid):
    """Localiza a execução da fonte, inclusive quando há outras cargas mais recentes."""
    token = None
    while True:
        arguments = [
            "glue",
            "get-workflow-runs",
            "--name",
            config["workflow_name"],
            "--max-results",
            "100",
            "--no-paginate",
        ]
        if token:
            arguments.extend(["--next-token", token])
        response = aws(profile, config["region"], *arguments)
        for run in response.get("Runs", []):
            if run.get("WorkflowRunProperties", {}).get("source_id") == sid:
                return run
        token = response.get("NextToken")
        if not token:
            return None


def workflow_failure(run):
    """Uma tentativa em andamento pode se recuperar; falhas terminais exigem ação."""
    statistics = run.get("Statistics", {})
    failures = {
        name: statistics.get(name, 0) for name in FAILURE_COUNTERS if statistics.get(name, 0)
    }
    terminal = run.get("Status") in {"ERROR", "STOPPED", "COMPLETED"}
    if run.get("Status") in {"ERROR", "STOPPED"} or (failures and terminal):
        return f"Workflow falhou: {run.get('Status')}, {failures}. Consulte Glue/CloudWatch; RunId={run['WorkflowRunId']}"
    return None


def download_publication(config, profile, sid, output):
    """Retorna recibo e relatório; ausência de recibo significa carga ainda não publicada."""
    receipt_path = output / "receipt.json"
    try:
        aws(
            profile,
            config["region"],
            "s3api",
            "get-object",
            "--bucket",
            config["data_bucket"],
            "--key",
            f"control/sources/{sid}.json",
            str(receipt_path),
        )
    except subprocess.CalledProcessError as error:
        if missing_object(error):
            return None
        raise
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    identifier = receipt.get("load_id", "")
    if (
        not re.fullmatch(r"[a-f0-9]{32}", identifier)
        or receipt.get("report_key") != f"reports/{identifier}/relatorio.html"
    ):
        raise ValueError("O recibo da carga contém referências de publicação inválidas.")
    report = output / "relatorio.html"
    aws(
        profile,
        config["region"],
        "s3api",
        "get-object",
        "--bucket",
        config["data_bucket"],
        "--key",
        receipt["report_key"],
        str(report),
    )
    return receipt, report


def wait_for_publication(config, profile, sid, output, *, timeout):
    """Espera a publicação sem confundir timeout de job com o limite do cliente."""
    if timeout <= 0:
        raise ValueError("O timeout de acompanhamento deve ser maior que zero.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    limit = time.monotonic() + timeout
    completed_at = None
    failure_at = None
    while time.monotonic() < limit:
        publication = download_publication(config, profile, sid, output)
        if publication:
            return publication
        run = find_workflow_run(config, profile, sid)
        if run:
            failure = workflow_failure(run)
            if failure:
                # Glue pode informar COMPLETED entre a falha e a tentativa automática.
                failure_at = failure_at if failure_at is not None else time.monotonic()
                if time.monotonic() - failure_at >= FAILURE_GRACE_SECONDS:
                    raise RuntimeError(failure)
            else:
                failure_at = None
            if run.get("Status") != "COMPLETED" or failure:
                completed_at = None
            if run.get("Status") == "COMPLETED" and not failure:
                # A execução pode concluir entre a leitura do recibo e esta consulta.
                completed_at = completed_at if completed_at is not None else time.monotonic()
                if time.monotonic() - completed_at >= RECEIPT_GRACE_SECONDS:
                    raise RuntimeError(
                        "Workflow terminou sem publicar o recibo da fonte. "
                        f"Consulte Glue/CloudWatch; RunId={run['WorkflowRunId']}"
                    )
        remaining = limit - time.monotonic()
        if remaining > 0:
            time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    if completed_at is not None:
        raise RuntimeError(
            "Workflow concluído, mas o recibo de publicação não foi encontrado. "
            "Consulte o manifesto e os logs antes de reenviar."
        )
    raise TimeoutError(
        "A espera terminou; a execução AWS pode continuar. Consulte o Glue Workflow "
        + config["workflow_name"]
    )


def open_report(report):
    """Falhas do navegador não alteram o resultado de uma carga já publicada."""
    try:
        if webbrowser.open(report.as_uri()):
            return
    except (OSError, webbrowser.Error):
        pass
    print("Abra o arquivo relatorio.html acima para visualizar os resultados.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Enviar Excel ao S3 e aguardar toda a pipeline Glue."
    )
    parser.add_argument("arquivo", nargs="?", type=Path)
    parser.add_argument("--profile", default="poc-aws")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--timeout", type=positive_int, default=3600)
    parser.add_argument("--sem-abrir", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.arquivo is None:
        from executar_pipeline import select_file

        args.arquivo = select_file()
    if args.arquivo is None:
        print("Seleção cancelada.")
        return 0
    source = validate_source(args.arquivo, config["max_source_bytes"])
    identity(args.profile, config["region"], config["account_id"])
    check_release(config, args.profile)
    upload = upload_source(source, config, args.profile)
    print(f"Arquivo enviado: s3://{upload['bucket']}/{upload['key']}", flush=True)
    print("Aguardando Glue Workflow: Bronze → Silver → Gold → Athena.", flush=True)
    receipt, report = wait_for_publication(
        config,
        args.profile,
        upload["source_id"],
        ROOT / "outputs_aws" / upload["source_id"],
        timeout=args.timeout,
    )
    print("Pipeline concluída. Load ID: " + receipt["load_id"])
    print("Relatório: " + str(report))
    if not args.sem_abrir:
        open_report(report)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        OSError,
        ValueError,
        RuntimeError,
        EOFError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(1)
