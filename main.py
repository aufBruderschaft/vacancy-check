import sys
import logging
import argparse
import yaml

# Импорты для Льва
from app.fetch import fetch_vacancies, load_local, ApiError
from app.normalize import normalize_all
from app.rules import check_rules

from app.report import save_report

# Настройка логов
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def run_pipeline(raw_data, mode, cfg):
    vacancies = normalize_all(raw_data)
    all_issues = {}

    for v in vacancies:
        v_id = v['id']
        issues = []

        # Правила 
        try:
            issues.extend(check_rules(v))
        except Exception as e:
            logger.error(f"Ошибка в правилах для {v_id}: {e}")

        # TODO: Сюда позже воткнем вызов LLM
        # try:
        #     
        #     
        # except Exception as e:
        #     logger.warning(f"Ошибка LLM для {v_id}: {e}")

        all_issues[v_id] = issues
        
    return vacancies, all_issues

def main():
    # Парсер аргументов (создает args)
    parser = argparse.ArgumentParser(description="Vacancy Checker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Запуск через API")
    check_parser = subparsers.add_parser("check", help="Проверка локального JSON")
    check_parser.add_argument("--input", required=True, help="Путь к файлу")
    subparsers.add_parser("schedule", help="Работа по расписанию")
    args = parser.parse_args()
    cfg = load_config()

    try:
        if args.command == "run":
            logger.info("Запуск через API...")
            raw = fetch_vacancies(
                cfg['source']['url'], 
                cfg['source']['page_size'], 
                cfg['source']['max_vacancies']
            )
            
            vacancies, issues = run_pipeline(raw, mode="api", cfg=cfg)
            save_report(vacancies, issues, mode="api", model=cfg['llm']['model'], report_path=cfg['output']['report_path'])
            
            logger.info(f"Обработано вакансий: {len(issues)}")

        elif args.command == "check":
            logger.info(f"Запуск проверки файла: {args.input}")
            raw = load_local(args.input)
            
            vacancies, issues = run_pipeline(raw, mode="check", cfg=cfg)
            save_report(vacancies, issues, mode="check", model=cfg['llm']['model'], report_path=cfg['output']['report_path'])
            
            logger.info(f"Обработано вакансий: {len(issues)}")

    except ApiError as e:
        logger.error(f"Ошибка API: {e}. Старый отчет не тронут.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()