import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import yaml

import main
from app.fetch import ApiError, fetch_vacancies, load_local, unwrap
from app.llm import (LLMError, LLMUnavailable, parse_model_response, analyze_vacancy,
                     cache_key, validate_quotes)
from app.normalize import normalize_all
from tools.verify_model import matches_expectation

RAW = load_local('data/kantkod_sample_vacancies.json')
VACANCIES = {v['id']: v for v in normalize_all(RAW)}
CFG = yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8'))


def duty_issue(v):
    quote = v['duty']
    return {'topic': 'обязанности', 'type': 'ambiguous', 'field': 'duty',
            'quote': quote, 'values': [], 'problem': 'Обязанности не конкретизированы',
            'recommendation': 'Какие именно задачи?', 'source': 'llm'}


def contradiction(v):
    duty = 'Работа полностью дистанционная из любого города.'
    office = 'Обязательное ежедневное присутствие в офисе в Калининграде к 09:00.'
    return {'topic': 'место работы', 'type': 'contradiction', 'field': 'duty',
            'quote': duty, 'values': [duty, office], 'problem': 'Форматы различаются',
            'recommendation': 'Какой формат работы?', 'source': 'llm'}


class ContractTests(unittest.TestCase):
    def test_expected_training_payloads_have_real_normalized_quotes(self):
        examples = {
            '002': [duty_issue(VACANCIES['DEMO-39-002'])],
            '004': [contradiction(VACANCIES['DEMO-39-004'])],
            '006': [duty_issue(VACANCIES['DEMO-39-006'])],
            '008': [{'topic': 'график', 'type': 'ambiguous', 'field': 'requirement.qualification',
                     'quote': 'Возможен гибкий график без уточнения правил.', 'values': [],
                     'problem': 'Неясно, как действует гибкость', 'recommendation': 'Какие правила гибкости?'}],
            '010': [{'topic': 'место работы', 'type': 'ambiguous', 'field': 'requirement.qualification',
                     'quote': 'Формат и адрес рабочего места уточняются при собеседовании.', 'values': [],
                     'problem': 'Формат и адрес уточняются', 'recommendation': 'Какой формат и адрес?'}],
        }
        for suffix, issues in examples.items():
            v = VACANCIES['DEMO-39-' + suffix]
            with self.subTest(suffix=suffix):
                result = validate_quotes(issues, v)
                self.assertTrue(matches_expectation(v, result))
                self.assertTrue(all(i['source'] == 'llm' for i in result))
        for suffix in ['001', '005', '007', '009']:
            self.assertTrue(matches_expectation(VACANCIES['DEMO-39-' + suffix], []))

    def test_rejects_invalid_quotes_and_schema(self):
        v = VACANCIES['DEMO-39-004']
        good = contradiction(v)
        for change in [
            {'field': 'nonexistent'}, {'field': 'requirement.qualification'},
            {'quote': good['quote'].lower()}, {'topic': 'адрес'}, {'type': 'missing'},
            {'values': ['вымышленная цитата', good['quote']]}, {'values': [good['quote']]},
            {'problem': ''}, {'recommendation': None}, {'values': {}},
        ]:
            with self.subTest(change=change), self.assertRaises(LLMError):
                validate_quotes([{**good, **change}], v)
        self.assertIsNone(validate_quotes(None, v))
        self.assertEqual(validate_quotes([], v), [])

    def test_template_copy_and_nested_quotes_rejected(self):
        v = VACANCIES['DEMO-39-004']
        good = contradiction(v)
        variants = [{**good, 'problem': 'что неясно'},
                    {**good, 'recommendation': 'конкретный вопрос'},
                    {**good, 'quote': 'полностью дистанционная',
                     'values': ['полностью дистанционная', good['quote']]}]
        for item in variants:
            with self.subTest(item=item), self.assertRaises(LLMError):
                validate_quotes([item], v)

    def test_parse_json_and_error_not_empty(self):
        self.assertEqual(parse_model_response('```json\n[]\n```'), [])
        for text in ['', '{}', 'oops', '[', '[1] trailing']:
            with self.subTest(text=text), self.assertRaises(LLMError):
                parse_model_response(text)

    def test_service_request_model_config_timeout_and_failure(self):
        cfg = {'model': 'test-model', 'base_url': 'http://model/v1', 'timeout': 3}
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {'choices': [{'message': {'content': '[]'}, 'finish_reason': 'stop'}]}
        with patch('app.llm.requests.post', return_value=R()) as post:
            self.assertEqual(analyze_vacancy(VACANCIES['DEMO-39-001'], cfg), [])
            self.assertEqual(post.call_args.args[0], 'http://model/v1/chat/completions')
            self.assertEqual(post.call_args.kwargs['json']['model'], 'test-model')
            self.assertEqual(post.call_args.kwargs['timeout'], 3)
        with patch('app.llm.requests.post', side_effect=requests.Timeout()):
            with self.assertRaises(LLMUnavailable):
                analyze_vacancy(VACANCIES['DEMO-39-001'], cfg)
        class Truncated(R):
            def json(self): return {'choices': [{'message': {'content': '[]'}, 'finish_reason': 'length'}]}
        with patch('app.llm.requests.post', return_value=Truncated()), self.assertRaises(LLMError):
            analyze_vacancy(VACANCIES['DEMO-39-001'], cfg)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'cache.json')
        self.cfg = copy.deepcopy(CFG)
        self.cfg['output']['report_path'] = str(Path(self.temp.name) / 'report.json')
        self.patcher = patch.object(main, 'CACHE_PATH', self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def run_one(self):
        return main.run_pipeline([RAW[1]], 'check', self.cfg)

    def test_empty_success_cached_and_disabled_never_reads_it(self):
        with patch.object(main, 'analyze_vacancy', return_value=[]) as model:
            vacs, _ = self.run_one()
            self.assertTrue(vacs[0]['_llm_checked'])
            self.run_one()
            self.assertEqual(model.call_count, 1)
        self.assertEqual(list(json.loads(Path(self.path).read_text()).values()), [[]])
        self.cfg['llm']['enabled'] = False
        with patch.object(main, 'analyze_vacancy', side_effect=AssertionError('called')) as model:
            vacs, issues = self.run_one()
            self.assertFalse(vacs[0]['_llm_checked'])
            self.assertFalse(any(x.get('source') == 'llm' for x in issues[vacs[0]['id']]))
            model.assert_not_called()

    def test_failures_not_cached_or_reported_as_checked(self):
        for result in [None, LLMError('broken JSON'), LLMUnavailable('timeout')]:
            with self.subTest(result=result):
                Path(self.path).unlink(missing_ok=True)
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(main, 'analyze_vacancy', **kwargs):
                    vacs, issues = self.run_one()
                self.assertFalse(vacs[0]['_llm_checked'])
                self.assertFalse(any(x.get('source') == 'llm' for x in issues[vacs[0]['id']]))
                self.assertFalse(Path(self.path).exists())

    def test_old_and_invalid_cache_do_not_short_circuit(self):
        v = normalize_all([RAW[1]])[0]
        key = cache_key(v, self.cfg['llm'])
        for cached in [None, [{'topic': 'fake'}]]:
            Path(self.path).write_text(json.dumps({key: cached, self.cfg['llm']['model'] + ':' + v['content_hash']: []}))
            with patch.object(main, 'analyze_vacancy', return_value=[duty_issue(v)]) as model:
                _, issues = self.run_one()
                model.assert_called_once()
                self.assertTrue(any(i.get('source') == 'llm' for i in issues[v['id']]))
        Path(self.path).write_text(json.dumps({self.cfg['llm']['model'] + ':' + v['content_hash']: []}))
        with patch.object(main, 'analyze_vacancy', return_value=[]) as model:
            self.run_one()
            model.assert_called_once()

    def test_unavailable_still_uses_other_cached_vacancies(self):
        other = normalize_all([RAW[2]])[0]
        key = cache_key(other, self.cfg['llm'])
        Path(self.path).write_text(json.dumps({key: []}))
        with patch.object(main, 'analyze_vacancy', side_effect=LLMUnavailable('offline')) as model:
            vacancies, issues = main.run_pipeline([RAW[1], RAW[2]], 'check', self.cfg)
            model.assert_called_once()
            self.assertFalse(vacancies[0]['_llm_checked'])
            self.assertTrue(vacancies[1]['_llm_checked'])

    def test_cache_equals_report_disabled(self):
        self.cfg['output']['report_path'] = self.path
        with patch.object(main, 'analyze_vacancy', return_value=[]) as model:
            self.run_one(); self.run_one()
            self.assertEqual(model.call_count, 2)
            self.assertFalse(Path(self.path).exists())


class FetchTests(unittest.TestCase):
    def test_invalid_response_is_not_empty_success(self):
        for value in ({'error': 'failure'}, {}, {'results': {'vacancies': 'bad'}}, [42]):
            with self.subTest(value=value), self.assertRaises(ApiError):
                unwrap(value)
        self.assertEqual(unwrap({'results': {'vacancies': []}}), [])
        # Пустая страница API: конец данных, а не ошибка
        self.assertEqual(unwrap({'status': '200', 'results': {}}), [])

    def test_page_number_and_repeated_page(self):
        pages = [[{'id': '1'}, {'id': '2'}], [{'id': '3'}, {'id': '4'}]]
        with patch('app.fetch.fetch_page', side_effect=pages) as page:
            self.assertEqual(len(fetch_vacancies('x', 2, 4)), 4)
            self.assertEqual([call.args[1] for call in page.call_args_list], [0, 1])
        with patch('app.fetch.fetch_page', return_value=[{'id': '1'}, {'id': '2'}]):
            with self.assertRaises(ApiError):
                fetch_vacancies('x', 2, 4)


class LiveHTTPTests(unittest.TestCase):
    def test_roundtrip_openai_compatible_server(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        v = VACANCIES['DEMO-39-004']
        payload = [contradiction(v)]
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                data = json.dumps({'choices': [{'message': {'content': json.dumps(payload, ensure_ascii=False)},
                                                'finish_reason': 'stop'}]}, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = {'model': 'fake-model', 'base_url': f'http://127.0.0.1:{server.server_port}/v1'}
            self.assertEqual(validate_quotes(analyze_vacancy(v, cfg), v), payload)
            self.assertEqual(observed[0][0], '/v1/chat/completions')
            self.assertEqual(observed[0][1]['model'], 'fake-model')
            fields = json.loads(observed[0][1]['messages'][1]['content'])
            self.assertEqual(fields['duty'], v['duty'])
            self.assertNotIn('company.name', fields)
        finally:
            server.shutdown(); server.server_close(); thread.join()


class PartialAcceptanceTests(unittest.TestCase):
    def test_bad_item_dropped_good_kept(self):
        v = VACANCIES['DEMO-39-002']
        good = {'topic': 'обязанности', 'type': 'ambiguous', 'field': 'duty',
                'quote': 'различных поручений', 'values': [],
                'problem': 'Не указан перечень поручений', 'recommendation': 'Какие поручения входят в работу?'}
        bad = {**good, 'quote': 'различные поручения'}
        self.assertEqual(len(validate_quotes([good, bad], v)), 1)

    def test_all_items_bad_is_error_not_empty(self):
        v = VACANCIES['DEMO-39-002']
        bad = {'topic': 'обязанности', 'type': 'ambiguous', 'field': 'duty',
               'quote': 'нет такого текста', 'values': [], 'problem': 'x', 'recommendation': 'y'}
        with self.assertRaises(LLMError):
            validate_quotes([bad], v)


if __name__ == '__main__':
    unittest.main()
