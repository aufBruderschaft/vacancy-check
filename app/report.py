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
        v_id = v['id']
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