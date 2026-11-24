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
    return record

def normalize_all(vacancies):
    records = {}
    for vacancy in vacancies:
        record = normalize(vacancy)
        if record['id']:
            records[record['id']] = record
    return list(records.values())

