"""Implanta a pipeline e cria as views no Athena; conta de destino obrigatória."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_aws import package
from aws_cli import identity, terraform_environment, athena_query


def main():
    parser = argparse.ArgumentParser(description="Implantar S3, Glue, Data Catalog, Athena e entrada automática.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--plan-only", action="store_true", help="Validar e salvar plano sem criar recursos")
    args = parser.parse_args()
    identity(args.profile, args.region, args.account_id)
    release = package()
    environment = terraform_environment(args.profile)
    command = ["terraform", "-chdir=" + str(ROOT / "infra/terraform")]
    run = lambda *parts, **kw: subprocess.run([*command, *parts], env=environment, check=True, **kw)
    run("init", "-input=false")
    run("validate")
    plan = str(ROOT / "build/aws/deploy.tfplan")
    log = ROOT / "build/aws/terraform-plan.txt"
    with log.open("w") as stream:
        run("plan", "-input=false", "-no-color", "-out=" + plan, "-var=aws_account_id=" + args.account_id,
            "-var=aws_region=" + args.region, "-var=release_id=" + release, stdout=stream, stderr=subprocess.STDOUT)
    print("Plano Terraform: " + str(log), flush=True)
    if args.plan_only:
        print("Plano preparado; nenhum recurso foi criado.")
        return 0
    run("apply", "-input=false", "-auto-approve", plan)
    outputs = json.loads(run("output", "-json", capture_output=True, text=True).stdout)
    config = {key: value["value"] for key, value in outputs.items()}
    (ROOT / "build/aws/deployment.json").write_text(json.dumps(config, indent=2) + "\n")
    sql = (ROOT / "sql/athena_views.sql").read_text()
    for layer, database in config["databases"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", database):
            raise ValueError("Nome de database inválido.")
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
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(error.returncode)
