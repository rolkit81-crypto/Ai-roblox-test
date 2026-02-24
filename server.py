import os
from flask import Flask, request, jsonify
from google import genai

app = Flask(__name__)
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

sessions = {}

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    message = data.get("message", "")
    player = data.get("player", "Player")
    nearby = data.get("nearby_players", [])
    objects = data.get("nearby_objects", [])
    health = data.get("health", 100)

    if player not in sessions:
        sessions[player] = []

    prompt = f"""
Ты живой NPC по имени VRIX в Roblox.
Здоровье: {health}/100
Игроки рядом: {', '.join(nearby) if nearby else 'никого'}
Предметы рядом: {', '.join(objects) if objects else 'ничего'}

Ты живёшь своей жизнью — гуляешь, берёшь предметы, строишь, катаешься на машине.
Реагируй на игроков и на окружение.

Команды:
[GRAB: название] — взять предмет
[DROP] — бросить
[SHOOT] — выстрелить
[WANDER] — погулять
[BUILD] — строить
[DRIVE] — сесть в машину

Отвечай коротко, живо, на русском.

Игрок {player} говорит: {message}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-04-17",
            contents=prompt
        )
        reply = response.text

        actions = []
        if "[SHOOT]" in reply: actions.append({"type": "shoot"})
        if "[DROP]" in reply: actions.append({"type": "drop"})
        if "[WANDER]" in reply: actions.append({"type": "wander"})
        if "[BUILD]" in reply: actions.append({"type": "build"})
        if "[DRIVE]" in reply: actions.append({"type": "drive"})
        if "[GRAB:" in reply:
            item = reply.split("[GRAB:")[1].split("]")[0].strip()
            actions.append({"type": "grab", "target": item})

        clean = reply
        for tag in ["[SHOOT]","[DROP]","[WANDER]","[BUILD]","[DRIVE]"]:
            clean = clean.replace(tag, "")
        if "[GRAB:" in clean:
            clean = clean.split("[GRAB:")[0] + (clean.split("]",1)[1] if "]" in clean else "")
        clean = clean.strip()

        return jsonify({"reply": clean, "actions": actions})

    except Exception as e:
        return jsonify({"reply": "...", "actions": [], "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
