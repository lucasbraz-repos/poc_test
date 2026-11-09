"""Compilação e importação dos jobs, sem iniciar Glue nem acessar arquivos de dados."""

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


class MedallionSyntaxTests(unittest.TestCase):
    def test_scripts_compile_and_import_without_starting_jobs(self):
        jobs = Path(__file__).resolve().parents[1] / "cloud/jobs"
        for stage in ("bronze", "silver", "gold"):
            with self.subTest(stage=stage):
                path = jobs / (stage + ".py")
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
                with patch(
                    "cloud.runtime.Runtime", side_effect=AssertionError("Importação iniciou o Glue")
                ):
                    namespace = runpy.run_path(str(path), run_name="syntax_check")
                self.assertTrue(callable(namespace[stage]))
