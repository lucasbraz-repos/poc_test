"""Prepara OIDC/estado remoto uma vez, usando o administrador local autorizado."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.aws_cli import aws, format_error, identity, terraform_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    if args.account_id != "909492399516":
        raise ValueError("O backend deste projeto está configurado para a conta 909492399516.")
    identity(args.profile, "us-east-1", args.account_id)
    environment = terraform_environment(args.profile)
    bootstrap = ROOT / "infra/bootstrap"
    state = bootstrap / "terraform.tfstate"
    managed = state.exists() and any(
        item["type"] == "aws_iam_openid_connect_provider" and item["mode"] == "managed"
        for item in json.loads(state.read_text()).get("resources", [])
    )
    providers = aws(args.profile, "us-east-1", "iam", "list-open-id-connect-providers")
    existing = next(
        (
            item["Arn"]
            for item in providers["OpenIDConnectProviderList"]
            if item["Arn"].endswith("/token.actions.githubusercontent.com")
        ),
        "",
    )
    command = ["terraform", "-chdir=" + str(bootstrap)]
    subprocess.run([*command, "init", "-input=false"], env=environment, check=True)
    subprocess.run([*command, "validate"], env=environment, check=True)
    build = ROOT / "build/aws"
    build.mkdir(parents=True, exist_ok=True)
    plan = build / "cicd-bootstrap.tfplan"
    subprocess.run(
        [
            *command,
            "plan",
            "-input=false",
            "-out=" + str(plan),
            "-var=aws_account_id=" + args.account_id,
            "-var=existing_oidc_provider_arn=" + ("" if managed else existing),
        ],
        env=environment,
        check=True,
    )
    if args.plan_only:
        return
    subprocess.run([*command, "apply", "-input=false", str(plan)], env=environment, check=True)
    pipeline = ROOT / "infra/terraform"
    previous = pipeline / "terraform.tfstate"
    backup = build / "state-backup-before-cicd.tfstate"
    metadata = pipeline / ".terraform/terraform.tfstate"
    already_remote = (
        metadata.exists()
        and json.loads(metadata.read_text()).get("backend", {}).get("type") == "s3"
    )
    if not already_remote and not previous.is_file():
        raise ValueError(
            "Estado local da pipeline ausente; não migrar ou recriar recursos automaticamente."
        )
    original = None
    if not already_remote:
        shutil.copyfile(previous, backup)
        original = json.loads(backup.read_text())
    subprocess.run(
        [
            "terraform",
            "-chdir=" + str(pipeline),
            "init",
            "-input=false",
            "-migrate-state",
            "-force-copy",
            "-backend-config=backend.hcl",
        ],
        env=environment,
        check=True,
    )
    result = subprocess.run(
        ["terraform", "-chdir=" + str(pipeline), "state", "pull"],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    remote = json.loads(result.stdout)
    if original and (
        remote["outputs"] != original["outputs"] or remote["resources"] != original["resources"]
    ):
        raise ValueError("Conferência da migração divergiu do backup local. Não execute deploy.")
    print(
        "OIDC configurado e estado da pipeline conferido no S3. Nenhuma credencial foi gravada no repositório."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(format_error(error), file=sys.stderr)
        sys.exit(1)
