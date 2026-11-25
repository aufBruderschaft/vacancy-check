import hashlib
import json
import re

FIELDS = [
    'id', 'vac_url', 'job-name', 'company.name',
    'duty', 'requirements',
    'requirement.qualification', 'requirement.education', 'requirement.experience',
    'qualification', 'skills', 'medicalDocuments',
    'salary', 'salary_min', 'salary_max', 'term.text',
    'schedule', 'scheduleTypeComment', 'shift', 'employment',
    'conditions', 'date_modify',
] #контакты убрал специально, ибо это персональная инфа

def get_path(vacancy, path):
    value = vacancy
    for key in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value

def clean(value):
    if isinstance(value, str):
        value = re.sub(r'\s+', ' ', value).strip()
        return value or None
    if isinstance (value, list):
        items = [clean(item) for item in value]
        return [item for item in items if item]
    return value

def join_addresses(vacancy):
    items = get_path(vacancy, 'addresses.address') or []
    locations = [clean(a.get('location')) for a in items if isinstance(a, dict)]
    return '; '.join(loc.strip(' ;') for loc in locations if loc) or None

def join_benefits(vacancy):
    parts = [clean(vacancy.get('benefit')), clean(vacancy.get('benefitDetails'))]
    return ': '.join(p for p in parts if p) or None

def normalize(vacancy):
    record = {path: clean(get_path(vacancy, path)) for path in FIELDS}
    record['addresses'] = join_addresses(vacancy)
    record['benefits'] = join_benefits(vacancy)
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False)
    record['content_hash'] = hashlib.sha1(raw.encode('utf-8')).hexdigest()
    source_texts = {}
    for path in FIELDS:
        value = get_path(vacancy, path)
        if isinstance(value, str) and value.strip():
            source_texts[path] = [value]
    addresses = get_path(vacancy, 'addresses.address') or []
    if isinstance(addresses, dict):
        addresses = [addresses]
    if isinstance(addresses, list):
        locations = [item.get('location') for item in addresses if isinstance(item, dict)]
        source_texts['addresses'] = [x for x in locations if isinstance(x, str) and x.strip()]
    source_texts['benefits'] = [vacancy[k] for k in ('benefit', 'benefitDetails')
                                if isinstance(vacancy.get(k), str) and vacancy[k].strip()]
    record['_source_texts'] = source_texts
    return record

def normalize_all(vacancies):
    records = {}
    for index, vacancy in enumerate(vacancies, 1):
        record = normalize(vacancy)
        identifier = record['id']
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
            raise ValueError(f'Вакансия {index}: отсутствует корректный id')
        record['id'] = str(identifier).strip()
        records[record['id']] = record
    return list(records.values())
