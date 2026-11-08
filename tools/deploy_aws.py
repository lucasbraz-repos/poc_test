"""Implanta a pipeline e cria as views no Athena; conta de destino obrigatória."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.aws_cli import athena_query, aws, format_error, identity, terraform_environment
from tools.config import DEFAULT_CONFIG, load_config, positive_int, validate_config
from tools.package_aws import package


def deployment_target(profile, region, account_id):
    """Descobre recursos existentes sem depender de credenciais no estado local."""
    if DEFAULT_CONFIG.exists():
        config = load_config(DEFAULT_CONFIG)
        if config["account_id"] != account_id or config["region"] != region:
            raise ValueError(
                "deployment.json pertence a outra conta ou região. Separe a configuração e "
                "o estado Terraform antes de implantar em outro destino."
            )
        return config
    return {"account_id": account_id, "region": region, "workflow_name": "poc-carteira-dev"}


def assert_deployment_idle(config, profile):
    """Recusa trocar a release enquanto houver carga executando ou aguardando entrada."""
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
        try:
            response = aws(profile, config["region"], *arguments)
        except subprocess.CalledProcessError as error:
            if "EntityNotFoundException" not in (error.stderr or ""):
                raise
            break
        active = [
            run["WorkflowRunId"]
            for run in response.get("Runs", [])
            if run.get("Status") in {"RUNNING", "STOPPING"}
        ]
        if active:
            raise RuntimeError(
                "Deploy bloqueado: há workflow em execução. Aguarde a conclusão: "
                + ", ".join(active)
            )
        token = response.get("NextToken")
        if not token:
            break
    # O workflow pode parecer concluído entre tentativas automáticas de um job.
    for stage in ("bronze", "silver", "gold"):
        try:
            jobs = aws(
                profile,
                config["region"],
                "glue",
                "get-job-runs",
                "--job-name",
                config["workflow_name"] + "-" + stage,
                "--max-results",
                "10",
            )["JobRuns"]
        except subprocess.CalledProcessError as error:
            if "EntityNotFoundException" not in (error.stderr or ""):
                raise
            continue
        if any(
            job.get("JobRunState") in {"STARTING", "WAITING", "RUNNING", "STOPPING"} for job in jobs
        ):
            raise RuntimeError(
                "Deploy bloqueado: há job em execução ou aguardando tentativa: " + stage
            )

    queue_url = config.get("queue_url")
    if not queue_url:
        try:
            queue_url = aws(
                profile,
                config["region"],
                "sqs",
                "get-queue-url",
                "--queue-name",
                config["workflow_name"] + "-input",
            )["QueueUrl"]
        except subprocess.CalledProcessError as error:
            if not any(
                code in (error.stderr or "") for code in ("QueueDoesNotExist", "NonExistentQueue")
            ):
                raise
            return
    attributes = aws(
        profile,
        config["region"],
        "sqs",
        "get-queue-attributes",
        "--queue-url",
        queue_url,
        "--attribute-names",
        "ApproximateNumberOfMessages",
        "ApproximateNumberOfMessagesNotVisible",
        "ApproximateNumberOfMessagesDelayed",
    )["Attributes"]
    pending = sum(
        int(attributes.get(name, 0))
        for name in (
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        )
    )
    if pending:
        raise RuntimeError(
            f"Deploy bloqueado: há {pending} mensagem(ns) aguardando ou em processamento na fila. "
            "Aguarde a drenagem da entrada antes de atualizar a release."
        )


def wait_for_idle(config, profile, minutes=0):
    """Aguarda uma janela livre; erros de credencial/permissão falham imediatamente."""
    deadline = time.monotonic() + minutes * 60
    while True:
        try:
            assert_deployment_idle(config, profile)
            return
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                raise
            print(str(error) + " Nova tentativa em até 30 segundos.", flush=True)
            time.sleep(min(30, max(0, deadline - time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Implantar S3, Glue, Data Catalog, Athena e entrada automática."
    )
    parser.add_argument("--profile", help="Perfil local; omitir para credenciais OIDC do ambiente")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--plan-only", action="store_true", help="Validar e salvar plano sem criar recursos"
    )
    parser.add_argument("--wait-idle-minutes", type=positive_int, default=0)
    args = parser.parse_args(argv)
    identity(args.profile, args.region, args.account_id)
    target = deployment_target(args.profile, args.region, args.account_id)
    wait_for_idle(target, args.profile, args.wait_idle_minutes)
    release = package()
    environment = terraform_environment(args.profile)
    command = ["terraform", "-chdir=" + str(ROOT / "infra/terraform")]

    def run(*parts, **kwargs):
        return subprocess.run([*command, *parts], env=environment, check=True, **kwargs)

    run("init", "-input=false", "-lockfile=readonly", "-backend-config=backend.hcl")
    run("validate")
    plan = ROOT / "build/aws/deploy.tfplan"
    log = ROOT / "build/aws/terraform-plan.txt"
    with log.open("w", encoding="utf-8") as stream:
        run(
            "plan",
            "-input=false",
            "-no-color",
            "-out=" + str(plan),
            "-var=aws_account_id=" + args.account_id,
            "-var=aws_region=" + args.region,
            "-var=release_id=" + release,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    print("Plano Terraform: " + str(log), flush=True)
    if args.plan_only:
        print("Plano preparado; nenhum recurso foi criado.")
        return 0
    wait_for_idle(target, args.profile, args.wait_idle_minutes)
    run("apply", "-input=false", "-auto-approve", str(plan))
    outputs = json.loads(run("output", "-json", capture_output=True, text=True).stdout)
    config = validate_config({key: value["value"] for key, value in outputs.items()})
    DEFAULT_CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    sql = (ROOT / "sql/athena_views.sql").read_text(encoding="utf-8")
    for layer, database in config["databases"].items():
        sql = sql.replace("__" + layer.upper() + "_DATABASE__", database)
    for statement in sql.split(";"):
        if statement.strip():
            athena_query(config, args.profile, statement)
    print("\nInfraestrutura e views prontas.")
    print("Entrada: s3://" + config["data_bucket"] + "/landing/")
    print("Workflow: " + config["workflow_name"])
    print("Athena: " + config["databases"]["gold"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(error.returncode if isinstance(error, subprocess.CalledProcessError) else 1)
