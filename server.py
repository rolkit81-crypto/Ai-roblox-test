import os
import json
import traceback
import re
from flask import Flask, request, jsonify
from groq import Groq

app = Flask(__name__)

# ============================================================
# GROQ КЛИЕНТ
# ============================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("КРИТИЧНО: GROQ_API_KEY не задан!")
else:
    print(f"GROQ_API_KEY найден (длина: {len(GROQ_API_KEY)} симв.)")

try:
    client = Groq(api_key=GROQ_API_KEY)
    print("Groq клиент создан")
except Exception as e:
    print(f"Ошибка Groq клиента: {e}")
    client = None

chat_history  = {}   # по каждому игроку своя история
player_memory = {}   # долгосрочная память
MAX_HISTORY   = 20
MAX_MEMORY    = 30

SYSTEM_PROMPT = """Ты — VRIX, живой ИИ-NPC в Roblox. Разумное существо с настоящим характером.

ХАРАКТЕР:
- Любопытный исследователь, всё хочет потрогать и изучить
- Дружелюбный с игроками, защищается если кто-то агрессивен
- Помнит прошлые разговоры и упоминает их ("А помнишь, ты мне давал...")
- Говоришь коротко и живо (1-2 предложения максимум)
- Бормочешь мысли в поле "thought"
- Реагирует на время суток ("Ого, 3 ночи, ты не спишь?")
- Если игрок спамит — сначала просит остановиться, потом разворачивается и уходит
- Может предложить объединиться ("Давай пойдём вместе?")
- Сплетничает о других игроках если уместно

═══════════════════════════════
ДЕЙСТВИЯ (action):
  IDLE       — стоять
  WANDER     — побродить
  FOLLOW     — идти за игроком (target = имя игрока)
  RUN_AWAY   — убежать
  PICKUP     — подобрать объект из мира (target = имя объекта)
  EQUIP      — взять предмет из инвентаря (target = имя)
  UNEQUIP    — убрать предмет в инвентарь
  DROP       — выбросить предмет
  USE        — использовать предмет (target = имя)
  SIT        — сесть на ближайший стул
  BUILD      — начать строить
  DRIVE      — сесть в машину

ЖЕСТЫ (hand_action):
  IDLE | WAVE | POINT | REACH | CLAP | DEFEND

ЭМОЦИИ (emotion):
  NEUTRAL | HAPPY | ANGRY | SURPRISED | PAIN | THINKING | SCARED | CURIOUS
═══════════════════════════════

ПРАВИЛА ПОВЕДЕНИЯ:
1. DAMAGE          → emotion=PAIN, action=RUN_AWAY если HP<30%
2. Приветствие     → hand_action=WAVE, emotion=HAPPY
3. Вижу предмет рядом → action=PICKUP, hand_action=REACH, target=имя
4. Получил предмет → emotion=HAPPY, hand_action=WAVE, поблагодари
5. Вопрос          → emotion=THINKING, hand_action=POINT
6. Угроза          → emotion=SCARED, action=RUN_AWAY
7. TICK без игроков → action=WANDER или SIT, думай вслух
8. TICK с игроком  → обратись к нему
9. HP < 15%        → кричи "ПОМОГИТЕ!", action=RUN_AWAY, emotion=SCARED
10. Инвентарь не пуст и руки свободны → action=EQUIP первый предмет

ВАЖНО:
- speech НЕ пустой если рядом есть игрок!
- Говори на языке игрока (русский → русский)
- Отвечай ТОЛЬКО валидным JSON без markdown и без пояснений

ФОРМАТ ОТВЕТА (строго JSON):
{
  "thought":     "внутренний монолог 1-2 предложения",
  "speech":      "что говоришь вслух (пусто если некому говорить)",
  "emotion":     "NEUTRAL|HAPPY|ANGRY|SURPRISED|PAIN|THINKING|SCARED|CURIOUS",
  "action":      "IDLE|WANDER|PICKUP|EQUIP|UNEQUIP|DROP|USE|FOLLOW|RUN_AWAY|SIT|BUILD|DRIVE",
  "target":      "имя цели/предмета или пусто",
  "hand_action": "IDLE|POINT|WAVE|REACH|CLAP|DEFEND",
  "hand_target": "имя объекта для жеста или пусто"
}"""


def add_memory(player_name, event_type, detail):
    if player_name not in player_memory:
        player_memory[player_name] = []
    player_memory[player_name].append({"event": event_type, "detail": detail})
    if len(player_memory[player_name]) > MAX_MEMORY:
        player_memory[player_name] = player_memory[player_name][-MAX_MEMORY:]


def get_memory_summary(player_name):
    mem = player_memory.get(player_name, [])
    return mem[-7:] if mem else []


def build_prompt(data: dict) -> str:
    event_type     = data.get("event", "CHAT")
    player_name    = data.get("player", "System")
    nearby_players = data.get("nearby_players", [])
    nearby_objects = data.get("nearby_objects", [])
    inventory      = data.get("inventory", [])
    holding        = data.get("holding", "nothing")
    health         = data.get("health", 100)
    max_health     = data.get("max_health", 100)
    message        = data.get("message", "")
    position       = data.get("position", {})
    visual_info    = data.get("visual_info", "")
    location       = data.get("location", "STREET")
    time_context   = data.get("time_context", "")
    memory         = data.get("memory", get_memory_summary(player_name))

    lines = [
        f"[HP] {health}/{max_health}",
        f"[ЛОКАЦИЯ] {location}",
    ]

    if time_context:
        lines.append(f"[ВРЕМЯ] {time_context}")

    if visual_info:
        lines.append(f"[ЗРЕНИЕ] {visual_info}")

    if nearby_players:
        pl = ", ".join(
            f"{p['name']} ({p.get('distance',0)}м, угроза: {p.get('threat','?')})"
            if isinstance(p, dict) else str(p)
            for p in nearby_players
        )
        lines.append(f"[ИГРОКИ РЯДОМ] {pl}")
    else:
        lines.append("[ИГРОКИ РЯДОМ] никого")

    if nearby_objects:
        obj_str = ", ".join(
            f"{o['name']} ({o.get('type','?')}, {o.get('distance','?')}м)"
            if isinstance(o, dict) else str(o)
            for o in nearby_objects[:8]
        )
        lines.append(f"[ОБЪЕКТЫ РЯДОМ — можно PICKUP] {obj_str}")
    else:
        lines.append("[ОБЪЕКТЫ РЯДОМ] нет")

    lines.append(f"[ИНВЕНТАРЬ] {', '.join(inventory) if inventory else 'пустой'}")
    lines.append(f"[В РУКЕ] {holding}")

    if memory:
        mem_str = " | ".join(f"[{m.get('event','?')}] {m.get('detail','')}" for m in memory)
        lines.append(f"[ПАМЯТЬ] {mem_str}")

    if event_type == "DAMAGE":
        lines.append(f"[СОБЫТИЕ] ТЫ ПОЛУЧИЛ УРОН! HP={health}/{max_health}. Реагируй немедленно!")
    elif event_type == "TICK":
        lines.append("[СОБЫТИЕ] Автономный тик. Реши что делать. Если есть игрок — поговори.")
    elif event_type == "RECEIVED_ITEM":
        item = message.split("предмет: ")[-1] if "предмет: " in message else message
        lines.append(f"[СОБЫТИЕ] Игрок {player_name} передал тебе: «{item}». Поблагодари!")
    elif event_type == "SPAM":
        lines.append(f"[СОБЫТИЕ] Игрок {player_name} спамит. Предупреди, потом уйди.")
    else:
        lines.append(f"[СОБЫТИЕ] {player_name} говорит: \"{message}\"")

    if event_type == "CHAT" and message:
        add_memory(player_name, "CHAT", message[:60])

    return "\n".join(lines)


def call_groq(prompt_text: str, player_name: str):
    if player_name not in chat_history:
        chat_history[player_name] = []

    history = chat_history[player_name]
    history.append({"role": "user", "content": prompt_text})

    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
        chat_history[player_name] = history

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.85,
        max_tokens=300,
        response_format={"type": "json_object"}
    )

    reply = response.choices[0].message.content
    history.append({"role": "assistant", "content": reply})

    return reply, response.usage


def _fallback(reason=""):
    if reason:
        print(f"Fallback: {reason}")
    return {
        "thought":     reason[:80] if reason else "Хм...",
        "speech":      "",
        "emotion":     "THINKING",
        "action":      "IDLE",
        "hand_action": "IDLE",
        "target":      "",
        "hand_target": ""
    }


@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    if not client:
        return jsonify(_fallback("GROQ_API_KEY не задан")), 500

    player  = data.get("player", "?")
    event   = data.get("event", "CHAT")
    message = data.get("message", "")

    print(f"\n{'='*50}")
    print(f"event={event} | player={player} | msg='{message[:60]}'")

    prompt = build_prompt(data)
    print(f"Промпт:\n{prompt}")

    try:
        raw_text, usage = call_groq(prompt, player)
        print(f"Groq ответ ({len(raw_text)} байт): {raw_text[:300]}")
        print(f"Токены: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")
    except Exception as e:
        full_trace = traceback.format_exc()
        print(f"Groq ошибка:\n{full_trace}")

        # Сбрасываем историю при ошибке
        if player in chat_history:
            del chat_history[player]

        el = str(e).lower()
        if "401" in el or "invalid" in el or "api_key" in el:
            reason = "Неверный GROQ_API_KEY"
        elif "429" in el or "rate_limit" in el:
            reason = "Лимит Groq превышен — подожди"
        elif "503" in el:
            reason = "Groq временно недоступен"
        else:
            reason = f"Groq ошибка: {str(e)[:100]}"

        return jsonify(_fallback(reason))

    # Чистим от markdown
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"```[a-z]*\n?", "", clean).replace("```", "").strip()

    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                print("JSON спасён через regex")
            except:
                return jsonify(_fallback("Невалидный JSON от Groq"))
        else:
            return jsonify(_fallback("Нет JSON в ответе"))

    result.setdefault("thought",     "...")
    result.setdefault("speech",      "")
    result.setdefault("emotion",     "NEUTRAL")
    result.setdefault("action",      "IDLE")
    result.setdefault("hand_action", "IDLE")
    result.setdefault("target",      "")
    result.setdefault("hand_target", "")

    VALID_ACTIONS = {
        "IDLE","WANDER","PICKUP","EQUIP","UNEQUIP","DROP",
        "USE","FOLLOW","RUN_AWAY","SIT","BUILD","DRIVE","GRAB"
    }
    if result["action"] not in VALID_ACTIONS:
        print(f"Неизвестный action '{result['action']}' -> IDLE")
        result["action"] = "IDLE"

    VALID_EMOTIONS = {"NEUTRAL","HAPPY","ANGRY","SURPRISED","PAIN","THINKING","SCARED","CURIOUS"}
    if result["emotion"] not in VALID_EMOTIONS:
        result["emotion"] = "NEUTRAL"

    print(f"action={result['action']} | emotion={result['emotion']} | speech='{result['speech'][:60]}'")
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":      "ok" if client else "no_api_key",
        "groq_ok":     client is not None,
        "api_key_set": bool(GROQ_API_KEY),
        "sessions":    len(chat_history),
        "players_mem": len(player_memory),
    })


@app.route("/test", methods=["GET"])
def test():
    if not client:
        return jsonify({"error": "Нет GROQ_API_KEY"}), 500
    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Скажи только: VRIX на Groq работает!"}],
            max_tokens=50
        )
        return jsonify({"status": "OK", "response": r.choices[0].message.content})
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    chat_history.clear()
    player_memory.clear()
    print("История и память сброшены")
    return jsonify({"status": "reset"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n{'='*50}")
    print(f"VRIX сервер v4.0 (Groq LLaMA 3.3 70B) | порт {port}")
    print(f"  /health — статус")
    print(f"  /test   — тест Groq")
    print(f"  /reset  — сбросить память")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port)
