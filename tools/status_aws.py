"""Progresso das cargas recebidas; não imprime conteúdo da carteira."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.aws_cli import aws, identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="poc-aws")
    args = parser.parse_args()
    config = json.loads((ROOT / "build/aws/deployment.json").read_text())
    identity(args.profile, config["region"], config["account_id"])
    response = aws(args.profile, config["region"], "glue", "get-workflow-runs", "--name", config["workflow_name"],
                   "--max-results", "20", "--include-graph")
    runs = []
    for run in response.get("Runs", []):
        if not run.get("WorkflowRunProperties", {}).get("source_id"):
            continue
        jobs = []
        for node in run.get("Graph", {}).get("Nodes", []):
            if node["Type"] != "JOB":
                continue
            attempts = node.get("JobDetails", {}).get("JobRuns", [])
            current = max(attempts, key=lambda r:r.get("StartedOn", "")) if attempts else {}
            jobs.append({"name": node["Name"], "state": current.get("JobRunState", "AGUARDANDO"),
                         "error": current.get("ErrorMessage")})
        runs.append({"run_id": run["WorkflowRunId"], "status": run["Status"], "statistics": run["Statistics"], "jobs": jobs})
    print(json.dumps(runs, indent=2, ensure_ascii=False))


if __name__ == "__main__":main()
