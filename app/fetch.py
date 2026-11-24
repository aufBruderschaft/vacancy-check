import requests
import json

class ApiError(Exception):
    pass



def unwrap(data):
    items = data.get('results', {}).get('vacancies', [])
    return [item['vacancy'] for item in items if 'vacancy' in item]

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
    page_size = min(page_size, 100)
    vacancies = []
    page = 0
    while len(vacancies) < max_vacancies:
        batch = fetch_page(url, page, page_size)
        if not batch:
            break
        vacancies.extend(batch)
        page += 1
    if not vacancies:
        raise ApiError('API вернул 0 вакансий - проверь URL и регион')
    return vacancies[:max_vacancies]

def load_local(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return unwrap(data)
