"""
Прототип семантического классификатора сцен.
Заменяет grep-маркеры на LLM-классификацию.
"""

import openai
import os
import json

# Загружаем переменные
def _get_env():
    env_vars = {}
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env_vars

_env = _get_env()
_API_KEY = _env.get("OPENROUTER_API_KEY") or _env.get("TEST_API_KEY")
_MODEL = _env.get("MODEL_NAME", "qwen/qwen3-max")

# Список доступных сцен (MVP)
AVAILABLE_SCENES = {
    "scene_01_self_blame": "Человек принимает всё на свой счёт, чувствует вину за чужие эмоции или действия",
    "scene_02_reading_minds": "Человек думает за другого, приписывает ему мысли/намерения без фактов",
    "scene_03_catastrophe": "Человек живёт будущей катастрофой, прыгает от события к необратимому ужасу",
    "scene_04_dual_choice": "Человек видит только два выхода (оба плохих), не замечает промежуточных вариантов",
    "scene_05_feeling_as_fact": "Человек считает своё чувство доказательством реальности ('я чувствую себя никчёмным = я никчёмный')",
    "scene_06_fear_of_loss": "Человек боится потерять отношения и поэтому терпит/молчит/угождает",
    "scene_07_overload": "Состояние перегрузки: туман в голове, трясёт, не могу думать, слишком много всего, голова пустая или гудит. Человек не способен анализировать, нуждается в сенсорном заземлении.",
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
        "Я чувствую себя бесполезным значит так и есть"
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