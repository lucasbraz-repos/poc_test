"""Executa testes Spark com spark-submit e recusa uma suíte apenas ignorada."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests_aws"), pattern="test_spark_jobs.py"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() and result.testsRun >= 5 and not result.skipped else 1)
