from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import json

import executar_pipeline as launcher


class LauncherTests(TestCase):
    def test_mac_selection_uses_native_dialog(self):
        with patch.object(launcher.platform, "system", return_value="Darwin"), patch.object(
            launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="/tmp/base com espaços.xlsx\n", stderr="")
        ) as call:
            self.assertEqual(launcher.select_file(), Path("/tmp/base com espaços.xlsx"))
            self.assertEqual(call.call_args.args[0][:2], ["osascript", "-e"])
            self.assertNotIn("shell", call.call_args.kwargs)

    def test_cancel_does_not_start_pipeline(self):
        with patch.object(launcher, "select_file", return_value=None), patch.object(launcher, "run_pipeline") as run, redirect_stdout(StringIO()):
            self.assertEqual(launcher.main([]), 0)
            run.assert_not_called()

    def test_mac_cancel_is_handled(self):
        with patch.object(launcher.platform, "system", return_value="Darwin"), patch.object(
            launcher.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="User canceled. (-128)")
        ):
            self.assertIsNone(launcher.select_file())

    def test_invalid_extension_does_not_start_pipeline(self):
        with patch.object(launcher, "run_pipeline") as run, redirect_stderr(StringIO()):
            self.assertEqual(launcher.main(["arquivo.csv"]), 1)
            run.assert_not_called()

    def test_missing_file_does_not_start_pipeline(self):
        with TemporaryDirectory() as temp, patch.object(launcher, "run_pipeline") as run, redirect_stderr(StringIO()):
            self.assertEqual(launcher.main([str(Path(temp) / "ausente.xlsx")]), 1)
            run.assert_not_called()

    def test_selection_runs_everything_and_opens_generated_report(self):
        with TemporaryDirectory() as temp:
            base = Path(temp) / "base.xlsx"
            base.touch()
            output = Path(temp) / "resultado"
            output.mkdir()
            (output / "qualidade.json").write_text(json.dumps({"counts": {"registros_unicos": 1, "APTO": 1, "REVISAO": 0, "QUARENTENA": 0}}))
            with patch.object(launcher, "select_file", return_value=base), patch.object(launcher, "run_pipeline", return_value=output) as run, patch.object(launcher.webbrowser, "open", return_value=True) as browser, redirect_stdout(StringIO()):
                self.assertEqual(launcher.main([]), 0)
                run.assert_called_once_with(base.resolve(), launcher.PROJECT / "outputs")
                browser.assert_called_once_with((output / "relatorio.html").as_uri())
