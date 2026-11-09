"""Compilação e importação dos jobs, sem iniciar Glue nem acessar arquivos de dados."""

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


class MedallionSyntaxTests(unittest.TestCase):
    def check_stage(self, stage):
        jobs = Path(__file__).resolve().parents[1] / "cloud/jobs"
        path = jobs / (stage + ".py")
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
        with patch(
            "cloud.runtime.Runtime", side_effect=AssertionError("Importação iniciou o Glue")
        ):
            namespace = runpy.run_path(str(path), run_name="syntax_check")
        self.assertTrue(callable(namespace[stage]))

    def test_bronze(self):
        self.check_stage("bronze")

    def test_silver(self):
        self.check_stage("silver")

    def test_gold(self):
        self.check_stage("gold")
