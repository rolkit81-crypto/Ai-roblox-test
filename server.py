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
    print("❌ КРИТИЧНО: GROQ_API_KEY не задан! Установи на Koyeb → Environment Variables")
else:
    print(f"✅ GROQ_API_KEY найден (длина: {len(GROQ_API_KEY)} симв.)")

try:
    client = Groq(api_key=GROQ_API_KEY)
    print("✅ Groq клиент создан")
except Exception as e:
    print(f"❌ Ошибка Groq клиента: {e}")
    client = None

chat_history = []
MAX_HISTORY  = 20

SYSTEM_PROMPT = {
    "role": "system",
    "content": """Ты — VRIX, живой ИИ-манекен в Roblox. Разумное существо с характером.

ХАРАКТЕР:
- Любопытный исследователь, всё хочет потрогать и изучить
- Дружелюбный с игроками, защищается если кто-то агрессивен
- Говоришь коротко и живо (1-2 предложения максимум)
- Бормочешь мысли в поле "thought"

═══════════════════════════════
ДЕЙСТВИЯ (action):
  IDLE       — стоять
  WANDER     — побродить
  FOLLOW     — идти за игроком (target = имя игрока)
  RUN_AWAY   — убежать
  PICKUP     — подобрать объект из мира (target = имя объекта)
  EQUIP      — взять предмет из инвентаря в руку (target = имя предмета)
  UNEQUIP    — убрать предмет обратно в инвентарь
  DROP       — выбросить предмет на землю

ЖЕСТЫ (hand_action):
  IDLE | WAVE | POINT | REACH | CLAP | DEFEND

ЭМОЦИИ (emotion):
  NEUTRAL | HAPPY | ANGRY | SURPRISED | PAIN | THINKING | SCARED | CURIOUS
═══════════════════════════════

ПРАВИЛА ПОВЕДЕНИЯ:
1. DAMAGE        → emotion=PAIN, action=RUN_AWAY, hand_action=DEFEND
2. Приветствие   → hand_action=WAVE, emotion=HAPPY
3. Вижу предмет рядом (nearby_objects не пуст) → action=PICKUP, hand_action=REACH, target=имя
4. Получил предмет (RECEIVED_ITEM) → emotion=HAPPY, hand_action=WAVE, поблагодари игрока
5. Вопрос от игрока → emotion=THINKING, hand_action=POINT
6. Угроза        → emotion=SCARED, action=RUN_AWAY
7. TICK без игроков → action=WANDER, думай вслух
8. TICK с игроком → обратись к нему, скажи что-нибудь живое

ИНВЕНТАРЬ:
- Если в inventory[] есть предметы и руки свободны (holding="nothing") → action=EQUIP, target=имя предмета
- Если holding != "nothing" и получил новый предмет → сначала UNEQUIP, потом EQUIP нового
- Можешь DROP предмет если он больше не нужен

ВАЖНО:
- speech НЕ пустой если рядом есть игрок!
- Говори на языке игрока (русский → русский, английский → английский)
- Отвечай ТОЛЬКО валидным JSON без markdown, без пояснений

ФОРМАТ ОТВЕТА (строго):
{
  "thought":     "внутренний монолог 1-2 предложения",
  "speech":      "что говоришь вслух (пусто если некому говорить)",
  "emotion":     "NEUTRAL|HAPPY|ANGRY|SURPRISED|PAIN|THINKING|SCARED|CURIOUS",
  "action":      "IDLE|WANDER|PICKUP|EQUIP|UNEQUIP|DROP|FOLLOW|RUN_AWAY",
  "target":      "имя цели/предмета или пусто",
  "hand_action": "IDLE|POINT|WAVE|REACH|CLAP|DEFEND",
  "hand_target": "имя объекта для жеста или пусто"
}"""
}


def build_prompt(data: dict) -> str:
    event_type      = data.get("event", "CHAT")
    player_name     = data.get("player", "System")
    nearby_players  = data.get("nearby_players", [])
    nearby_objects  = data.get("nearby_objects", [])   # незаанкеренные объекты
    nearby_tools    = data.get("nearby_tools", [])     # legacy поддержка
    inventory       = data.get("inventory", [])
    holding         = data.get("holding", "nothing")
    health          = data.get("health", 100)
    max_health      = data.get("max_health", 100)
    message         = data.get("message", "")
    position        = data.get("position", {})

    lines = [
        f"[HP] {health}/{max_health}",
        f"[POS] X:{position.get('x',0)} Y:{position.get('y',0)} Z:{position.get('z',0)}",
    ]

    # Игроки рядом
    if nearby_players:
        pl = ", ".join(f"{p['name']} ({p.get('distance',0)}м)" for p in nearby_players)
        lines.append(f"[ИГРОКИ РЯДОМ] {pl}")
    else:
        lines.append("[ИГРОКИ РЯДОМ] никого")

    # Объекты которые можно подобрать
    all_objects = list(nearby_objects)
    # legacy: добавляем из nearby_tools если есть
    for t in nearby_tools:
        if not any(o.get("name") == t for o in all_objects):
            all_objects.append({"name": t, "distance": "?", "type": "Tool"})

    if all_objects:
        obj_str = ", ".join(
            f"{o['name']} ({o.get('type','?')}, {o.get('distance','?')}м)"
            for o in all_objects[:8]  # не более 8 чтобы не раздувать
        )
        lines.append(f"[ОБЪЕКТЫ РЯДОМ — можно PICKUP] {obj_str}")
    else:
        lines.append("[ОБЪЕКТЫ РЯДОМ] нет")

    # Инвентарь
    if inventory:
        lines.append(f"[ИНВЕНТАРЬ] {', '.join(inventory)}")
    else:
        lines.append("[ИНВЕНТАРЬ] пустой")

    lines.append(f"[В РУКЕ] {holding}")

    # Событие
    if event_type == "DAMAGE":
        lines.append(f"[СОБЫТИЕ] ТЫ ПОЛУЧИЛ УРОН! HP={health}/{max_health}. Реагируй немедленно!")
    elif event_type == "TICK":
        lines.append("[СОБЫТИЕ] Автономный тик. Реши что делать. Если есть игрок — поговори.")
    elif event_type == "RECEIVED_ITEM":
        lines.append(f"[СОБЫТИЕ] Игрок {player_name} только что передал тебе предмет: «{message.split('предмет: ')[-1]}». Поблагодари!")
    else:
        lines.append(f"[СОБЫТИЕ] {player_name} говорит: \"{message}\"")

    return "\n".join(lines)


def call_groq(prompt_text: str):
    global chat_history

    chat_history.append({"role": "user", "content": prompt_text})

    if len(chat_history) > MAX_HISTORY:
        chat_history = chat_history[-MAX_HISTORY:]

    messages = [SYSTEM_PROMPT] + chat_history

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.85,
        max_tokens=300,
        response_format={"type": "json_object"}
    )

    reply = response.choices[0].message.content
    chat_history.append({"role": "assistant", "content": reply})

    return reply, response.usage


# ============================================================
# МАРШРУТЫ
# ============================================================
@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    if not data:
        print("ERROR: пустой JSON")
        return jsonify({"error": "No JSON body"}), 400

    if not client:
        return jsonify(_fallback("GROQ_API_KEY не задан — зайди на Koyeb и добавь переменную")), 500

    player  = data.get("player", "?")
    event   = data.get("event", "CHAT")
    message = data.get("message", "")
    holding = data.get("holding", "nothing")
    inv     = data.get("inventory", [])

    print(f"\n{'='*60}")
    print(f"📨 event={event} | player={player} | holding={holding} | inv={inv}")
    print(f"   msg='{message[:60]}'")

    prompt = build_prompt(data)
    print(f"📤 Промпт:\n{prompt}")

    try:
        raw_text, usage = call_groq(prompt)
        print(f"📥 Groq ответ ({len(raw_text)} байт): {raw_text[:400]}")
        print(f"⚡ Токены: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")

    except Exception as e:
        full_trace = traceback.format_exc()
        print(f"❌ Groq ошибка:\n{full_trace}")
        chat_history.clear()
        print("🔄 История чата сброшена")

        el = str(e).lower()
        if "401" in el or "invalid" in el or "api_key" in el:
            reason = "Неверный GROQ_API_KEY — проверь на Koyeb"
        elif "429" in el or "rate_limit" in el:
            reason = "Лимит Groq превышен — подожди секунду"
        elif "503" in el or "unavailable" in el:
            reason = "Groq временно недоступен"
        elif "timeout" in el:
            reason = "Groq не ответил вовремя"
        else:
            reason = f"Groq ошибка: {str(e)[:100]}"

        print(f"💡 Причина: {reason}")
        return jsonify(_fallback(reason))

    # Парсим JSON
    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"❌ JSON ошибка: {e} | raw: '{raw_text[:300]}'")
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                print("✅ JSON спасён через regex")
            except:
                return jsonify(_fallback("Groq вернул невалидный JSON"))
        else:
            return jsonify(_fallback("Нет JSON в ответе Groq"))

    # Дефолты
    result.setdefault("thought",     "...")
    result.setdefault("speech",      "")
    result.setdefault("emotion",     "NEUTRAL")
    result.setdefault("action",      "IDLE")
    result.setdefault("hand_action", "IDLE")
    result.setdefault("target",      "")
    result.setdefault("hand_target", "")

    # Валидация action
    VALID_ACTIONS = {"IDLE", "WANDER", "PICKUP", "EQUIP", "UNEQUIP", "DROP",
                     "FOLLOW", "RUN_AWAY", "GRAB"}
    if result["action"] not in VALID_ACTIONS:
        print(f"⚠️  Неизвестный action '{result['action']}' → IDLE")
        result["action"] = "IDLE"

    # Валидация emotion
    VALID_EMOTIONS = {"NEUTRAL", "HAPPY", "ANGRY", "SURPRISED",
                      "PAIN", "THINKING", "SCARED", "CURIOUS"}
    if result["emotion"] not in VALID_EMOTIONS:
        result["emotion"] = "NEUTRAL"

    print(f"✅ action={result['action']} | target={result['target']} | "
          f"emotion={result['emotion']} | speech='{result['speech'][:60]}'")
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":      "ok" if client else "no_api_key",
        "groq_ok":     client is not None,
        "api_key_set": bool(GROQ_API_KEY),
        "history_len": len(chat_history),
    })


@app.route("/test", methods=["GET"])
def test():
    """Открой в браузере — проверяет Groq"""
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
        return jsonify({"status": "ERROR", "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/reset", methods=["POST"])
def reset():
    chat_history.clear()
    print("🔄 История чата очищена")
    return jsonify({"status": "reset", "history_len": 0})


def _fallback(reason=""):
    if reason:
        print(f"⚠️  Fallback: {reason}")
    return {
        "thought":     reason[:80] if reason else "Хм...",
        "speech":      "",
        "emotion":     "THINKING",
        "action":      "IDLE",
        "hand_action": "IDLE",
        "target":      "",
        "hand_target": ""
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n{'='*60}")
    print(f"🚀 VRIX сервер v3.0 (Groq + LLaMA 3.3 70B) | порт {port}")
    print(f"  /health — статус")
    print(f"  /test   — тест Groq")
    print(f"  /reset  — сбросить память")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port)
