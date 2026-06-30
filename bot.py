import telebot
import openai
import os
import sys
from datetime import datetime
from db import UserRepository
from core.scene_router import classify_scene, AVAILABLE_SCENES

# Загрузка переменных из .env
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars = {}
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        return env_vars
    except FileNotFoundError:
        print(f"ОШИБКА: Файл .env не найден по пути {env_path}")
        sys.exit(1)

env = load_env()
TELEGRAM_BOT_TOKEN = env.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    print("ОШИБКА: TELEGRAM_BOT_TOKEN не найден в .env")
    sys.exit(1)
OPENROUTER_API_KEY = env.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    print("ОШИБКА: OPENROUTER_API_KEY не найден в .env")
    sys.exit(1)
TEST_API_KEY = env.get("TEST_API_KEY")
OWNER_ID = int(env.get("OWNER_ID", "0"))
MODEL_NAME = env.get("MODEL_NAME", "qwen/qwen3-max")

# Проверка наличия критических файлов
required_files = [
    "core/system_prompt_core.md",
    "modules/module_crisis_phrases.md"
]
for f in required_files:
    if not os.path.exists(f):
        print(f"ОШИБКА: Файл {f} не найден")
        sys.exit(1)
print("Все файлы загружены успешно")

# ============================================
# SAFETY LAYER (код до LLM)
# ============================================

SUICIDE_MARKERS = [
    "хочу умереть", "убить себя", "не хочу жить", "лучше бы я не родился",
    "лучше бы меня не было", "нет выхода", "нет смысла жить",
    "больше не могу так жить", "зачем я живу", "устал жить", "считаю дни",
    "готовлюсь уйти", "прощайте все", "всем будет лучше без меня",
    "конец всему", "пора заканчивать"
]

PANIC_MARKERS = [
    "не могу дышать", "паника", "сердце колотится", "задыхаюсь",
    "теряю сознание", "сейчас умру", "инфаркт", "инсульт"
]

ISOLATED_PLACE_MARKERS = ["заброшка", "заброшенное", "подвал", "гараж", "чердак", "стройку", "пустое здание"]
SUBSTANCE_MARKERS = ["выпили", "пиво", "алкоголь", "водка", "вино", "напились", "выпил"]
COERCION_MARKERS = ["настаивает", "не отстанет", "тащит", "зовет несмотря на отказ", "не отпускает", "принуждает"]

CRISIS_RESPONSE_PHYSICAL_THREAT = """Я слышу, что ситуация может быть физически небезопасной. Сейчас важнее всего твоя безопасность, а не разбор чувств.

Пожалуйста, сделай одно из этого прямо сейчас:
• Уйди в людное место (магазин, кафе, остановка)
• Позвони или напиши человеку, которому доверяешь: «Мне нужна помощь, я в небезопасной ситуации»
• Если чувствуешь непосредственную угрозу — звони 102 или 112

Ты не обязана справляться одна. Твоя безопасность сейчас — приоритет.

Когда будешь в безопасном месте — напиши мне."""

CRISIS_RESPONSE_SUICIDE = """🛑 СТОП. Я слышу тебя. То, что ты чувствуешь — это очень серьёзно.

Прямо сейчас важно только одно — твоё тело и безопасность.

1. Почувствуй стопы на полу. Сделай медленный вдох через нос (4 секунды).
2. Задержи дыхание (4 секунды).
3. Медленный выдох через рот (4 секунды).
4. Повтори 5 раз.

Ты не один. Пожалуйста, свяжись с теми, кто может помочь прямо сейчас:
📞 Телефон доверия (Россия): 8-800-2000-122
📞 Экстренная психологическая помощь МЧС: 8-495-989-50-50
📞 Скорая помощь: 103 | Если в опасности — 112.

Я здесь. Но мне нужна помощь живых людей, чтобы тебя поддержать. Пожалуйста, позвони."""

CRISIS_RESPONSE_PANIC = """🛑 СТОП. Это паническая атака. Она пройдёт через 10-15 минут. Ты не умрёшь.

Прямо сейчас сделай так:

1. Почувствуй стопы на полу. Они твёрдые. Ты здесь.
2. Сделай медленный вдох через нос (4 секунды).
3. Задержи дыхание (4 секунды).
4. Медленный выдох через рот (6 секунд).
5. Повтори 5 раз.

Это не инфаркт. Это адреналин. Он выйдет. Ты в безопасности."""

def check_safety(user_message: str) -> str | None:
    message_lower = user_message.lower()
    
    has_isolated = any(marker in message_lower for marker in ISOLATED_PLACE_MARKERS)
    has_substance = any(marker in message_lower for marker in SUBSTANCE_MARKERS)
    has_coercion = any(marker in message_lower for marker in COERCION_MARKERS)

    threat_flags = sum([has_isolated, has_substance, has_coercion])
    if threat_flags >= 2:
        return CRISIS_RESPONSE_PHYSICAL_THREAT
    
    for marker in SUICIDE_MARKERS:
        if marker in message_lower:
            return CRISIS_RESPONSE_SUICIDE
    
    for marker in PANIC_MARKERS:
        if marker in message_lower:
            return CRISIS_RESPONSE_PANIC
    
    return None

# ============================================
# ЗАГРУЗКА СИСТЕМНОГО ПРОМПТА И МОДУЛЕЙ
# ============================================

def load_system_prompt():
    try:
        with open(os.path.join("core", "system_prompt_core.md"), "r", encoding="utf-8") as f:
            core_prompt = f.read()
        
        methodology_path = os.path.join("core", "methodology_private.md")
        if os.path.exists(methodology_path):
            with open(methodology_path, "r", encoding="utf-8") as f:
                methodology_content = f.read()
            return core_prompt + "\n\n" + methodology_content
        
        return core_prompt
    except FileNotFoundError:
        return "Ты — навигатор по психике. Следуй уровням 0→1→2→3→4."

def load_module(module_name):
    try:
        if not module_name.startswith("module_") or ".." in module_name or "/" in module_name or "\\" in module_name:
            return ""
        with open(os.path.join("modules", f"{module_name}.md"), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

# ============================================
# СТРУКТУРНЫЕ МАРКЕРЫ (оставлены как fallback)
# ============================================

STRUCTURAL_MARKERS = [
    "единственный доход", "держит деньгами", "не могу уйти финансово",
    "без него я никто", "его статус", "стыдно остаться",
    "единственный кто слушает", "единственный кто замечает", "без него пустота", "никому не нужна",
    "боюсь ответственности", "всё решал за меня", "не знаю как жить самостоятельно",
    "бьёт но не могу уйти", "унижает но обеспечивает", "терплю потому что денег нет",
    "не могу уйти", "завишу от него", "он единственный кормилец",
    "без него пропаду", "держит на себе", "не выживу без него",
    "останусь ни с чем", "найдёт другую", "боюсь что уйдёт к другой",
    "потеряю его и останусь одна", "без него я ничто"
]

# ============================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def get_api_client(user_id: int) -> openai.OpenAI:
    """Возвращает клиент с правильным API-ключом в зависимости от user_id."""
    if user_id == OWNER_ID:
        return openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")
    elif TEST_API_KEY:
        return openai.OpenAI(api_key=TEST_API_KEY, base_url="https://openrouter.ai/api/v1")
    else:
        return openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

# Хранилище контекста
user_contexts = {}
processed_messages = set()

# ============================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================

@bot.message_handler(commands=['start'])
def start_message(message):
    user_id = message.chat.id
    system_prompt = load_system_prompt()
    
    welcome_text = (
        "Привет. Я — бот \"Открытые горизонты\".\n\n"
        "Я могу помочь тебе:\n"
        "• Разобраться в чувствах и состояниях\n"
        "• Найти опору, когда тревожно или пусто\n"
        "• Отделить факты от интерпретаций в сложных ситуациях\n"
        "• Проверить убеждения маленькими безопасными шагами\n\n"
        "Ты можешь описать, что происходит, одним предложением или просто сказать, что чувствуешь прямо сейчас. Я выслушаю."
    )
    
    context = {
        "messages": [{"role": "system", "content": system_prompt}],
        "modules_loaded": [],
        "current_level": 0
    }
    context["messages"].append({"role": "assistant", "content": welcome_text})
    UserRepository.save_context(user_id, context)
    UserRepository.log_event(user_id, "session_start")
    
    bot.send_message(user_id, welcome_text)

@bot.message_handler(commands=['reset'])
def reset_message(message):
    user_id = message.chat.id
    UserRepository.reset_context(user_id)
    UserRepository.log_event(user_id, "reset")
    bot.send_message(user_id, "Контекст сброшен. Начнём сначала.")

@bot.message_handler(commands=['help'])
def help_message(message):
    bot.send_message(message.chat.id,
        "Команды:\n"
        "/start — начать диалог\n"
        "/reset — сбросить контекст\n"
        "/help — помощь"
    )

# ============================================
# ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ============================================

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.chat.id
    user_text = message.text
    
    if not user_text or len(user_text) > 5000:
        bot.send_message(user_id, "Сообщение слишком длинное. Пожалуйста, сократи его.")
        return
    
    message_id = message.message_id
    if message_id in processed_messages:
        return
    processed_messages.add(message_id)
    
    if len(processed_messages) > 1000:
        processed_messages.clear()
    
    context = UserRepository.get_context(user_id)
    if not context:
        context = {
            "messages": [{"role": "system", "content": load_system_prompt()}],
            "modules_loaded": [],
            "current_level": 0
        }
        UserRepository.save_context(user_id, context)
    
    user_text_lower = user_text.lower()
    
    # === STRUCTURAL DEPENDENCY FALLBACK ===
    structural_marker_found = None
    if "module_structural_dependency" not in context["modules_loaded"]:
        for marker in STRUCTURAL_MARKERS:
            if marker in user_text_lower:
                structural_marker_found = marker
                break

    # === SAFETY CHECK ===
    safety_response = check_safety(user_text)
    
    # === DIAGNOSTICS ===

    if structural_marker_found:
        module_content = load_module("module_structural_dependency")
        if module_content:
            context["messages"].append({
                "role": "system",
                "content": f"\n\n[АКТИВИРОВАН МОДУЛЬ: module_structural_dependency]\n{module_content}"
            })
            context["modules_loaded"].append("module_structural_dependency")
            UserRepository.log_event(user_id, "module_loaded", {"module": "module_structural_dependency"})
        
        context["messages"].append({"role": "user", "content": user_text})
        if len(context["messages"]) > 50:
            context["messages"] = context["messages"][-25:]
        
        api_client = get_api_client(user_id)
        
        try:
            response = api_client.chat.completions.create(
                model=MODEL_NAME,
                messages=context["messages"],
                temperature=0.7,
                max_tokens=1500
            )
            bot_reply = response.choices[0].message.content
            context["messages"].append({"role": "assistant", "content": bot_reply})
            UserRepository.save_context(user_id, context)
            bot.send_message(user_id, bot_reply)
        except Exception as e:
            bot.send_message(user_id, f"Ошибка: {e}")
        return
    
    if safety_response:
        bot.send_message(user_id, safety_response)
        return
    
    # === СЕМАНТИЧЕСКАЯ КЛАССИФИКАЦИЯ СЦЕНЫ ===
    scene_result = classify_scene(user_text)
    print(f"[SCENE DEBUG] → {scene_result}")
    scene_id = scene_result.get("scene_id", "unknown")
    confidence = scene_result.get("confidence", 0.0)
    
    if scene_id != "unknown" and confidence >= 0.7:
        scene_path = os.path.join("core", f"{scene_id}.md")
        if os.path.exists(scene_path):
            with open(scene_path, "r", encoding="utf-8") as f:
                scene_content = f.read()
            context["messages"].append({
                "role": "system",
                "content": f"\n\n[АКТИВИРОВАНА СЦЕНА: {scene_id}]\n{scene_content}"
            })
            if scene_id not in context.get("modules_loaded", []):
                context.setdefault("modules_loaded", []).append(scene_id)
            UserRepository.log_event(user_id, "scene_activated", {"scene": scene_id, "confidence": confidence})
    
    context["messages"].append({"role": "user", "content": user_text})
    
    if len(context["messages"]) > 50:
        context["messages"] = context["messages"][-25:]
    
    api_client = get_api_client(user_id)
    api_key_type = "owner" if user_id == OWNER_ID else "tester"
    
    UserRepository.log_event(user_id, "message_sent", {"length": len(user_text), "api_key_type": api_key_type})
    
    try:
        response = api_client.chat.completions.create(
            model=MODEL_NAME,
            messages=context["messages"],
            temperature=0.7,
            max_tokens=1500
        )
        bot_reply = response.choices[0].message.content
        
        context["messages"].append({"role": "assistant", "content": bot_reply})
        
        if scene_id == "unknown":
            UserRepository.log_event(user_id, "base_response", {"trigger": "no_scene_matched"})
        
        UserRepository.save_context(user_id, context)
        bot.send_message(user_id, bot_reply)
        
    except Exception as e:
        bot.send_message(user_id, f"Ошибка: {e}")

# ============================================
# ЗАПУСК БОТА
# ============================================

if __name__ == "__main__":
    print("Бот запущен...")
    print("Нажми Ctrl+C для остановки")
    bot.polling(none_stop=True)
    