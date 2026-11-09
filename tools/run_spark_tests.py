"""Executa testes Spark com spark-submit e recusa uma suíte apenas ignorada."""

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "jobs", "smoke"), default="all")
    parser.add_argument("--layer", choices=("all", "bronze", "silver", "gold"), default="all")
    args = parser.parse_args()
    if args.layer != "all" and args.suite != "smoke":
        parser.error("--layer exige --suite smoke")
    suite = unittest.TestSuite()
    for name, filename in (("jobs", "test_spark_jobs.py"), ("smoke", "test_spark_smoke.py")):
        if args.suite not in ("all", name):
            continue
        if name == "smoke" and args.layer != "all":
            discovered = unittest.defaultTestLoader.loadTestsFromName(
                "tests_aws.test_spark_smoke.MedallionSmokeTests.test_" + args.layer
            )
        else:
            discovered = unittest.defaultTestLoader.discover(
                str(ROOT / "tests_aws"), pattern=filename
            )
        if not discovered.countTestCases():
            parser.error("Nenhum teste encontrado em " + filename)
        suite.addTests(discovered)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1)
