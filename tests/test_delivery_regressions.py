import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.fetch import load_local
from app.llm import LLMError, validate_quotes, cache_key
from app.normalize import normalize, normalize_all
from app.report import save_report


class DeliveryRegressions(unittest.TestCase):
    def test_missing_id_fails_and_duplicate_ids_collapse(self):
        with self.assertRaisesRegex(ValueError, 'id'):
            normalize_all([{'id': 'one'}, {'duty': 'x'}])
        result = normalize_all([{'id': 123, 'duty': 'first'}, {'id': '123', 'duty': 'last'}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['duty'], 'last')

    def test_ai_quote_must_be_a_substring_of_original(self):
        raw = {'id': 'test', 'duty': 'Работа  с\nдокументами'}
        v = normalize(raw)
        issue = {'topic': 'обязанности', 'type': 'ambiguous', 'field': 'duty',
                 'quote': 'Работа  с\nдокументами', 'values': [],
                 'problem': 'Неясны задачи', 'recommendation': 'Какие задачи?'}
        self.assertEqual(validate_quotes([issue], v)[0]['quote'], issue['quote'])
        with self.assertRaises(LLMError):
            validate_quotes([{**issue, 'quote': 'Работа с документами'}], v)
        other = normalize({'id': 'test', 'duty': 'Работа с документами'})
        self.assertNotEqual(cache_key(v, {'model': 'test', 'base_url': 'http://localhost/v1'}), cache_key(other, {'model': 'test', 'base_url': 'http://localhost/v1'}))

    def test_basename_report_path(self):
        with tempfile.TemporaryDirectory() as d:
            script = "from app.report import save_report; save_report([], {}, 'check', None, 'report.json')"
            import os
            env = dict(os.environ, PYTHONPATH=str(Path.cwd()))
            subprocess.run([sys.executable, '-c', script], cwd=d, env=env, check=True, capture_output=True)
            self.assertTrue(Path(d, 'report.json').is_file())

    def test_cli_local_file_no_ai_and_previous_report_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d, 'report.json')
            config = Path(d, 'config.yaml')
            config.write_text('llm:\n  enabled: true\n  model: fake\noutput:\n  report_path: ' + json.dumps(str(report)) + '\n', encoding='utf-8')
            cmd = [sys.executable, 'main.py', 'check', '--config', str(config), '--no-ai', '--input']
            good = subprocess.run(cmd + ['data/kantkod_sample_vacancies.json'], capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(len(data['vacancies']), len(load_local('data/kantkod_sample_vacancies.json')))
            self.assertTrue(all(v['llm_status'] == 'disabled' for v in data['vacancies']))
            previous = report.read_bytes()
            invalid = Path(d, 'invalid.json')
            invalid.write_text('[{"duty": "no id"}]', encoding='utf-8')
            bad = subprocess.run(cmd + [str(invalid)], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertEqual(report.read_bytes(), previous)

class ScheduleRegressions(unittest.TestCase):
    def test_first_run_and_failed_fetch_do_not_replace_report(self):
        from unittest.mock import patch
        import main
        from app.fetch import ApiError
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, 'report.json')
            path.write_text('old report', encoding='utf-8')
            cfg = {'llm': {'enabled': False}, 'schedule': {'every_hours': 1},
                   'output': {'report_path': str(path)}}
            with patch.object(sys, 'argv', ['main.py', 'schedule']), \
                 patch.object(main, 'load_config', return_value=cfg), \
                 patch.object(main, 'fetch_with_retries', side_effect=ApiError('offline')) as fetch, \
                 patch.object(main.time, 'sleep', side_effect=KeyboardInterrupt):
                with self.assertRaises(SystemExit):
                    main.main()
                fetch.assert_called_once()
            self.assertEqual(path.read_text(encoding='utf-8'), 'old report')

    def test_first_run_saves_successful_report(self):
        from unittest.mock import patch
        import main
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, 'report.json')
            cfg = {'llm': {'enabled': False}, 'schedule': {'every_hours': 1},
                   'output': {'report_path': str(path)}}
            with patch.object(sys, 'argv', ['main.py', 'schedule']), \
                 patch.object(main, 'load_config', return_value=cfg), \
                 patch.object(main, 'fetch_with_retries', return_value=[{'id': 's1'}]), \
                 patch.object(main.time, 'sleep', side_effect=KeyboardInterrupt):
                with self.assertRaises(SystemExit) as exit_status:
                    main.main()
                self.assertEqual(exit_status.exception.code, 0)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['vacancies'][0]['id'], 's1')
