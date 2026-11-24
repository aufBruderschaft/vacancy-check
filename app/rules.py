import re

def issue(topic, type_, field, problem, recommendation, quote='', values=None):
    return {
        'topic': topic,
        'type': type_,
        'field': field,
        'quote': quote,
        'values': values or [],
        'problem': problem,
        'recommendation': recommendation,
        'source': 'rules',
    }
def salary_missing(v):
    if not v['salary_min'] and not v['salary_max']:
        return issue('оплата', 'missing', 'salary_min',
                     'Зарплата не указана.',
                     'Уточнить у работодателя размер оплаты.')

def salary_no_max(v):
    if v['salary_min'] and not v['salary_max']:
        return issue('оплата', 'missing', 'salary_max',
                     f"Указана только нижняя граница зарплаты: от {v['salary_min']}.",
                     'Уточнить верхнюю границу или фиксированную сумму.')

def employment_missing(v):
    if not v['employment']:
        return issue('тип занятости', 'missing', 'employment',
                     'Не указан тип занятости.',
                     'Уточнить: полная, частичная, проектная работа или стажировка.')

def duty_missing(v):
    if not v['duty']:
        return issue('обязанности', 'missing', 'duty',
                     'Обязанности не указаны.',
                     'Уточнить, что входит в работу.')


def requirements_missing(v):
    if not (v['requirements'] or v['requirement.qualification'] or v['qualification']):
        return issue('требования', 'missing', 'requirements',
                     'Требования к кандидату не указаны.',
                     'Уточнить требования к кандидату.')

GENERIC_TITLES = {'специалист', 'сотрудник', 'работник', 'менеджер', 'помощник', 'оператор'}


def title_vague(v):
    title = v['job-name']
    if not title:
        return issue('название', 'missing', 'job-name',
                     'Не указано название вакансии.',
                     'Уточнить должность.')
    if title.lower() in GENERIC_TITLES:
        return issue('название', 'ambiguous', 'job-name',
                     f'Название «{title}» не говорит, чем человек будет заниматься.',
                     'Уточнить должность и направление работы.',
                     quote=title)


SCHEDULE_DETAILS = re.compile(r'\d{1,2}[:.]\d{2}|\d/\d|сутки через|через сутки|дневн|ночн', re.IGNORECASE)
TEXT_FIELDS = ['scheduleTypeComment', 'duty', 'requirements', 'requirement.qualification', 'conditions']


def schedule_vague(v):
    schedule = v['schedule']
    if not schedule:
        return issue('график', 'missing', 'schedule',
                     'График работы не указан.',
                     'Уточнить дни и часы работы.')
    if not re.search(r'гибк|сменн', schedule, re.IGNORECASE):
        return None
    texts = [schedule] + [v[f] or '' for f in TEXT_FIELDS] + (v['shift'] or [])
    if SCHEDULE_DETAILS.search(' '.join(texts)):
        return None
    return issue('график', 'ambiguous', 'schedule',
                 f'График «{schedule}» указан без дней и часов.',
                 'Уточнить дни, часы работы и порядок смен.',
                 quote=schedule)

def employment_schedule_conflict(v):
    employment = (v['employment'] or '').lower()
    schedule = (v['schedule'] or '').lower()
    full_job = 'полная' in employment
    part_day = 'неполный' in schedule
    part_job = 'частичная' in employment
    full_day = 'полный рабочий день' in schedule
    if (full_job and part_day) or (part_job and full_day):
        return issue('тип занятости', 'contradiction', 'employment',
                     f"Тип занятости «{v['employment']}» не сходится с графиком «{v['schedule']}».",
                     'Уточнить, полная это занятость или частичная.',
                     quote=v['employment'], values=[v['employment'], v['schedule']])


def address_vague(v):
    address = v['addresses']
    if not address:
        return issue('место работы', 'missing', 'addresses',
                     'Адрес работы не указан.',
                     'Уточнить адрес рабочего места.')
    if ',' not in address and 'област' in address.lower():
        return issue('место работы', 'ambiguous', 'addresses',
                     'Указан только регион, без города и адреса.',
                     'Уточнить город и адрес рабочего места.',
                     quote=address)


EXPERIENCE = re.compile(r'(?:не менее|от|более|минимум)\s+(\d+)(?:-?х)?\s*(?:лет|года|год)', re.IGNORECASE)


def experience_conflict(v):
    years = v['requirement.experience']
    if not isinstance(years, int):
        return None
    for field in ('requirement.qualification', 'requirements', 'qualification'):
        text = v[field] or ''
        for m in EXPERIENCE.finditer(text):
            n = int(m.group(1))
            window = text[max(0, m.start() - 40):m.end() + 40].lower()
            if years < n <= 15 and re.search(r'опыт|стаж', window):
                return issue('требования', 'contradiction', field,
                             f'Опыт в поле: {years} г., а в тексте — «{m.group(0)}».',                             'Уточнить, какой опыт на самом деле требуется.',
                             quote=m.group(0), values=[str(years), m.group(0)])
    
RULES = [title_vague, duty_missing, requirements_missing, salary_missing, salary_no_max,
         employment_missing, employment_schedule_conflict, schedule_vague,
         address_vague, experience_conflict]
         
def check_rules(v):
    issues = []
    for rule in RULES:
        try:
            found = rule(v)
        except Exception as e:
            print(f"правило {rule.__name__} упало на {v.get('id')}: {e}")
            continue
        if found:
            issues.append(found)
    return issues