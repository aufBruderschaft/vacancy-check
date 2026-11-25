"""Ручная проверка модели на учебных вакансиях; ни один ответ не кэшируется."""
import argparse
import json
import sys
from pathlib import Path

import yaml

from app.fetch import load_local
from app.llm import LLMAllRejected, LLMError, LLMUnavailable, analyze_vacancy, validate_quotes
from app.normalize import normalize_all

CLEAN = {'DEMO-39-001', 'DEMO-39-005', 'DEMO-39-007', 'DEMO-39-009'}
EXPECTED = {
    'DEMO-39-002': ('обязанности', 'duty'),
    'DEMO-39-004': ('место работы', 'duty'),
    'DEMO-39-006': ('обязанности', 'duty'),
    'DEMO-39-008': ('график', 'requirement.qualification'),
    'DEMO-39-010': ('место работы', 'requirement.qualification'),
}


def matches_expectation(v, issues):
    identifier = v['id']
    if identifier in CLEAN:
        return not issues
    if identifier not in EXPECTED:
        return None
    topic, field = EXPECTED[identifier]
    for issue in issues:
        fields = {field}
        if identifier == 'DEMO-39-004':
            fields.update({'requirement.qualification', 'addresses'})
        if identifier == 'DEMO-39-008':
            fields.add('schedule')
        if issue.get('topic') != topic or issue.get('field') not in fields:
            continue
        if identifier == 'DEMO-39-004':
            values = issue.get('values', [])
            if issue.get('type') != 'contradiction':
                continue
            # Удалёнка в duty против офиса: в требованиях или в адресе
            office = (v.get('requirement.qualification') or '') + ' ' + (v.get('addresses') or '')
            if not (any(x in (v.get('duty') or '') for x in values) and
                    any(x in office for x in values)):
                continue
        return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='data/kantkod_sample_vacancies.json')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--output', help='Необязательный JSON с ответами модели')
    args = parser.parse_args(argv)
    try:
        with open(args.config, encoding='utf-8') as f:
            config = yaml.safe_load(f)
        cfg = config['llm']
        if not cfg.get('enabled'):
            raise ValueError('llm.enabled выключен; модель не проверялась')
        vacancies = normalize_all(load_local(args.input))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Не удалось подготовить проверку: {exc}', file=sys.stderr)
        return 2
    present = {v['id'] for v in vacancies}
    missing = sorted((CLEAN | set(EXPECTED)) - present)
    if missing:
        print('Нет ожидаемых учебных вакансий: ' + ', '.join(missing), file=sys.stderr)
        return 2

    results = []
    unavailable = False
    for vacancy in vacancies:
        if unavailable:
            results.append({'id': vacancy['id'], 'error': 'Сервис недоступен; запрос пропущен'})
            continue
        try:
            try:
                issues = validate_quotes(analyze_vacancy(vacancy, cfg), vacancy)
            except LLMAllRejected:
                issues = []  # как в main.py: неподтверждённые замечания не выводятся
            if issues is None:
                raise LLMError('Модель не вернула результат')
            matched = matches_expectation(vacancy, issues)
            results.append({'id': vacancy['id'], 'meets_shape_check': matched, 'issues': issues})
        except LLMUnavailable as exc:
            unavailable = True
            results.append({'id': vacancy['id'], 'error': str(exc)})
        except (LLMError, OSError, ValueError, TypeError) as exc:
            results.append({'id': vacancy['id'], 'error': str(exc)})

    report = {'model': cfg.get('model'), 'results': results,
              'note': 'Проверка формы и ожидаемой темы не доказывает правильность смысла; '
                      'все замечания нужно проверить вручную.'}
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + '\n', encoding='utf-8')
        print(f'Проверка сохранена: {path}')
    else:
        print(content)
    if any('error' in item for item in results):
        return 2
    return 1 if any(item.get('meets_shape_check') is False for item in results) else 0


if __name__ == '__main__':
    sys.exit(main())
