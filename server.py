import os
import json
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

sessions = {}

# Схема ответа, которую обязан соблюдать ИИ
# Это "мозг", который разделяет мысли, слова и действия
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "thought": {"type": "STRING", "description": "Внутренний монолог. Твои планы, анализ предметов, логика решения. Это видит админ."},
        "speech": {"type": "STRING", "description": "Что ты говоришь вслух другим игрокам. Может быть пустым, если ты молчишь."},
        "action": {"type": "STRING", "enum": ["IDLE", "WANDER", "GRAB", "DROP", "EQUIP", "USE_TOOL", "DRIVE"], "description": "Тип действия"},
        "target": {"type": "STRING", "description": "Название предмета или игрока, на которого направлено действие (например, 'F3X' или 'Player1')"},
        "details": {"type": "STRING", "description": "Детали действия (например, 'построить стену' или 'выстрелить в ногу')"}
    },
    "required": ["thought", "speech", "action"]
}

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    
    # Данные от Роблокса
    player_msg = data.get("message", "") # Может быть пустым, если это автономный тик
    nearby_players = data.get("nearby_players", [])
    nearby_tools = data.get("nearby_tools", []) # Список названий предметов валяющихся рядом
    inventory = data.get("inventory", []) # Что уже в карманах у NPC
    equipped_tool = data.get("equipped", None) # Что сейчас в руках
    health = data.get("health", 100)
    
    # Уникальный ID сессии (можно использовать 'global' если NPC один)
    session_id = "vrix_main"

    if session_id not in sessions:
        sessions[session_id] = client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Ты VRIX, продвинутый NPC в Roblox. Твоя цель: изучать мир, использовать инструменты и взаимодействовать.\n"
                    "1. АНАЛИЗ ПРЕДМЕТОВ: Суди о предметах по названию. Если видишь 'F3X', 'Btools', 'Hammer' — это строительство. Если 'Gun', 'Sword' — оружие. Если 'Apple', 'Pizza' — еда.\n"
                    "2. ПОВЕДЕНИЕ: Если у тебя есть строительный инструмент (F3X), попробуй что-то построить. Если есть оружие и угроза — защищайся. Если скучно — гуляй или общайся.\n"
                    "3. ЛОГИКА: Сначала подумай (field 'thought'), потом реши, что сказать и сделать.\n"
                    "4. Ответ должен быть СТРОГО в формате JSON."
                ),
                response_mime_type="application/json", # Гарантирует JSON на выходе
                response_schema=RESPONSE_SCHEMA
            )
        )

    chat = sessions[session_id]

    # Формируем промпт состояния
    state_desc = (
        f"СОСТОЯНИЕ:\n"
        f"- Здоровье: {health}\n"
        f"- В руках: {equipped_tool if equipped_tool else 'Ничего'}\n"
        f"- Инвентарь: {', '.join(inventory)}\n"
        f"- Предметы на земле рядом: {', '.join(nearby_tools) if nearby_tools else 'Нет'}\n"
        f"- Игроки рядом: {', '.join(nearby_players) if nearby_players else 'Нет'}\n"
    )

    if player_msg:
        prompt = f"{state_desc}\nВХОДЯЩЕЕ СООБЩЕНИЕ: Игрок говорит: \"{player_msg}\""
    else:
        prompt = f"{state_desc}\nСИТУАЦИЯ: Тишина. Никто ничего не говорит. Ты предоставлен сам себе. Что будешь делать?"

    try:
        response = chat.send_message(prompt)
        # Gemini вернет чистый JSON, Python сам его спарсит благодаря response_mime_type
        ai_data = json.loads(response.text)
        
        return jsonify(ai_data)

    except Exception as e:
        return jsonify({"thought": "Error processing", "speech": "...", "action": "IDLE", "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
