import telebot
from telebot import types
import openai
import os
import sys
from datetime import datetime
from db import UserRepository
from core.scene_router import classify_scene

# ============================================
# ЗАГРУЗКА КОНФИГУРАЦИИ
# ============================================

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
OPENROUTER_API_KEY = env.get("OPENROUTER_API_KEY")
TEST_API_KEY = env.get("TEST_API_KEY")
OWNER_ID = int(env.get("OWNER_ID", "0"))
MODEL_NAME = env.get("MODEL_NAME", "qwen/qwen3-max")

if not TELEGRAM_BOT_TOKEN:
    print("ОШИБКА: TELEGRAM_BOT_TOKEN не найден")
    sys.exit(1)
if not OPENROUTER_API_KEY:
    print("ОШИБКА: OPENROUTER_API_KEY не найден")
    sys.exit(1)

REQUIRED_FILES = [
    "core/system_prompt_core.md",
    "modules/module_crisis_phrases.md"
]
for f in REQUIRED_FILES:
    if not os.path.exists(f):
        print(f"ОШИБКА: Файл {f} не найден")
        sys.exit(1)

print("✅ Все файлы загружены успешно")

# ============================================
# ANONYMOUS RESEARCH LOG (Приватность!)
# ============================================

ANON_LOG_PATH = os.path.join(os.path.dirname(__file__), "research_first_messages.log")

def log_anonymous_first_message(category: str, text: str):
    """Логирует первое сообщение пользователя БЕЗ user_id для исследования."""
    timestamp = datetime.now().isoformat()
    safe_text = text[:500].replace("\n", " ")
    line = f"{timestamp}\t{category}\t{safe_text}\n"
    try:
        with open(ANON_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[RESEARCH LOG ERROR] {e}")

# ============================================
# SAFETY LAYER
# ============================================

SUICIDE_MARKERS = [
    "хочу умереть", "убить себя", "не хочу жить", "лучше бы я не родился",
    "нет выхода", "нет смысла жить", "больше не могу так жить", "устал жить",
    "готовлюсь уйти", "прощайте все", "всем будет лучше без меня"
]

PANIC_MARKERS = [
    "не могу дышать", "паника", "сердце колотится", "задыхаюсь",
    "теряю сознание", "сейчас умру", "инфаркт", "инсульт"
]

CRISIS_RESPONSE_SUICIDE = """🛑 СТОП. Я слышу тебя. То, что ты чувствуешь — это очень серьёзно.

Прямо сейчас важно только одно — твоё тело и безопасность.

1. Почувствуй стопы на полу. Сделай медленный вдох через нос (4 секунды).
2. Задержи дыхание (4 секунды).
3. Медленный выдох через рот (4 секунды).
4. Повтори 5 раз.

Ты не один. Пожалуйста, свяжись с теми, кто может помочь прямо сейчас:
📞 Телефон доверия (Россия): 8-800-2000-122
📞 Экстренная психологическая помощь МЧС: 8-495-989-50-50
📞 Скорая помощь: 103 | Если в опасности — 112."""

CRISIS_RESPONSE_PANIC = """🛑 СТОП. Это паническая атака. Она пройдёт через 10-15 минут. Ты не умрёшь.

1. Почувствуй стопы на полу. Они твёрдые. Ты здесь.
2. Вдох через нос (4 сек) → Задержка (4 сек) → Выдох через рот (6 сек).
3. Повтори 5 раз.

Это не инфаркт. Это адреналин. Ты в безопасности."""

def check_safety(text: str) -> str | None:
    t = text.lower()
    for m in SUICIDE_MARKERS:
        if m in t:
            return CRISIS_RESPONSE_SUICIDE
    for m in PANIC_MARKERS:
        if m in t:
            return CRISIS_RESPONSE_PANIC
    return None

# ============================================
# ENTRY POINTS V1 + REPLY KEYBOARD
# ============================================

MAIN_CATEGORIES = [
    {"text": "❤️ Отношения", "callback_data": "cat_relations"},
    {"text": "💼 Работа", "callback_data": "cat_work"},
    {"text": "💰 Деньги", "callback_data": "cat_money"},
    {"text": "👨‍👩‍👧 Семья", "callback_data": "cat_family"},
    {"text": "🧠 Моё состояние", "callback_data": "cat_state"},
    {"text": "🚨 Срочно нужна помощь", "callback_data": "ep_crisis"},
    {"text": "✍️ Рассказать своими словами", "callback_data": "ep_free"}
]

STATE_SUBMENU = [
    {"text": "Мне тревожно", "callback_data": "state_anxiety"},
    {"text": "Я устал / выгорел", "callback_data": "state_burnout"},
    {"text": "Запутался / не знаю что делать", "callback_data": "state_confused"},
    {"text": "Не могу принять решение", "callback_data": "state_decision"},
    {"text": "Расскажу подробнее сам", "callback_data": "state_free"}
]

DEFAULT_QUESTION = "Расскажи, что произошло?"

REPLY_KEYBOARD = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
REPLY_KEYBOARD.add(types.KeyboardButton("🏠 Меню"))

# ============================================
# SYSTEM PROMPT
# ============================================

def load_system_prompt():
    core_path = os.path.join("core", "system_prompt_core.md")
    with open(core_path, "r", encoding="utf-8") as f:
        prompt = f.read()
    
    meth_path = os.path.join("core", "methodology_private.md")
    if os.path.exists(meth_path):
        with open(meth_path, "r", encoding="utf-8") as f:
            prompt += "\n\n" + f.read()
    
    ce_path = os.path.join("docs", "conversation_engine.md")
    if os.path.exists(ce_path):
        with open(ce_path, "r", encoding="utf-8") as f:
            prompt += "\n\n[CONVERSATION ENGINE RULES]\n" + f.read()
            
    return prompt

# ============================================
# BOT INIT
# ============================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
processed_messages = set()

def get_api_client(user_id: int):
    key = OPENROUTER_API_KEY if user_id == OWNER_ID else (TEST_API_KEY or OPENROUTER_API_KEY)
    return openai.OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")

# ============================================
# /start & /reset & MENU BUTTON
# ============================================

@bot.message_handler(commands=['start', 'reset'])
def send_main_menu(message):
    user_id = message.chat.id
    
    context = {
        "messages": [{"role": "system", "content": load_system_prompt()}],
        "modules_loaded": [],
        "current_ep": None,
        "awaiting_story": False,
        "first_message_logged": False
    }
    UserRepository.save_context(user_id, context)
    # Объективное событие воронки
    UserRepository.log_funnel_event(user_id, "session_start")
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for cat in MAIN_CATEGORIES:
        markup.add(types.InlineKeyboardButton(text=cat["text"], callback_data=cat["callback_data"]))
    
    bot.send_message(
        user_id, 
        "Привет. Я — бот «Открытые горизонты».\nВыбери тему:", 
        reply_markup=markup
    )
    bot.send_message(user_id, "Кнопка ниже всегда вернёт тебя в меню 👇", reply_markup=REPLY_KEYBOARD)

@bot.message_handler(func=lambda m: m.text == "🏠 Меню")
def handle_menu_button(message):
    send_main_menu(message)

# ============================================
# CALLBACK HANDLER
# ============================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.message.chat.id
    data = call.data
    
    context = UserRepository.get_context(user_id) or {
        "messages": [{"role": "system", "content": load_system_prompt()}],
        "modules_loaded": [],
        "current_ep": None,
        "awaiting_story": False,
        "first_message_logged": False
    }
    
    # 1. ЭКСТРЕННЫЙ ВХОД
    if data == "ep_crisis":
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, CRISIS_RESPONSE_PANIC)
        context["current_ep"] = "crisis"
        context["awaiting_story"] = False
        UserRepository.save_context(user_id, context)
        UserRepository.log_funnel_event(user_id, "ep_selected", {"ep": "crisis"})
        return
    
    # 2. СВОБОДНЫЙ ВХОД
    if data == "ep_free":
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, DEFAULT_QUESTION)
        context["current_ep"] = "free"
        context["awaiting_story"] = True
        context["first_message_logged"] = False
        context["messages"].append({"role": "assistant", "content": DEFAULT_QUESTION})
        UserRepository.save_context(user_id, context)
        UserRepository.log_funnel_event(user_id, "ep_selected", {"ep": "free"})
        return
    
    # 3. ПОДМЕНЮ "МОЁ СОСТОЯНИЕ"
    if data == "cat_state":
        bot.answer_callback_query(call.id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        for item in STATE_SUBMENU:
            markup.add(types.InlineKeyboardButton(text=item["text"], callback_data=item["callback_data"]))
        bot.send_message(user_id, "Что ближе к тому, что ты чувствуешь?", reply_markup=markup)
        return
    
    # 4. ВЫБОР ИЗ ПОДМЕНЮ СОСТОЯНИЯ
    if data.startswith("state_"):
        bot.answer_callback_query(call.id)
        state_map = {s["callback_data"]: s["text"] for s in STATE_SUBMENU}
        selected = state_map.get(data, "Моё состояние")
        
        bot.send_message(user_id, DEFAULT_QUESTION)
        context["current_ep"] = selected
        context["awaiting_story"] = True
        context["first_message_logged"] = False
        context["messages"].append({"role": "assistant", "content": DEFAULT_QUESTION})
        UserRepository.save_context(user_id, context)
        UserRepository.log_funnel_event(user_id, "ep_selected", {"ep": selected})
        return
    
    # 5. ОБЫЧНАЯ КАТЕГОРИЯ
    if data.startswith("cat_"):
        bot.answer_callback_query(call.id)
        cat_map = {c["callback_data"]: c["text"] for c in MAIN_CATEGORIES}
        selected = cat_map.get(data, "Тема")
        
        bot.send_message(user_id, DEFAULT_QUESTION)
        context["current_ep"] = selected
        context["awaiting_story"] = True
        context["first_message_logged"] = False
        context["messages"].append({"role": "assistant", "content": DEFAULT_QUESTION})
        UserRepository.save_context(user_id, context)
        UserRepository.log_funnel_event(user_id, "ep_selected", {"ep": selected})
        return

# ============================================
# MAIN MESSAGE HANDLER (V1 Clean Analytics)
# ============================================

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_id = message.chat.id
    text = message.text
    
    if not text or len(text) > 5000:
        bot.send_message(user_id, "Сообщение слишком длинное. Сократи, пожалуйста.")
        return
    
    if message.message_id in processed_messages:
        return
    processed_messages.add(message.message_id)
    if len(processed_messages) > 1000:
        processed_messages.clear()
    
    # SAFETY CHECK
    safety = check_safety(text)
    if safety:
        bot.send_message(user_id, safety)
        return
    
    context = UserRepository.get_context(user_id)
    if not context:
        send_main_menu(message)
        return
    
    awaiting_story = context.get("awaiting_story", False)
    current_ep = context.get("current_ep")
    first_msg_logged = context.get("first_message_logged", False)
    
    # === ИССЛЕДОВАТЕЛЬСКАЯ ЛОГИКА: Первое сообщение после EP ===
    if awaiting_story and current_ep and not first_msg_logged and current_ep != "crisis":
        # 1. Анонимный лог первого предложения (для entry_points.md)
        log_anonymous_first_message(current_ep, text)
        
        # 2. Объективное событие воронки
        UserRepository.log_funnel_event(user_id, "first_message_sent", {
            "ep": current_ep,
            "length": len(text)
        })
        
        # 3. Помечаем, что первое сообщение уже залогировано
        context["first_message_logged"] = True
        
        # 4. Классификация с контекстом
        combined_input = f"[Контекст: {current_ep}]\n{text}"
        scene_result = classify_scene(combined_input)
        print(f"[SCENE DEBUG V1] EP={current_ep} → {scene_result}")
        context["awaiting_story"] = False
    else:
        scene_result = classify_scene(text)
        print(f"[SCENE DEBUG FREE] → {scene_result}")
    
    scene_id = scene_result.get("scene_id", "unknown")
    confidence = scene_result.get("confidence", 0.0)
    
    # === ГИПОТЕЗА LLM (вероятностные данные) ===
    if scene_id != "unknown":
        UserRepository.log_llm_hypothesis(
            user_id=user_id,
            hypothesis_type="scene_classification",
            value=scene_id,
            confidence=confidence,
            context_ep=current_ep
        )
        
        # Активация сцены в контексте диалога
        if confidence >= 0.7:
            scene_path = os.path.join("core", f"{scene_id}.md")
            if os.path.exists(scene_path):
                with open(scene_path, "r", encoding="utf-8") as f:
                    scene_content = f.read()
                context["messages"].append({
                    "role": "system",
                    "content": f"\n[АКТИВИРОВАНА СЦЕНА: {scene_id}]\n{scene_content}"
                })
                if scene_id not in context.get("modules_loaded", []):
                    context.setdefault("modules_loaded", []).append(scene_id)
    
    context["messages"].append({"role": "user", "content": text})
    if len(context["messages"]) > 50:
        context["messages"] = context["messages"][-25:]
    
    client = get_api_client(user_id)
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=context["messages"],
            temperature=0.7,
            max_tokens=1500
        )
        reply = response.choices[0].message.content
        context["messages"].append({"role": "assistant", "content": reply})
        UserRepository.save_context(user_id, context)
        bot.send_message(user_id, reply)
        
        # === ВОРОНКА: Проверка микрошага (объективный факт) ===
        if scene_id != "unknown" and confidence >= 0.7:
            microstep_markers = ["микрошаг", "следующий шаг", "попробуй", "сделай", "выбери одно"]
            if any(m in reply.lower() for m in microstep_markers):
                UserRepository.log_funnel_event(user_id, "reached_microstep", {
                    "scene": scene_id,
                    "ep": current_ep
                })
                
    except Exception as e:
        bot.send_message(user_id, f"Ошибка API: {e}")

# ============================================
# RUN
# ============================================

if __name__ == "__main__":
    print("🤖 Бот запущен (V1 Clean Analytics)")
    print(f"📊 Анонимные первые сообщения → {ANON_LOG_PATH}")
    print("Нажми Ctrl+C для остановки")
    bot.polling(none_stop=True)