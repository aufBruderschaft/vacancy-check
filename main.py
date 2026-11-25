import sys
import logging
import argparse
import time
import yaml
import json
import os

from app.fetch import fetch_vacancies, load_local, ApiError
from app.normalize import normalize_all
from app.rules import check_rules

from app.report import save_report
from app.llm import analyze_vacancy, validate_quotes, cache_key as llm_cache_key, LLMError, LLMUnavailable, LLMAllRejected
from app.ai_support import filter_rule_duplicates

CACHE_PATH = "reports/cache.json"

# Настройка логов
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Не удалось прочитать кэш: {e}")
            return {}
    return {}

def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp_path = CACHE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CACHE_PATH)

def run_pipeline(raw_data, mode, cfg):
    vacancies = normalize_all(raw_data)
    all_issues = {}
    
    cache_enabled = bool(cfg['llm'].get('enabled'))
    if cache_enabled and os.path.abspath(CACHE_PATH) == os.path.abspath(cfg['output']['report_path']):
        cache_enabled = False
        logger.warning("Путь кэша совпадает с отчётом; кэш отключён")
    cache = load_cache() if cache_enabled else {}
    if not isinstance(cache, dict):
        logger.warning("Некорректный формат кэша, используем пустой")
        cache = {}
    cache_updated = False
    llm_unavailable = False

    for index, v in enumerate(vacancies):
        v_id = v['id']
        issues = []

        # Правила 
        try:
            issues.extend(check_rules(v))
        except Exception as e:
            logger.error(f"Ошибка в правилах для {v_id}: {e}")

        # LLM: None или исключение — сбой, [] — успешный ответ без замечаний.
        v['_llm_checked'] = False
        v['_llm_status'] = 'disabled'
        budget = cfg['llm'].get('max_vacancies_for_ai')
        within_budget = budget is None or index < max(0, int(budget))
        if cfg['llm'].get('enabled') and not within_budget:
            v['_llm_status'] = 'skipped_limit'
        if cfg['llm'].get('enabled') and within_budget:
            v['_llm_status'] = 'error'
            try:
                cache_key = llm_cache_key(v, cfg['llm']) if cache_enabled else None
                llm_issues = None
                if cache_key and cache_key in cache:
                    try:
                        llm_issues = validate_quotes(cache[cache_key], v)
                        if llm_issues is None:
                            raise LLMError("Пустая запись кэша")
                    except LLMError as e:
                        logger.warning(f"Некорректная запись кэша для {v_id}: {e}")
                        cache.pop(cache_key, None)
                        cache_updated = True
                        llm_issues = None

                if llm_issues is not None:
                    v['_llm_status'] = 'cache'
                    logger.info("LLM: использован кэш для %s (%d замечаний)", v_id, len(llm_issues))
                if llm_issues is None and not llm_unavailable:
                    try:
                        llm_issues = analyze_vacancy(v, cfg['llm'])
                        llm_issues = validate_quotes(llm_issues, v)
                    except LLMAllRejected as e:
                        # Модель ответила, но ни одно замечание не подтвердилось цитатой:
                        # в отчёт ничего не попадает, это обычный ответ «замечаний нет».
                        logger.info(f"LLM: для {v_id} все замечания модели отклонены проверкой ({e})")
                        llm_issues = []
                    if llm_issues is not None:
                        v['_llm_status'] = 'model'
                        logger.info("LLM: проверена вакансия %s (%d замечаний)", v_id, len(llm_issues))
                    if llm_issues is not None and cache_key:
                        cache[cache_key] = llm_issues
                        cache_updated = True

                if llm_issues is not None:
                    issues.extend(filter_rule_duplicates(llm_issues, issues))
                    v['_llm_checked'] = True
            except LLMUnavailable as e:
                # Другие вакансии всё ещё могут использовать ранее успешный кэш.
                llm_unavailable = True
                logger.warning(f"Сервис LLM недоступен в этом запуске: {e}")
            except Exception as e:
                logger.warning(f"Ошибка LLM для {v_id}, пропускаем: {e}")

        # 3. ДЕДУПЛИКАЦИЯ (импортируй функцию из report.py)
        from app.report import deduplicate_issues
        cleaned_issues = deduplicate_issues(issues)
        
        all_issues[v_id] = cleaned_issues

    # Сохраняем обновленный кэш на диск
    if cache_updated:
        try:
            save_cache(cache)
        except (OSError, TypeError, ValueError) as e:
            logger.warning(f"Не удалось сохранить кэш LLM: {e}")
        
    return vacancies, all_issues

def fetch_with_retries(cfg):
    max_retries = cfg['source'].get('max_retries', 3)
    delay = cfg['source'].get('retry_delay_seconds', 5)
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Попытка загрузки данных ({attempt}/{max_retries})...")
            return fetch_vacancies(
                cfg['source']['url'], 
                cfg['source']['page_size'], 
                cfg['source']['max_vacancies']
            )
        except ApiError as e:
            logger.warning(f"Сбой API (попытка {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                #  пробрасываем ошибку выше чтобы внешний except не дал перезаписать старый отчет
                logger.error("Все попытки исчерпаны.")
                raise e
            logger.info(f"Ждем {delay} секунд перед следующей попыткой...")
            time.sleep(delay)

def main():
    # Парсер аргументов (создает args)
    parser = argparse.ArgumentParser(description="Vacancy Checker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Запуск через API")
    check_parser = subparsers.add_parser("check", help="Проверка локального JSON")
    check_parser.add_argument("--input", required=True, help="Путь к файлу")
    schedule_parser = subparsers.add_parser("schedule", help="Работа по расписанию")
    for command_parser in (run_parser, check_parser, schedule_parser):
        command_parser.add_argument("--config", default="config.yaml", help="Путь к YAML-конфигурации")
        command_parser.add_argument("--no-ai", action="store_true", help="Отключить ИИ")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.no_ai:
        cfg['llm']['enabled'] = False

    try:
        if args.command == "run":
            logger.info("Запуск через API...")
            raw = fetch_with_retries(cfg) 
            vacancies, issues = run_pipeline(raw, mode="api", cfg=cfg)
            report_model = cfg['llm']['model'] if any(v.get('_llm_checked') for v in vacancies) else None
            save_report(vacancies, issues, mode="api", model=report_model, report_path=cfg['output']['report_path'])
            logger.info(f"Обработано вакансий: {len(issues)}")

        elif args.command == "check":
            logger.info(f"Запуск проверки файла: {args.input}")
            raw = load_local(args.input)
            vacancies, issues = run_pipeline(raw, mode="check", cfg=cfg)
            report_model = cfg['llm']['model'] if any(v.get('_llm_checked') for v in vacancies) else None
            save_report(vacancies, issues, mode="check", model=report_model, report_path=cfg['output']['report_path'])
            logger.info(f"Обработано вакансий: {len(issues)}")

        elif args.command == "schedule":
            logger.info("Запуск по расписанию...")
            hours = cfg['schedule']['every_hours']
            if not isinstance(hours, (int, float)) or isinstance(hours, bool) or hours <= 0:
                raise ValueError('schedule.every_hours должен быть положительным числом')
            logger.info(f"Интервал: каждые {hours} часов. (Нажми Ctrl+C для выхода)")
            while True:
                try:
                    logger.info("Плановая загрузка данных...")
                    raw = fetch_with_retries(cfg)
                    vacancies, issues = run_pipeline(raw, mode="api", cfg=cfg)
                    report_model = cfg['llm']['model'] if any(v.get('_llm_checked') for v in vacancies) else None
                    save_report(vacancies, issues, mode="api", model=report_model, report_path=cfg['output']['report_path'])
                    logger.info(f"Плановый отчет сохранен. Ждем {hours} ч. ...")
                    time.sleep(hours * 3600)
                except KeyboardInterrupt:
                    logger.info("\nРабота остановлена пользователем (Ctrl+C). Выход.")
                    sys.exit(0)
                except Exception as e:
                    # если тут, то все ретраи провалились
                    logger.error(f"Не удалось получить данные: {e}. Ждем следующего планового запуска.")
                    time.sleep(min(hours, 1) * 3600)  # API лёг — пробуем снова через час
    except KeyboardInterrupt:
        logger.info("\nРабота остановлена пользователем (Ctrl+C)")
        sys.exit(0)
    except ApiError as e:
        logger.error(f"Ошибка API: {e}. Старый отчет не тронут.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()