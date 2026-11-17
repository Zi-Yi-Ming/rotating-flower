"""Failure-stage evidence must preserve the actual exception, even on access denial."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import windows_setup as setup


class InstallDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_reports_registry_value_without_changing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            runtime=root/'runtimes/fixture'
            interpreter=runtime/'venv/Scripts/python.exe'
            interpreter.parent.mkdir(parents=True)
            interpreter.write_bytes(b'fixture, not executed')
            with patch.object(setup,'get_autostart',return_value='original startup command'), \
                 patch.object(setup,'set_autostart') as write:
                result=setup.installation_diagnostics(root,{'runtime':str(runtime)})
            self.assertTrue(result['autostart_registered'])
            self.assertEqual(result['autostart_command'],'original startup command')
            self.assertTrue(result['installed_python_exists'])
            write.assert_not_called()

    def test_diagnostics_records_query_denial_without_calling_it_missing(self):
        with patch.object(setup,'get_autostart',side_effect=PermissionError('query denied')):
            result=setup.installation_diagnostics(Path('.'),{'runtime':'missing-fixture-runtime'})
        self.assertIn('query denied',result['autostart_query_error'])
        self.assertNotIn('autostart_registered',result)

    def test_stage_records_original_error_and_filename(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as stderr:
            root=Path(directory)
            failure=PermissionError(13, 'Access denied', str(root/'venv'))
            with self.assertRaises(PermissionError) as caught:
                with setup.install_step(root,'create-virtual-environment'):
                    raise failure
            self.assertIs(caught.exception,failure)
            events=[json.loads(line) for line in (root/'logs/install-events.jsonl').read_text().splitlines()]
            self.assertEqual([e['event'] for e in events],['begin','failed'])
            self.assertEqual(events[-1]['filename'],str(root/'venv'))
            self.assertEqual(events[-1]['errno'],13)
            self.assertIn('create-virtual-environment',stderr.getvalue())

    def test_unwritable_log_does_not_mask_the_installation_failure(self):
        failure=PermissionError(13,'original access failure')
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            with patch.object(Path,'mkdir',side_effect=PermissionError('log denied')):
                with self.assertRaises(PermissionError) as caught:
                    with setup.install_step(Path(directory),'commit-config-and-autostart'):
                        raise failure
            self.assertIs(caught.exception,failure)

    def test_nested_stages_distinguish_dependency_from_outer_install_failure(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            root=Path(directory)
            with self.assertRaises(RuntimeError):
                with setup.install_step(root,'prepare-runtime'):
                    with setup.install_step(root,'install-serial-dependency'):
                        raise RuntimeError('dependency unavailable')
            events=[json.loads(line) for line in (root/'logs/install-events.jsonl').read_text().splitlines()]
            self.assertEqual([e['stage'] for e in events if e['event']=='failed'],
                             ['install-serial-dependency','prepare-runtime'])
