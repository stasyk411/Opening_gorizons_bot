"""
Прототип семантического классификатора сцен.
Заменяет grep-маркеры на LLM-классификацию.
Синхронизирован с CORE_FILTER.md: описания сцен фокусируются на операциях, а не теориях.
"""

import openai
import os
import json

# Загружаем ТОЛЬКО API-ключ из .env
def _get_api_key():
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    if key.strip() == "OPENROUTER_API_KEY":
                        return value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None

_API_KEY = _get_api_key() or os.environ.get("TEST_API_KEY")
_MODEL = "qwen/qwen3-max"  # ЖЁСТКИЙ ХАРДКОД. Никаких переменных среды.

# Список доступных сцен (MVP + новая онтология операций)
AVAILABLE_SCENES = {
    "scene_01_self_blame": "Человек принимает всё на свой счёт, чувствует вину за чужие эмоции или действия",
    "scene_02_reading_minds": "Человек думает за другого, приписывает ему мысли/намерения без фактов",
    "scene_03_catastrophe": "Человек живёт будущей катастрофой, прыгает от события к необратимому ужасу",
    "scene_04_dual_choice": "Человек видит только два выхода (оба плохих), не замечает промежуточных вариантов",
    "scene_feeling_equals_fact": "Операция 'Отделить эмоцию от события'. Человек склеивает чувство и реальность ('мне страшно = опасно', 'чувствую вину = виноват'). Нужен вопрос-разделитель.",
    "scene_fact_equals_conclusion": "Операция 'Отделить факт от вывода'. Человек склеивает событие и свою интерпретацию ('он молчит = ненавидит', 'не ответила = безразличен'). Нужен вопрос-разделитель.",
    "scene_thought_equals_reality": "Операция 'Отделить мысль от реальности'. Человек считает свою гипотезу фактом ('мне кажется = так и есть'). Нужен вопрос-проверка.",
    "scene_should_equals_want": "Операция 'Отделить внешнее требование от внутреннего желания'. Человек склеивает долг и хотение ('я должен = я хочу', 'надо терпеть'). Нужен вопрос-разделитель.",
    "scene_all_or_nothing": "Операция 'Отделить абсолют от градаций'. Человек видит только 100% или 0% ('не идеально = провал'). Нужен вопрос о ценности частичного.",
    "scene_labeling": "Операция 'Отделить глобальную оценку от факта'. Человек заменяет описание ярлыком ('я неудачник', 'он эгоист'). ВАЖНО: если фраза про вину, обузу или влияние на других ('я всем надоел', 'я плохой отец') — это scene_01_self_blame, а не labeling. Нужен вопрос о конкретике.",
    "scene_emotional_reasoning": "Операция 'Отделить чувство как сигнал от чувства как доказательства'. Человек считает эмоцию фактом ('чувствую вину = виноват'). Нужен вопрос о фактах отдельно от ощущения.",
    "scene_personalization": "Операция 'Отделить внешнее событие от личной значимости'. Человек считает действия других направленными против него ('она опоздала = не уважает меня'). ВАЖНО: если фраза про приписывание намерений или мыслей другому ('он специально', 'он думает что я...') — это scene_02_reading_minds, а не personalization. Нужен вопрос об альтернативных причинах.",
    "scene_06_fear_of_loss": "Человек боится потерять отношения и поэтому терпит/молчит/угождает",
    "scene_07_overload": "Состояние перегрузки: туман в голове, трясёт, не могу думать, слишком много всего, голова пустая или гудит. Человек не способен анализировать, нуждается в сенсорном заземлении.",
    "scene_disqualifying_positive": "Операция 'Признать результат как реальный'. Человек стирает достижения фильтром ('просто повезло', 'любой бы справился'). Нужен вопрос о конкретных действиях.",
    "scene_overgeneralization": "Операция 'Отделить единичный факт от глобального правила'. Человек делает вывод на основе одного случая ('никогда не получается', 'все одинаковые'). Нужен вопрос об исключениях.",
    "unknown": "Сцена не распознана — нужен уточняющий вопрос"
}

SCENE_LIST = "\n".join([f"- {k}: {v}" for k, v in AVAILABLE_SCENES.items()])

SYSTEM_PROMPT = f"""Ты — классификатор жизненных сцен. 
Пользователь пишет сообщение. Твоя задача — определить, какая из сцен ниже лучше всего описывает его ситуацию.

Доступные сцены:
{SCENE_LIST}

Отвечай ТОЛЬКО JSON-объектом вида:
{{"scene_id": "scene_XX_...", "confidence": 0.0-1.0}}

Если ни одна сцена не подходит точно — используй "unknown".
Не объясняй. Не добавляй текст. Только JSON."""


def classify_scene(user_text: str, api_key: str = None) -> dict:
    """
    Классифицирует сообщение пользователя по жизненным сценам.
    Возвращает {"scene_id": str, "confidence": float}
    """
    key = api_key or _API_KEY
    if not key:
        return {"scene_id": "unknown", "confidence": 0.0, "error": "no_api_key"}

    client = openai.OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")

    try:
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            temperature=0.1,
            max_tokens=100
        )
        raw = response.choices[0].message.content.strip()
        # Убираем возможные markdown-блоки
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        
        result = json.loads(raw)
        scene_id = result.get("scene_id", "unknown")
        confidence = float(result.get("confidence", 0.0))

        # Валидация
        if scene_id not in AVAILABLE_SCENES:
            scene_id = "unknown"
        
        return {"scene_id": scene_id, "confidence": confidence}

    except json.JSONDecodeError:
        return {"scene_id": "unknown", "confidence": 0.0, "error": "invalid_json"}
    except Exception as e:
        return {"scene_id": "unknown", "confidence": 0.0, "error": str(e)}


# Быстрый тест
if __name__ == "__main__":
    test_phrases = [
        "Я всем надоел",
        "Он специально это сделал чтобы меня задеть",
        "Если я откажусь то всё рухнет и меня уволят",
        "Мне нужно выбрать либо терпеть либо уйти третьего нет",
        "Я чувствую себя бесполезным значит так и есть",
        "Мне страшно, значит что-то плохое точно случится",  # feeling_equals_fact
        "Он молчит, значит он меня ненавидит",  # fact_equals_conclusion
        "Мне кажется, что всё плохо — значит так и есть",  # thought_equals_reality
        "Я должен быть сильным, нельзя показывать слабость",  # should_equals_want
        "Если я не сделаю это идеально, то лучше вообще не начинать",  # all_or_nothing
        "Я полный неудачник",  # labeling
        "Я чувствую вину, значит я точно виноват",  # emotional_reasoning
        "Она опоздала, потому что ей на меня плевать",  # personalization
        "Это просто повезло, а не моя заслуга",  # disqualifying_positive
        "У меня никогда ничего не получается",  # overgeneralization
    ]
    
    print("=" * 60)
    print("ТЕСТ КЛАССИФИКАТОРА СЦЕН")
    print("=" * 60)
    
    for phrase in test_phrases:
        result = classify_scene(phrase)
        status = "✅" if result["scene_id"] != "unknown" else "❌"
        conf = result.get("confidence", 0)
        err = result.get("error", "")
        print(f"\n{status} «{phrase}»")
        print(f"   → {result['scene_id']} (confidence: {conf:.2f})")
        if err:
            print(f"   ⚠ Error: {err}")