import json
import os
from datetime import datetime

def save_report(vacancies, issues_dict, mode, model, report_path):
    report_data = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": mode,
        "model": model,
        "vacancies": []
    }

    for v in vacancies:
        v_id = v.get("id") 
        # на всякий пожарный
        if not v_id:
            continue

        report_data["vacancies"].append({
            "id": v_id,
            "url": v.get("vac_url", ""), 
            "issues": issues_dict.get(v_id, [])
        })

    # временный файл
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    tmp_path = report_path + ".tmp"
    
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    os.replace(tmp_path, report_path)
    print(f"Отчет успешно сохранен в {report_path}")

def deduplicate_issues(issues):
    if not issues:
        return []

    # Приоритет: 
    # 0. LLM contradiction (самое сильное)
    # 1. Rules contradiction
    # 2. LLM ambiguous
    # 3. Rules ambiguous/missing

    def priority(issue):
        source = issue.get("source", "rules")
        type_ = issue.get("type", "ambiguous")
        if source == "llm" and type_ == "contradiction": return 0
        if source == "rules" and type_ == "contradiction": return 1
        if source == "llm": return 2
        return 3
    sorted_issues = sorted(issues, key=priority)
    
    seen = set()
    result = []
    
    for issue in sorted_issues:
        # Ключ уникальности: тема + тип + поле
        # (Если поля разные, но тема одна, например, график в schedule и qualification - они не схлопнутся. Но если поле одинаковое - схлопнутся).
        key = (issue.get("topic"), issue.get("type"), issue.get("field"))
        
        if key not in seen:
            seen.add(key)
            result.append(issue)
            
    return result