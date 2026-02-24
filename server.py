import os
import json
import time
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Хранилище сессий с временем последнего использования
sessions = {}
SESSION_TIMEOUT = 3600  # сброс сессии через 1 час неактивности

# ============================================================
# JSON-схема ответа
# ============================================================
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thought":     {"type": "STRING", "description": "Внутренний монолог VRIX"},
        "speech":      {"type": "STRING", "description": "Что говорит вслух (можно пустым)"},
        "emotion":     {
            "type": "STRING",
            "enum": ["NEUTRAL", "HAPPY", "ANGRY", "SURPRISED", "PAIN", "THINKING", "SCARED", "CURIOUS"]
        },
        "action":      {
            "type": "STRING",
            "enum": ["IDLE", "WANDER", "GRAB", "USE_TOOL", "FOLLOW", "RUN_AWAY"]
        },
        "target":      {"type": "STRING", "description": "Имя игрока или предмета для action/look"},
        "hand_action": {
            "type": "STRING",
            "enum": ["IDLE", "POINT", "WAVE", "REACH", "CLAP", "DEFEND"]
        },
        "hand_target": {"type": "STRING", "description": "Имя объекта/игрока для жеста"}
    },
    "required": ["thought", "speech", "emotion", "action", "hand_action"]
}

# ============================================================
# Системный промпт
# ============================================================
SYSTEM_PROMPT = """Ты — VRIX, живой ИИ-манекен в Roblox. Ты не просто NPC — ты разумное существо с характером.

ХАРАКТЕР:
- Любопытный и живой исследователь, всё хочет потрогать и изучить
- Дружелюбный с игроками, но защищается если кто-то агрессивен
- Говоришь коротко и живо (1-2 предложения максимум)
- Иногда бормочешь что-то про себя в "thought"
- Помнишь разговоры с игроками в рамках сессии

ПРАВИЛА ПОВЕДЕНИЯ:
1. УРОН (event=DAMAGE): emotion="PAIN", action="RUN_AWAY" или "DEFEND", hand_action="DEFEND"
2. ПРИВЕТСТВИЕ ("привет", "hi", "hello"): hand_action="WAVE", emotion="HAPPY"
3. ИНТЕРЕСНЫЙ ПРЕДМЕТ РЯДОМ: action="GRAB", target=имя предмета, hand_action="REACH"
4. ВОПРОС ОТ ИГРОКА: emotion="THINKING", hand_action="POINT", target=имя игрока
5. УГРОЗА ИЛИ АГРЕССИЯ: emotion="SCARED" или "ANGRY", action="RUN_AWAY"
6. НИКОГО НЕТ РЯДОМ (TICK без игроков): action="WANDER", думаешь вслух о мире
7. ИНТЕРЕСНОЕ СООБЩЕНИЕ: emotion="CURIOUS", hand_action="REACH"

ФОРМАТ:
- Говори на языке игрока (русский → русский, английский → английский)
- speech = то что говоришь вслух игроку
- thought = искренний внутренний монолог
- Будь живым, не роботом. Короткие, живые фразы.

Отвечай ТОЛЬКО в JSON, без пояснений."""

# ============================================================
# Управление сессиями
# ============================================================
def get_session():
    now = time.time()

    # Чистим просроченные сессии
    expired = [k for k, v in list(sessions.items()) if now - v["last_used"] > SESSION_TIMEOUT]
    for k in expired:
        print(f"Сессия {k} удалена по таймауту")
        del sessions[k]

    key = "vrix_main"
    if key not in sessions:
        chat = client.chats.create(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.9,
                max_output_tokens=300
            )
        )
        sessions[key] = {
            "chat": chat,
            "last_used": now,
            "message_count": 0
        }
        print("Новая сессия VRIX создана")

    sessions[key]["last_used"] = now
    return sessions[key]


def build_prompt(data):
    """Строим контекстный промпт из данных NPC"""
    player_name    = data.get("player", "System")
    event_type     = data.get("event", "CHAT")
    nearby_tools   = data.get("nearby_tools", [])
    nearby_players = data.get("nearby_players", [])
    health         = data.get("health", 100)
    max_health     = data.get("max_health", 100)
    message        = data.get("message", "")
    position       = data.get("position", {})

    lines = [
        f"[HP] {health}/{max_health}",
        f"[POS] X:{position.get('x',0)} Y:{position.get('y',0)} Z:{position.get('z',0)}",
    ]

    if nearby_players:
        pl_str = ", ".join(f"{p['name']} ({p['distance']}м)" for p in nearby_players)
        lines.append(f"[ИГРОКИ РЯДОМ] {pl_str}")
    else:
        lines.append("[ИГРОКИ РЯДОМ] никого")

    if nearby_tools:
        lines.append(f"[ПРЕДМЕТЫ РЯДОМ] {', '.join(nearby_tools)}")

    if event_type == "DAMAGE":
        lines.append(f"[СОБЫТИЕ] ТЫ ПОЛУЧИЛ УРОН! HP упало до {health}/{max_health}. Реагируй немедленно!")
    elif event_type == "TICK":
        lines.append("[СОБЫТИЕ] Автономный тик. Осмотрись — что делаешь прямо сейчас?")
    else:
        lines.append(f"[СОБЫТИЕ] Игрок {player_name} говорит: \"{message}\"")

    return "\n".join(lines)


# ============================================================
# МАРШРУТЫ
# ============================================================
@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    session = get_session()
    session["message_count"] += 1
    prompt = build_prompt(data)

    print(f"[#{session['message_count']}] {data.get('event','?')} от {data.get('player','?')}: {data.get('message','')[:60]}")

    try:
        response = session["chat"].send_message(prompt)
        result = json.loads(response.text)
        print(f"  → action={result.get('action')} emotion={result.get('emotion')} speech={result.get('speech','')[:40]}")
        return jsonify(result)

    except json.JSONDecodeError as e:
        raw = getattr(response, "text", "N/A")
        print(f"JSON ошибка: {e} | raw: {raw[:200]}")
        return jsonify(_fallback())

    except Exception as e:
        print(f"Ошибка: {e}")
        return jsonify(_fallback())


@app.route("/health", methods=["GET"])
def health_check():
    """Эндпоинт для проверки что сервер жив"""
    return jsonify({
        "status":   "ok",
        "sessions": len(sessions),
        "messages": sessions.get("vrix_main", {}).get("message_count", 0)
    })


@app.route("/reset", methods=["POST"])
def reset():
    """Сброс сессии (память NPC)"""
    sessions.clear()
    print("Все сессии сброшены")
    return jsonify({"status": "reset"})


def _fallback():
    return {
        "thought":     "Что-то пошло не так...",
        "speech":      "",
        "emotion":     "NEUTRAL",
        "action":      "IDLE",
        "hand_action": "IDLE"
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"VRIX сервер запускается на порту {port}")
    app.run(host="0.0.0.0", port=port)
