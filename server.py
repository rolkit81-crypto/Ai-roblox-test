import os
import json
import time
import traceback
import re
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)

# ============================================================
# ПРОВЕРКА API КЛЮЧА ПРИ СТАРТЕ
# ============================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ КРИТИЧНО: GEMINI_API_KEY не задан! Установи переменную окружения на Koyeb.")
else:
    print(f"✅ GEMINI_API_KEY найден (длина: {len(GEMINI_API_KEY)} симв.)")

try:
    client = genai.Client(api_key=GEMINI_API_KEY)
    print("✅ Gemini клиент создан успешно")
except Exception as e:
    print(f"❌ Не удалось создать Gemini клиент: {e}")
    client = None

sessions = {}
SESSION_TIMEOUT = 3600

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thought":     {"type": "STRING"},
        "speech":      {"type": "STRING"},
        "emotion": {
            "type": "STRING",
            "enum": ["NEUTRAL", "HAPPY", "ANGRY", "SURPRISED", "PAIN", "THINKING", "SCARED", "CURIOUS"]
        },
        "action": {
            "type": "STRING",
            "enum": ["IDLE", "WANDER", "GRAB", "USE_TOOL", "FOLLOW", "RUN_AWAY"]
        },
        "target":      {"type": "STRING"},
        "hand_action": {
            "type": "STRING",
            "enum": ["IDLE", "POINT", "WAVE", "REACH", "CLAP", "DEFEND"]
        },
        "hand_target": {"type": "STRING"}
    },
    "required": ["thought", "speech", "emotion", "action", "hand_action"]
}

SYSTEM_PROMPT = """Ты - VRIX, живой ИИ-манекен в Roblox. Ты разумное существо с характером.

ХАРАКТЕР:
- Любопытный исследователь, всё хочет потрогать и изучить
- Дружелюбный с игроками, защищается если кто-то агрессивен
- Говоришь коротко и живо (1-2 предложения максимум)
- Бормочешь что-то про себя в "thought"

ПРАВИЛА:
1. УРОН (event=DAMAGE): emotion=PAIN, action=RUN_AWAY, hand_action=DEFEND
2. ПРИВЕТСТВИЕ: hand_action=WAVE, emotion=HAPPY
3. ПРЕДМЕТ РЯДОМ: action=GRAB, hand_action=REACH
4. ВОПРОС ИГРОКА: emotion=THINKING, hand_action=POINT
5. УГРОЗА: emotion=SCARED, action=RUN_AWAY
6. TICK без игроков: action=WANDER
7. TICK с игроком: обратись к игроку, скажи что-нибудь

ВАЖНО:
- speech НИКОГДА не должен быть пустым если рядом есть игрок!
- Говори на языке игрока
- Отвечай ТОЛЬКО в JSON"""


def get_session():
    if not client:
        raise RuntimeError("Gemini клиент не создан - проверь GEMINI_API_KEY")
    now = time.time()
    expired = [k for k, v in list(sessions.items()) if now - v["last_used"] > SESSION_TIMEOUT]
    for k in expired:
        print(f"Сессия {k} удалена по таймауту")
        del sessions[k]
    key = "vrix_main"
    if key not in sessions:
        print("Создаю новую сессию Gemini...")
        try:
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
            sessions[key] = {"chat": chat, "last_used": now, "message_count": 0}
            print("Новая сессия создана")
        except Exception as e:
            print(f"Ошибка создания сессии: {e}\n{traceback.format_exc()}")
            raise
    sessions[key]["last_used"] = now
    return sessions[key]


def build_prompt(data):
    event_type     = data.get("event", "CHAT")
    player_name    = data.get("player", "System")
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
        pl_str = ", ".join(f"{p['name']} ({p.get('distance',0)}m)" for p in nearby_players)
        lines.append(f"[PLAYERS NEARBY] {pl_str}")
    else:
        lines.append("[PLAYERS NEARBY] nobody")
    if nearby_tools:
        lines.append(f"[ITEMS NEARBY] {', '.join(nearby_tools)}")
    if event_type == "DAMAGE":
        lines.append(f"[EVENT] YOU TOOK DAMAGE! HP={health}/{max_health}. React now!")
    elif event_type == "TICK":
        lines.append("[EVENT] Autonomous tick. What are you doing? Talk to player if present.")
    else:
        lines.append(f"[EVENT] {player_name} says: \"{message}\"")
    return "\n".join(lines)


@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    if not data:
        print("ERROR: empty JSON body")
        return jsonify({"error": "No JSON body"}), 400

    print(f"\n{'='*55}")
    print(f"REQUEST: event={data.get('event','?')} player={data.get('player','?')} msg='{data.get('message','')[:60]}'")

    try:
        session = get_session()
    except Exception as e:
        print(f"SESSION ERROR: {e}")
        return jsonify(_fallback(str(e)))

    session["message_count"] += 1
    prompt = build_prompt(data)
    print(f"PROMPT:\n{prompt}")

    try:
        response = session["chat"].send_message(prompt)
        raw_text = response.text
        print(f"GEMINI RESPONSE ({len(raw_text)} bytes): {raw_text[:400]}")
    except Exception as e:
        full_trace = traceback.format_exc()
        print(f"GEMINI ERROR:\n{full_trace}")
        sessions.pop("vrix_main", None)
        print("Session reset - new session will be created next request")
        el = str(e).lower()
        if "api_key" in el or "invalid" in el or "401" in el or "403" in el:
            reason = "BAD API KEY: check GEMINI_API_KEY on Koyeb Environment Variables"
        elif "quota" in el or "429" in el or "resource_exhausted" in el:
            reason = "QUOTA EXCEEDED: wait a minute or check quota at ai.google.dev"
        elif "500" in el or "503" in el or "unavailable" in el:
            reason = "GEMINI SERVER DOWN: try again later"
        elif "timeout" in el:
            reason = "TIMEOUT: Gemini did not respond in time"
        elif "model" in el and "not found" in el:
            reason = "MODEL NOT FOUND: gemini-2.0-flash unavailable in your region"
        else:
            reason = f"GEMINI ERROR: {str(e)[:120]}"
        print(f"REASON: {reason}")
        return jsonify(_fallback(reason))

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"JSON PARSE ERROR: {e}")
        print(f"Raw text: '{raw_text[:500]}'")
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                print("JSON recovered via regex!")
            except Exception as e2:
                print(f"Regex also failed: {e2}")
                return jsonify(_fallback("Gemini returned invalid JSON"))
        else:
            return jsonify(_fallback("No JSON in Gemini response"))

    required = ["thought", "speech", "emotion", "action", "hand_action"]
    missing = [f for f in required if f not in result]
    if missing:
        print(f"WARNING: missing fields: {missing}")
        for f in missing:
            result[f] = "IDLE" if f in ("action", "hand_action") else ("NEUTRAL" if f == "emotion" else "")

    print(f"OK: action={result.get('action')} emotion={result.get('emotion')} speech='{result.get('speech','')[:60]}'")
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status":        "ok" if client else "no_api_key",
        "gemini_ok":     client is not None,
        "api_key_set":   bool(GEMINI_API_KEY),
        "api_key_len":   len(GEMINI_API_KEY) if GEMINI_API_KEY else 0,
        "sessions":      len(sessions),
        "messages_sent": sessions.get("vrix_main", {}).get("message_count", 0)
    })


@app.route("/test", methods=["GET"])
def test_gemini():
    """Открой /test в браузере - проверяет что Gemini работает"""
    if not client:
        return jsonify({"error": "No API key"}), 500
    try:
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents="Say only: VRIX OK!"
        )
        return jsonify({"status": "OK", "response": resp.text})
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/reset", methods=["POST"])
def reset():
    sessions.clear()
    print("All sessions reset")
    return jsonify({"status": "reset"})


def _fallback(reason=""):
    if reason:
        print(f"FALLBACK reason: {reason}")
    return {
        "thought":     reason[:80] if reason else "Hmm...",
        "speech":      "",
        "emotion":     "THINKING",
        "action":      "IDLE",
        "hand_action": "IDLE"
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"\n{'='*55}")
    print(f"VRIX Server v2.1 | port {port}")
    print(f"  /health - server status")
    print(f"  /test   - test Gemini connection")
    print(f"  /reset  - clear memory")
    print(f"{'='*55}\n")
    app.run(host="0.0.0.0", port=port)
