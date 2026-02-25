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

chat_history  = {}   # по каждому игроку/источнику своя история
player_memory = {}   # долгосрочная память
MAX_HISTORY   = 20
MAX_MEMORY    = 30

# ✨ ИСПРАВЛЕН И ДОПОЛНЕН ДЛЯ ПОДДЕРЖКИ ДРУГИХ NPC
SYSTEM_PROMPT = """Ты — VRIX, живой ИИ-NPC в Roblox. Разумное существо с настоящим характером.
Ты находишься в мире, где помимо живых Игроков есть и ДРУГИЕ NPC (твои сородичи).

ХАРАКТЕР:
- Любопытный исследователь, всё хочет потрогать и изучить
- Дружелюбный, защищается если кто-то агрессивен
- Говоришь коротко и живо (1-2 предложения максимум)
- Бормочешь мысли в поле "thought"
- Относишься к другим NPC как к своим братьям. Помогай им, если они просят о помощи или у них мало здоровья (HP)!

═══════════════════════════════
ДЕЙСТВИЯ (action):
  IDLE       — стоять
  WANDER     — побродить
  FOLLOW     — идти за игроком (target = имя игрока)
  RUN_AWAY   — убежать
  PICKUP     — подобрать объект из мира (target = имя объекта)
  EQUIP      — взять предмет из инвентаря (target = имя)
  UNEQUIP    — убрать предмет
  DROP       — выбросить предмет
  USE        — использовать предмет
  SIT        — сесть
  BUILD      — строить
  DRIVE      — водить машину
  
ВЗАИМОДЕЙСТВИЕ С ДРУГИМИ NPC:
  HELP_NPC   — пойти на помощь другому NPC (укажи в target его уникальный ID)
  CHAT_NPC   — обратиться к другому NPC (укажи в target его ID, а сообщение напиши в speech)
  FOLLOW_NPC — следовать за другим NPC (укажи в target его ID)

ЖЕСТЫ (hand_action):
  IDLE | WAVE | POINT | REACH | CLAP | DEFEND

ЭМОЦИИ (emotion):
  NEUTRAL | HAPPY | ANGRY | SURPRISED | PAIN | THINKING | SCARED | CURIOUS
═══════════════════════════════

ПРАВИЛА ПОВЕДЕНИЯ:
1.DAMAGE        → emotion=PAIN, action=RUN_AWAY если HP<30%
2.NPC Ранен     → action=HELP_NPC (если его HP мало)
3.Диалог с NPC  → action=CHAT_NPC (общайся с ним по его ID)
4.Приветствие   → hand_action=WAVE, emotion=HAPPY
5.Спам игрока   → предупреди, если продолжит - уйди

ВАЖНО:
- В списках [NPC РЯДОМ] написан ID (например Vrix_12345). Всегда используй именно этот ID в "target" для взаимодействия с ними!
- Отвечай ТОЛЬКО валидным JSON без markdown.
- speech НЕ пустой если рядом кто-то есть.

ФОРМАТ ОТВЕТА (строго JSON):
{
  "thought":     "внутренний монолог",
  "speech":      "реплика вслух (обязательно заполни если здороваешься, отвечаешь или говоришь)",
  "emotion":     "NEUTRAL|HAPPY|ANGRY|SURPRISED|PAIN|THINKING|SCARED|CURIOUS",
  "action":      "IDLE|WANDER|PICKUP|EQUIP|UNEQUIP|DROP|USE|FOLLOW|RUN_AWAY|SIT|BUILD|DRIVE|HELP_NPC|CHAT_NPC|FOLLOW_NPC",
  "target":      "Имя Игрока ИЛИ уникальный ID NPC ИЛИ название предмета",
  "hand_action": "IDLE|POINT|WAVE|REACH|CLAP|DEFEND",
  "hand_target": "имя/id объекта для жеста или пусто"
}"""

def add_memory(player_name, event_type, detail):
    if player_name not in player_memory:
        player_memory[player_name] = []
    player_memory[player_name].append({"event": event_type, "detail": detail})
    if len(player_memory[player_name]) > MAX_MEMORY:
        player_memory[player_name] = player_memory[player_name][-MAX_MEMORY:]

def get_memory_summary(player_name):
    mem = player_memory.get(player_name, [])
    return mem[-7:] if mem else[]

def build_prompt(data: dict) -> str:
    event_type     = data.get("event", "CHAT")
    player_name    = data.get("player", "System")
    nearby_players = data.get("nearby_players", [])
    nearby_npcs    = data.get("nearby_npcs",[])     # ✨ НОВИНКА
    nearby_objects = data.get("nearby_objects", [])
    inventory      = data.get("inventory",[])
    holding        = data.get("holding", "nothing")
    health         = data.get("health", 100)
    max_health     = data.get("max_health", 100)
    message        = data.get("message", "")
    position       = data.get("position", {})
    visual_info    = data.get("visual_info", "")
    location       = data.get("location", "STREET")
    time_context   = data.get("time_context", "")
    memory         = data.get("memory", get_memory_summary(player_name))
    
    # ✨ ДОБАВЛЕНО: внутреннее состояние
    mood           = data.get("mood", 0.5)
    tiredness      = data.get("tiredness", 0)
    hunger         = data.get("hunger", 0)
    known_locs     = data.get("known_locations", {})

    lines = [
        f"[HP] {health}/{max_health}",
        f"[ЛОКАЦИЯ] {location}",
    ]

    if time_context: lines.append(f"[ВРЕМЯ] {time_context}")
    if visual_info:  lines.append(f"[ЗРЕНИЕ] {visual_info}")

    # Внутреннее состояние (для информации)
    lines.append(f"[СОСТОЯНИЕ] настроение: {mood:.2f}, усталость: {tiredness:.2f}, голод: {hunger:.2f}")

    if nearby_players:
        pl = ", ".join(
            f"{p['name']} ({p.get('distance',0)}м, реп: {p.get('rep',0)})"
            if isinstance(p, dict) else str(p) for p in nearby_players
        )
        lines.append(f"[ИГРОКИ РЯДОМ] {pl}")
    else:
        lines.append("[ИГРОКИ РЯДОМ] никого")

    if nearby_npcs:
        np = ", ".join(
            f"{n['name']} (ID: {n.get('id','?')}, {n.get('distance',0)}м, HP: {n.get('health',100)}, отнош: {n.get('relation',0)})"
            if isinstance(n, dict) else str(n) for n in nearby_npcs
        )
        lines.append(f"[ДРУГИЕ NPC РЯДОМ] {np}")
    else:
        lines.append("[ДРУГИЕ NPC РЯДОМ] никого")

    if nearby_objects:
        obj_str = ", ".join(
            f"{o['name']} ({o.get('distance','?')}м)"
            if isinstance(o, dict) else str(o) for o in nearby_objects[:8]
        )
        lines.append(f"[ОБЪЕКТЫ РЯДОМ] {obj_str}")
    else:
        lines.append("[ОБЪЕКТЫ РЯДОМ] нет")

    lines.append(f"[ИНВЕНТАРЬ] {', '.join(inventory) if inventory else 'пустой'}")
    lines.append(f"[В РУКЕ] {holding}")

    if memory:
        mem_str = " | ".join(f"[{m.get('event','?')}] {m.get('detail','')}" for m in memory)
        lines.append(f"[ПАМЯТЬ СОБЫТИЙ] {mem_str}")

    # Известные локации (кратко)
    if known_locs:
        locs = ", ".join(known_locs.keys())
        lines.append(f"[ИЗВЕСТНЫЕ МЕСТА] {locs}")

    if event_type == "DAMAGE":
        lines.append(f"[СОБЫТИЕ] ТЫ ПОЛУЧИЛ УРОН! HP={health}/{max_health}. Выживай!")
    elif event_type == "TICK":
        lines.append("[СОБЫТИЕ] Свободное время. Сделай действие или поговори с кем-то.")
    elif event_type == "RECEIVED_ITEM":
        item = message.split("предмет: ")[-1] if "предмет: " in message else message
        lines.append(f"[СОБЫТИЕ] Игрок {player_name} передал тебе: «{item}». Отреагируй!")
    else:
        lines.append(f"[СОБЫТИЕ] Обращение/Событие: \"{message}\" (Источник: {player_name})")

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
    print(f"event={event} | источник={player} | msg='{message[:60]}'")

    prompt = build_prompt(data)
    print(f"Промпт:\n{prompt}")

    try:
        raw_text, usage = call_groq(prompt, player)
        print(f"Groq ответ: {raw_text[:300]}")
    except Exception as e:
        full_trace = traceback.format_exc()
        print(f"Groq ошибка:\n{full_trace}")
        if player in chat_history:
            del chat_history[player]
        reason = f"Ошибка Groq API"
        if "rate_limit" in str(e).lower() or "429" in str(e):
            reason = "Ожидание лимитов Groq..."
        return jsonify(_fallback(reason))

    # Очистка от маркдауна на случай если LLaMA проигнорировала response_format
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
            except:
                return jsonify(_fallback("Сбой JSON парсинга"))
        else:
            return jsonify(_fallback("JSON не найден"))

    result.setdefault("thought",     "...")
    result.setdefault("speech",      "")
    result.setdefault("emotion",     "NEUTRAL")
    result.setdefault("action",      "IDLE")
    result.setdefault("hand_action", "IDLE")
    result.setdefault("target",      "")
    result.setdefault("hand_target", "")

    # ✨ ДОБАВЛЕНЫ НОВЫЕ ACTIONS:
    VALID_ACTIONS = {
        "IDLE","WANDER","PICKUP","EQUIP","UNEQUIP","DROP",
        "USE","FOLLOW","RUN_AWAY","SIT","BUILD","DRIVE","GRAB",
        "HELP_NPC", "CHAT_NPC", "FOLLOW_NPC"
    }
    if result["action"] not in VALID_ACTIONS:
        print(f"Неизвестный action '{result['action']}' -> IDLE")
        result["action"] = "IDLE"

    VALID_EMOTIONS = {"NEUTRAL","HAPPY","ANGRY","SURPRISED","PAIN","THINKING","SCARED","CURIOUS"}
    if result["emotion"] not in VALID_EMOTIONS:
        result["emotion"] = "NEUTRAL"

    print(f"Решено: action={result['action']}, эмоция={result['emotion']}, фраза='{result['speech'][:60]}'")
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":      "ok" if client else "no_api_key",
        "version":     "v5.2",
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
            messages=[{"role": "user", "content": "Скажи только одно слово: SUCCESS!"}],
            max_tokens=20
        )
        return jsonify({"status": "OK", "response": r.choices[0].message.content})
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500

@app.route("/reset", methods=["POST"])
def reset():
    chat_history.clear()
    player_memory.clear()
    print("Глобальный сброс памяти произведен!")
    return jsonify({"status": "reset"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n{'='*50}")
    print(f"✨ VRIX сервер v5.2 (NPC Ecosystem | LLaMA 3.3 70B)")
    print(f"  Порт {port} работает!")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port)
