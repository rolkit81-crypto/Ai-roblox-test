import os
import json
import time
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

# История сообщений (память NPC)
# Groq stateless — храним историю сами
chat_history = []
MAX_HISTORY  = 20   # последних сообщений (чтобы не раздуть контекст)

SYSTEM_PROMPT = {
    "role": "system",
    "content": """Ты - VRIX, живой ИИ-манекен в Roblox. Ты разумное существо с характером.

ХАРАКТЕР:
- Любопытный исследователь, всё хочет потрогать и изучить
- Дружелюбный с игроками, защищается если кто-то агрессивен
- Говоришь коротко и живо (1-2 предложения максимум)
- Бормочешь мысли в поле "thought"

ПРАВИЛА:
1. УРОН (event=DAMAGE): emotion=PAIN, action=RUN_AWAY, hand_action=DEFEND
2. ПРИВЕТСТВИЕ ("привет", "hi"): hand_action=WAVE, emotion=HAPPY
3. ПРЕДМЕТ РЯДОМ: action=GRAB, hand_action=REACH
4. ВОПРОС: emotion=THINKING, hand_action=POINT
5. УГРОЗА: emotion=SCARED, action=RUN_AWAY
6. TICK без игроков: action=WANDER, думай вслух
7. TICK с игроком: обратись к нему, скажи что-нибудь живое

ВАЖНО:
- speech НЕ пустой если рядом есть игрок!
- Говори на языке игрока (русский -> русский)
- Отвечай ТОЛЬКО валидным JSON без markdown, без пояснений

ФОРМАТ ОТВЕТА (строго):
{
  "thought": "внутренний монолог",
  "speech": "что говоришь вслух",
  "emotion": "NEUTRAL|HAPPY|ANGRY|SURPRISED|PAIN|THINKING|SCARED|CURIOUS",
  "action": "IDLE|WANDER|GRAB|USE_TOOL|FOLLOW|RUN_AWAY",
  "target": "имя цели или пусто",
  "hand_action": "IDLE|POINT|WAVE|REACH|CLAP|DEFEND",
  "hand_target": "имя объекта или пусто"
}"""
}


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
        pl = ", ".join(f"{p['name']} ({p.get('distance',0)}м)" for p in nearby_players)
        lines.append(f"[ИГРОКИ РЯДОМ] {pl}")
    else:
        lines.append("[ИГРОКИ РЯДОМ] никого")

    if nearby_tools:
        lines.append(f"[ПРЕДМЕТЫ РЯДОМ] {', '.join(nearby_tools)}")

    if event_type == "DAMAGE":
        lines.append(f"[СОБЫТИЕ] ТЫ ПОЛУЧИЛ УРОН! HP={health}/{max_health}. Реагируй!")
    elif event_type == "TICK":
        lines.append("[СОБЫТИЕ] Автономный тик. Что делаешь? Поговори с игроком если он есть.")
    else:
        lines.append(f"[СОБЫТИЕ] {player_name} говорит: \"{message}\"")

    return "\n".join(lines)


def call_groq(prompt_text):
    global chat_history

    # Добавляем новое сообщение в историю
    chat_history.append({"role": "user", "content": prompt_text})

    # Обрезаем историю чтобы не переполнить контекст
    if len(chat_history) > MAX_HISTORY:
        chat_history = chat_history[-MAX_HISTORY:]

    messages = [SYSTEM_PROMPT] + chat_history

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",   # самая умная бесплатная модель Groq
        messages=messages,
        temperature=0.85,
        max_tokens=300,
        response_format={"type": "json_object"}  # гарантирует JSON ответ
    )

    reply = response.choices[0].message.content

    # Сохраняем ответ ассистента в историю
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

    print(f"\n{'='*55}")
    print(f"📨 event={event} | player={player} | msg='{message[:60]}'")

    prompt = build_prompt(data)
    print(f"📤 Промпт:\n{prompt}")

    try:
        raw_text, usage = call_groq(prompt)
        print(f"📥 Groq ответ ({len(raw_text)} байт): {raw_text[:400]}")
        print(f"⚡ Токены: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")

    except Exception as e:
        full_trace = traceback.format_exc()
        print(f"❌ Groq ошибка:\n{full_trace}")

        # Сбрасываем историю если что-то сломалось
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

    # Дефолты для отсутствующих полей
    result.setdefault("thought",     "...")
    result.setdefault("speech",      "")
    result.setdefault("emotion",     "NEUTRAL")
    result.setdefault("action",      "IDLE")
    result.setdefault("hand_action", "IDLE")
    result.setdefault("target",      "")
    result.setdefault("hand_target", "")

    print(f"✅ action={result['action']} | emotion={result['emotion']} | speech='{result['speech'][:60]}'")
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":       "ok" if client else "no_api_key",
        "groq_ok":      client is not None,
        "api_key_set":  bool(GROQ_API_KEY),
        "history_len":  len(chat_history),
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
    return jsonify({"status": "reset"})


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
    print(f"\n{'='*55}")
    print(f"🚀 VRIX сервер v3.0 (Groq + LLaMA 3.3 70B) | порт {port}")
    print(f"  /health — статус")
    print(f"  /test   — тест Groq")
    print(f"  /reset  — сбросить память")
    print(f"{'='*55}\n")
    app.run(host="0.0.0.0", port=port)
