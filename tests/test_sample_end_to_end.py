"""Интеграционный тест с имитацией ответов модели, не оценка качества LLM."""
import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml
import main
from app.fetch import load_local
from app.normalize import normalize_all


class SampleEndToEnd(unittest.TestCase):
    def test_ten_vacancies_mock_service_rules_model_and_cache(self):
        raw = load_local('data/kantkod_sample_vacancies.json')
        vacancies = normalize_all(raw)
        cases = {
            'DEMO-39-002': ('обязанности', 'duty', 'Выполнение различных поручений руководителя.'),
            'DEMO-39-006': ('обязанности', 'duty', 'Работа с документами и другие задачи по необходимости.'),
            'DEMO-39-008': ('график', 'requirement.qualification', 'Возможен гибкий график без уточнения правил.'),
            'DEMO-39-010': ('место работы', 'requirement.qualification', 'Формат и адрес рабочего места уточняются при собеседовании.'),
        }
        answers = {}
        for v in vacancies:
            case = cases.get(v['id'])
            if case:
                topic, field, quote = case
                answers[v['duty']] = [{'topic': topic, 'type': 'ambiguous', 'field': field, 'quote': quote,
                                       'values': [], 'problem': 'Условия требуют уточнения',
                                       'recommendation': 'Что именно предполагается?'}]
            elif v['id'] == 'DEMO-39-004':
                q1 = 'Работа полностью дистанционная из любого города.'
                q2 = 'Обязательное ежедневное присутствие в офисе в Калининграде к 09:00.'
                answers[v['duty']] = [{'topic': 'место работы', 'type': 'contradiction', 'field': 'duty',
                                       'quote': q1, 'values': [q1, q2], 'problem': 'Условия различаются',
                                       'recommendation': 'Какой формат работы?'}]
            else:
                answers[v['duty']] = []
        requests_seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fields = json.loads(data['messages'][1]['content'])
                requests_seen.append(fields)
                text = json.dumps(answers[fields['duty']], ensure_ascii=False)
                body = json.dumps({'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}]},
                                  ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = copy.deepcopy(yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8')))
            cfg['llm'].update({'model': 'fake', 'base_url': f'http://127.0.0.1:{server.server_port}/v1'})
            with tempfile.TemporaryDirectory() as tmp, patch.object(main, 'CACHE_PATH', tmp + '/cache.json'):
                result, issues = main.run_pipeline(raw, 'check', cfg)
                self.assertEqual(len(requests_seen), 10)
                for v in result:
                    self.assertTrue(v['_llm_checked'])
                    llm = [i for i in issues[v['id']] if i['source'] == 'llm']
                    if v['id'][-3:] in ('002', '004', '006', '008', '010'):
                        self.assertEqual(len(llm), 1)
                    elif v['id'][-3:] in ('001', '005', '007', '009'):
                        self.assertEqual(llm, [])
                    for i in llm:
                        self.assertIn(i['quote'], v[i['field']])
                self.assertTrue(any(i['source'] == 'rules' and i['type'] == 'contradiction'
                                    for i in issues['DEMO-39-003']))
                second, _ = main.run_pipeline(raw, 'check', cfg)
                self.assertEqual(len(requests_seen), 10)
                self.assertTrue(all(v['_llm_status'] == 'cache' for v in second))
                cfg['llm']['enabled'] = False
                third, issues = main.run_pipeline(raw, 'check', cfg)
                self.assertEqual(len(requests_seen), 10)
                self.assertTrue(all(not v['_llm_checked'] for v in third))
                self.assertFalse(any(i.get('source') == 'llm' for values in issues.values() for i in values))
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
