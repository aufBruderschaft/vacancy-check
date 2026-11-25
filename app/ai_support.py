"""Защита от дублей: замечание модели, повторяющее замечание правила, не выводится."""
import logging
import re

log = logging.getLogger(__name__)


def filter_rule_duplicates(llm_issues, rules):
    """Узкая дедупликация тех же фрагментов, без общих догадок о смысле."""
    result, seen = [], set()
    for issue in llm_issues:
        topic, field = issue['topic'], issue['field']
        quote = issue['quote'].casefold()
        text = (issue['problem'] + ' ' + issue['recommendation']).casefold()
        duplicate = False
        for rule in rules:
            if rule.get('topic') != topic or rule.get('field') != field:
                continue
            rq = str(rule.get('quote') or '').casefold()
            if not rq or not (rq in quote or quote in rq):
                continue
            if topic == 'название' and rule.get('type') == 'ambiguous':
                duplicate = True
            elif topic == 'график' and rule.get('type') == 'ambiguous':
                # Не убирать отдельный вопрос, как гибкость соотносится
                # с уже указанными часами: это не вопрос правила о пропуске.
                duplicate = bool(re.search(r'не\s+указан|без\s+дней|неясн.{0,30}(?:час|дн)|уточн.{0,25}(?:дни|часы)', text))
            elif topic == 'место работы' and rule.get('type') == 'ambiguous':
                duplicate = bool(re.search(r'только\s+регион|без\s+города|уточн.{0,20}(?:город|адрес)', text))
            elif topic == 'требования' and rule.get('type') == 'contradiction':
                duplicate = bool(re.search(r'опыт|стаж', text))
            elif issue.get('type') == rule.get('type') and rq == quote:
                duplicate = True
            if duplicate:
                break
        content = tuple(str(issue.get(k, '')).casefold().strip()
                        for k in ('field', 'quote', 'problem', 'recommendation'))
        if not duplicate and content not in seen:
            result.append(issue)
            seen.add(content)
        else:
            log.info('Пропущен повтор замечания модели для поля %s', field)
    return result
