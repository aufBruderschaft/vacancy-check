import requests
import json

class ApiError(Exception):
    pass



def unwrap(data):
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and isinstance(data.get('results'), dict):
        # Пустая страница API приходит как "results": {} — это конец данных, не ошибка.
        items = data['results'].get('vacancies', [])
        if not isinstance(items, list):
            raise ApiError('Некорректный формат данных: vacancies не список')
    else:
        # Ответ без results — это не страница вакансий (ошибка API).
        raise ApiError('Некорректный формат данных: список вакансий не найден')
    if not all(isinstance(item, dict) and isinstance(item.get('vacancy', item), dict) for item in items):
        raise ApiError('Некорректная запись вакансии')
    return [item.get('vacancy', item) for item in items]

def fetch_page(url, page, limit):
    try:
        resp = requests.get(url, params={'offset': page, 'limit': limit}, timeout=50)
        resp.raise_for_status()
        data = resp.json()
    except ValueError as e:
        raise ApiError('API вернул не JSON') from e
    except requests.RequestException as e:
        raise ApiError(f'API недоступен: {e}') from e
    return unwrap(data)

def fetch_vacancies(url, page_size, max_vacancies):
    if page_size < 1 or max_vacancies < 1:
        raise ValueError('Лимиты страниц и вакансий должны быть положительными')
    page_size = min(page_size, 100, max(1, (max_vacancies + 1) // 2))
    vacancies = []
    seen = set()
    page = 0
    while len(vacancies) < max_vacancies:
        batch = fetch_page(url, page, page_size)
        if not batch:
            break
        added = 0
        for item in batch:
            raw_id = item.get('id')
            key = str(raw_id).strip() if raw_id is not None else ''
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            vacancies.append(item)
            added += 1
            if len(vacancies) >= max_vacancies:
                break
        if not added:
            raise ApiError('API повторил страницу без новых вакансий')
        # API возвращает следующую страницу при увеличении offset на 1.
        page += 1
        if len(batch) < page_size:
            break  # неполная страница — она последняя
    if not vacancies:
        raise ApiError('API вернул 0 вакансий - проверь URL и регион')
    return vacancies[:max_vacancies]

def load_local(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return unwrap(data)
