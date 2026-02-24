import os
import json
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
sessions = {}

# Новая схема ответа для полного контроля тела
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thought": {"type": "STRING"},
        "speech": {"type": "STRING"},
        "emotion": {"type": "STRING", "enum": ["NEUTRAL", "HAPPY", "ANGRY", "SURPRISED", "PAIN", "THINKING"]},
        "action": {"type": "STRING", "enum": ["IDLE", "WANDER", "GRAB", "USE_TOOL", "FOLLOW", "RUN_AWAY"]},
        "target": {"type": "STRING", "description": "Имя цели для движения или захвата"},
        "hand_action": {
            "type": "STRING", 
            "enum": ["IDLE", "POINT", "WAVE", "REACH", "CLAP", "DEFEND"],
            "description": "Что делают руки?"
        },
        "hand_target": {"type": "STRING", "description": "На что нацелить руку (имя объекта или игрока)"}
    },
    "required": ["thought", "speech", "emotion", "action", "hand_action"]
}

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    player_name = data.get("player", "System")
    event_type = data.get("event", "CHAT") # CHAT, DAMAGE, or TICK
    nearby_tools = data.get("nearby_tools", [])
    health = data.get("health", 100)
    equipped = data.get("equipped", None)

    if "vrix_mega" not in sessions:
        sessions["vrix_mega"] = client.chats.create(
            model="gemini-2.0-flash",
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Ты — VRIX, совершенный ИИ-манекен в Roblox. Твое тело полностью под твоим контролем.\n"
                    "1. ЭМОЦИИ: Если тебя ударили (health упал), выбирай эмоцию 'PAIN' и действие 'RUN_AWAY' или 'DEFEND'.\n"
                    "2. ЖЕСТЫ (hand_action):\n"
                    "   - POINT: Укажи пальцем на игрока или предмет.\n"
                    "   - WAVE: Помахай рукой в знак приветствия.\n"
                    "   - REACH: Протяни руку к предмету, который хочешь изучить.\n"
                    "   - DEFEND: Закройся руками, если страшно.\n"
                    "3. ЛОГИКА: Ты должен вести себя как живой исследователь. Если видишь новый тул — иди к нему, возьми, изучи.\n"
                    "Отвечай только в JSON."
                ),
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA
            )
        )

    # Специальный промпт если получен урон
    if event_type == "DAMAGE":
        prompt = f"ВНИМАНИЕ! Ты получил урон! Здоровье: {health}/100. Кто-то напал на тебя? Твои мысли и реакция?"
    else:
        prompt = f"Состояние: HP={health}, Инвентарь={equipped}. Рядом: {nearby_tools}. Игрок {player_name} говорит: {data.get('message')}"

    try:
        response = sessions["vrix_mega"].send_message(prompt)
        return jsonify(json.loads(response.text))
    except Exception as e:
        return jsonify({"thought": "Error", "speech": "...", "emotion": "NEUTRAL", "action": "IDLE", "hand_action": "IDLE"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
