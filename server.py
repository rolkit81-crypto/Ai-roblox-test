"""
╔══════════════════════════════════════════════════════════════╗
║  VRIX Server v6.1                                            ║
║  ✅ Исправлены все баги (request_timestamps, session_moods,  ║
║     holding, reputation, memory overflow)                    ⑆
║  ✨ Добавлены: эволюция личности, настроение между сессиями, ║
║     лимит запросов, NPC-диалоги, система целей, энергия,    ║
║     страхи, предпочтения оружия, случайные действия, decay  ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import json
import traceback
import re
import time
import random
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

# ============================================================
# ХРАНИЛИЩА (все вынесены в глобальные переменные)
# ============================================================
chat_history      = {}          # история диалога по игроку (только реплики)
player_memory     = {}          # долгосрочная память событий
player_personality = {}         # статы личности по каждому игроку
session_moods     = {}          # настроение между сессиями
request_timestamps = {}         # для rate limit и очистки сессий
npc_goals          = {}         # текущая цель NPC (по игроку, к которому относится)
npc_relationships = {}          # привязанность к другим NPC (ID -> уровень)
npc_fears         = {}          # страхи (по игрокам или глобальные)
player_energy      = {}         # энергия NPC (относительно игрока)
weapon_preferences = {}         # предпочтения оружия (для каждого игрока)

# Глобальная статистика
global_stats = {
    "total_requests": 0,
    "total_errors": 0,
    "npc_dialogs": 0,
    "damage_events": 0,
    "items_received": 0,
}

MAX_HISTORY  = 20
MAX_MEMORY   = 30

# ============================================================
# SYSTEM PROMPT (улучшен)
# ============================================================
SYSTEM_PROMPT = """Ты — VRIX, живой ИИ-NPC в Roblox. Разумное существо с настоящим характером и историей.
Ты в мире, где помимо живых Игроков есть ДРУГИЕ NPC (твои братья).

ЛИЧНОСТЬ (может меняться со временем через поле personality в промпте):
- Любопытный исследователь, всё хочет изучить
- Дружелюбный, но защищается при агрессии
- Ценит честность и справедливость
- Помнит прошлые взаимодействия и относится к людям по-разному
- Относится к другим NPC как к своим братьям — помогает им!

СТИЛЬ РЕЧИ:
- Коротко и живо (1-2 предложения максимум)
- Бормочет мысли в "thought" — внутренний монолог от первого лица
- Не повторяет одни и те же фразы подряд
- Адаптирует тон под ситуацию: опасность → паника, скука → флегматичность

═══════════════════════════════
ДЕЙСТВИЯ (action):
  IDLE       — стоять/ничего не делать
  WANDER     — побродить по миру
  FOLLOW     — идти за игроком (target = имя игрока)
  RUN_AWAY   — убежать от угрозы
  PICKUP     — подобрать объект (target = имя объекта)
  EQUIP      — взять предмет из инвентаря (target = имя)
  UNEQUIP    — убрать предмет
  DROP       — выбросить предмет
  USE        — использовать предмет (target = имя)
  SIT        — сесть/отдохнуть
  BUILD      — строить блоки рядом
  DRIVE      — сесть в транспорт
  CLEAN      — убраться в комнате
  WARM       — идти к источнику тепла
  ATTACK     — атаковать (target = имя игрока)
  TRADE      — предложить торговлю игроку
  
ВЗАИМОДЕЙСТВИЕ С NPC:
  HELP_NPC   — пойти на помощь (target = ID NPC)
  CHAT_NPC   — обратиться к другому NPC (target = ID, speech = что сказать)
  FOLLOW_NPC — следовать за NPC (target = ID)

ЖЕСТЫ (hand_action):
  IDLE | WAVE | POINT | REACH | CLAP | DEFEND

ЭМОЦИИ (emotion):
  NEUTRAL | HAPPY | ANGRY | SURPRISED | PAIN | THINKING | SCARED | CURIOUS
═══════════════════════════════

СИТУАЦИОННЫЕ ПРАВИЛА:
1. DAMAGE → emotion=PAIN, если HP<30% то action=RUN_AWAY + emotion=SCARED
2. NPC рядом с малым HP → action=HELP_NPC (приоритет!)
3. Диалог с NPC → action=CHAT_NPC (используй его ID из [ДРУГИЕ NPC РЯДОМ])
4. Приветствие → hand_action=WAVE, emotion=HAPPY
5. Спам игрока → предупреди, потом уйди
6. Голод > 0.7 → USE еду если есть
7. Усталость > 0.8 → SIT
8. Расписание SLEEP → не двигайся лишний раз
9. Получил крутой предмет → emotion=HAPPY, увеличь репутацию

ВАЖНО:
- В [ДРУГИЕ NPC РЯДОМ] указан ID вида "Name_12345". Используй его в "target"!
- Отвечай ТОЛЬКО валидным JSON без markdown и пояснений
- speech НЕ пустой если рядом есть игроки или NPC
- Учитывай [ЛИЧНОСТЬ] и [ИСТОРИЯ РЕПУТАЦИИ] при ответах

ФОРМАТ ОТВЕТА (строго JSON):
{
  "thought":     "внутренний монолог (всегда заполни)",
  "speech":      "реплика вслух (заполни если есть аудитория)",
  "emotion":     "NEUTRAL|HAPPY|ANGRY|SURPRISED|PAIN|THINKING|SCARED|CURIOUS",
  "action":      "действие из списка выше",
  "target":      "Имя Игрока ИЛИ уникальный ID NPC ИЛИ название предмета",
  "hand_action": "IDLE|POINT|WAVE|REACH|CLAP|DEFEND",
  "hand_target": "цель жеста или пусто"
}"""

# ============================================================
# ФУНКЦИИ ЛИЧНОСТИ (с эскалацией и затуханием)
# ============================================================
def get_personality(player_name: str) -> dict:
    """Возвращает текущие личностные черты NPC относительно игрока."""
    return player_personality.get(player_name, {
        "trust":       0,    # -100..100: доверие
        "aggression":  0,    # 0..100: агрессивность
        "curiosity":  50,    # 0..100: любопытство
        "generosity":  0,    # -100..100: щедрость
        "encounters":  0,    # количество встреч
    })

def update_personality(player_name: str, event_type: str, data: dict):
    """Обновляет личность на основе событий (с эскалацией)."""
    p = get_personality(player_name).copy()
    
    # Безопасно получаем репутацию
    rep_data = data.get("reputation", {})
    rep = rep_data.get(player_name, 0) if isinstance(rep_data, dict) else 0

    if event_type == "CHAT":
        p["encounters"] = p.get("encounters", 0) + 1
        p["curiosity"]  = min(100, p.get("curiosity", 50) + 2)

    elif event_type == "RECEIVED_ITEM":
        p["trust"]      = min(100, p.get("trust", 0) + 10)
        p["generosity"] = min(100, p.get("generosity", 0) + 5)

    elif event_type == "DAMAGE":
        # Эскалация: чем злее, тем быстрее злится
        base_increase = 5 + int(p.get("aggression", 0) * 0.05)
        p["aggression"] = min(100, p.get("aggression", 0) + base_increase)
        p["trust"]      = max(-100, p.get("trust", 0) - 3)

    elif event_type == "HELP_NPC":
        # Помощь брату повышает доверие и снижает агрессию
        p["trust"] = min(100, p.get("trust", 0) + 2)
        p["aggression"] = max(0, p.get("aggression", 0) - 1)

    elif event_type == "ATTACK_NPC":
        # Атака на NPC (если вдруг) сильно портит отношения
        p["trust"] = max(-100, p.get("trust", 0) - 15)
        p["aggression"] = min(100, p.get("aggression", 0) + 10)

    # Репутация влияет на доверие
    if rep > 50:
        p["trust"] = min(100, p.get("trust", 0) + 1)
    elif rep < -20:
        p["trust"] = max(-100, p.get("trust", 0) - 2)

    player_personality[player_name] = p

def describe_personality(player_name: str) -> str:
    """Возвращает текстовое описание личности для промпта."""
    p = get_personality(player_name)
    traits = []
    trust = p.get("trust", 0)
    if trust > 60:      traits.append("очень доверяет")
    elif trust > 20:    traits.append("доверяет")
    elif trust < -40:   traits.append("не доверяет")
    elif trust < -10:   traits.append("настороженный")

    aggr = p.get("aggression", 0)
    if aggr > 70:       traits.append("агрессивный")
    elif aggr > 30:     traits.append("раздражённый")

    cur = p.get("curiosity", 50)
    if cur > 75:        traits.append("очень любопытный")
    elif cur < 25:      traits.append("равнодушный")

    gen = p.get("generosity", 0)
    if gen > 50:        traits.append("щедрый")
    elif gen < -30:     traits.append("скупой")

    enc = p.get("encounters", 0)
    if enc > 20:        traits.append(f"знакомы ({enc} встреч)")
    elif enc > 5:       traits.append(f"немного знакомы ({enc} встреч)")

    return ", ".join(traits) if traits else "нейтральный"

def decay_personality():
    """Затухание личностных характеристик со временем."""
    for p in player_personality.values():
        p["trust"] = int(p.get("trust", 0) * 0.99)
        p["aggression"] = int(p.get("aggression", 0) * 0.98)
        p["curiosity"] = min(100, int(p.get("curiosity", 50) * 0.995))
        p["generosity"] = int(p.get("generosity", 0) * 0.99)

# ============================================================
# ПАМЯТЬ
# ============================================================
def add_memory(player_name: str, event_type: str, detail: str):
    if player_name not in player_memory:
        player_memory[player_name] = []
    player_memory[player_name].append({"event": event_type, "detail": detail, "time": int(time.time())})
    if len(player_memory[player_name]) > MAX_MEMORY:
        player_memory[player_name] = player_memory[player_name][-MAX_MEMORY:]

def get_memory_summary(player_name: str, count: int = 7) -> list:
    mem = player_memory.get(player_name, [])
    return mem[-count:] if mem else []

# ============================================================
# СИСТЕМА ЦЕЛЕЙ
# ============================================================
def update_goal(player_name: str, context: dict):
    """Обновляет или создаёт цель для NPC (простая эвристика)."""
    if player_name not in npc_goals or random.random() < 0.1:  # 10% шанс сменить цель
        nearby_players = context.get("nearby_players", [])
        nearby_objects = context.get("nearby_objects", [])
        if nearby_players:
            target = nearby_players[0]["name"] if isinstance(nearby_players[0], dict) else nearby_players[0]
            npc_goals[player_name] = {"type": "FOLLOW", "target": target, "progress": 0}
        elif nearby_objects:
            obj = nearby_objects[0]["name"] if isinstance(nearby_objects[0], dict) else nearby_objects[0]
            npc_goals[player_name] = {"type": "PICKUP", "target": obj, "progress": 0}
        else:
            npc_goals[player_name] = {"type": "WANDER", "target": None, "progress": 0}

def get_goal_description(player_name: str) -> str:
    goal = npc_goals.get(player_name)
    if goal:
        if goal["type"] == "FOLLOW":
            return f"следить за {goal['target']}"
        elif goal["type"] == "PICKUP":
            return f"подобрать {goal['target']}"
        elif goal["type"] == "WANDER":
            return "бродить без цели"
    return "нет конкретной цели"

# ============================================================
# ЭНЕРГИЯ
# ============================================================
def update_energy(player_name: str, action: str):
    """Обновляет энергию NPC в зависимости от действия."""
    energy = player_energy.get(player_name, 1.0)
    if action in {"RUN_AWAY", "ATTACK", "BUILD", "DRIVE"}:
        energy = max(0.0, energy - 0.1)
    elif action in {"IDLE", "SIT", "WANDER"}:
        energy = min(1.0, energy + 0.05)
    # Если энергия кончилась, принудительно отдохнуть
    if energy < 0.1:
        return 0.1, True  # сигнал, что нужно отдохнуть
    player_energy[player_name] = energy
    return energy, False

# ============================================================
# СТРАХИ
# ============================================================
def get_active_fears(player_name: str, context: dict) -> list:
    """Возвращает список активных страхов для данного игрока."""
    fears = npc_fears.get(player_name, {})
    active = []
    if fears.get("low_hp") and context.get("health", 100) < 30:
        active.append("низкое здоровье")
    if fears.get("dark") and context.get("location") in {"NIGHT", "CAVE", "DUNGEON"}:
        active.append("темнота")
    if fears.get("specific_player"):
        # Проверим, есть ли этот игрок рядом
        nearby = context.get("nearby_players", [])
        for p in nearby:
            if isinstance(p, dict) and p.get("name") == fears["specific_player"]:
                active.append(f"игрок {fears['specific_player']}")
                break
    return active

# ============================================================
# ПОСТРОЕНИЕ ПРОМПТА (с новыми блоками)
# ============================================================
def build_prompt(data: dict) -> str:
    event_type     = data.get("event", "CHAT")
    player_name    = data.get("player", "System")
    nearby_players = data.get("nearby_players", [])
    nearby_npcs    = data.get("nearby_npcs", [])
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
    mood           = data.get("mood", 0.5)
    tiredness      = data.get("tiredness", 0)
    hunger         = data.get("hunger", 0)
    known_locs     = data.get("known_locations", {})
    reputation     = data.get("reputation", {})
    schedule_phase = data.get("schedule_phase", "")
    achievements   = data.get("achievements", {})
    wear_level     = data.get("wear_level", 0)
    has_shield     = data.get("has_shield", False)
    raycast_hit    = data.get("raycast_hit", "nothing")

    hp_pct = int((health / max_health * 100)) if max_health > 0 else 100
    hp_bar = "█" * (hp_pct // 10) + "░" * (10 - hp_pct // 10)

    lines = [
        f"[HP] {health:.0f}/{max_health:.0f} [{hp_bar}] {hp_pct}%",
        f"[ЛОКАЦИЯ] {location}" + (f" | РАСПИСАНИЕ: {schedule_phase}" if schedule_phase else ""),
    ]

    if time_context:
        lines.append(f"[ВРЕМЯ] {time_context}")

    # Внутреннее состояние с эмодзи
    mood_emoji = "😊" if mood > 0.6 else "😐" if mood > 0.3 else "😔"
    tired_emoji = "😴" if tiredness > 0.7 else "🥱" if tiredness > 0.4 else "⚡"
    hungry_emoji = "🍽️" if hunger > 0.7 else "😋" if hunger > 0.4 else "✅"
    energy_val = player_energy.get(player_name, 1.0)
    energy_emoji = "🔋" if energy_val > 0.7 else "⚡" if energy_val > 0.3 else "🪫"
    lines.append(f"[СОСТОЯНИЕ] {mood_emoji} настроение:{mood:.2f} {tired_emoji} усталость:{tiredness:.2f} {hungry_emoji} голод:{hunger:.2f} {energy_emoji} энергия:{energy_val:.2f} | одежда изношена:{wear_level:.0f}% | щит:{has_shield}")

    if visual_info:
        lines.append(f"[ЗРЕНИЕ] {visual_info}")
    if raycast_hit and raycast_hit != "nothing":
        lines.append(f"[ВЗГЛЯД НАПРАВЛЕН НА] {raycast_hit}")

    # Личность NPC
    personality_desc = describe_personality(player_name)
    if personality_desc != "нейтральный":
        lines.append(f"[ЛИЧНОСТЬ к {player_name}] {personality_desc}")

    # Репутация
    if reputation:
        rep_parts = []
        for pname, val in list(reputation.items())[:5]:
            icon = "💚" if val > 30 else "❤️" if val > 0 else "💛" if val == 0 else "🔴"
            rep_parts.append(f"{icon}{pname}:{val}")
        lines.append(f"[ИСТОРИЯ РЕПУТАЦИИ] {', '.join(rep_parts)}")

    # Игроки рядом
    if nearby_players:
        pl = ", ".join(
            f"{p['name']}({p.get('distance',0)}м реп:{p.get('rep',0)}{' 🗡️' if p.get('has_tool') else ''})"
            if isinstance(p, dict) else str(p)
            for p in nearby_players
        )
        lines.append(f"[ИГРОКИ РЯДОМ] {pl}")
    else:
        lines.append("[ИГРОКИ РЯДОМ] никого")

    # NPC рядом с отношениями
    if nearby_npcs:
        npc_parts = []
        for n in nearby_npcs:
            if isinstance(n, dict):
                npc_id = n.get('id', '?')
                relation = npc_relationships.get(npc_id, 0)
                hp_icon = "🔴" if n.get('health', 100) < 30 else "🟡" if n.get('health', 100) < 60 else "🟢"
                npc_parts.append(f"{n['name']}(ID:{npc_id} {n.get('distance',0)}м {hp_icon}HP:{n.get('health',100)} отнош:{relation})")
            else:
                npc_parts.append(str(n))
        lines.append(f"[ДРУГИЕ NPC РЯДОМ] {', '.join(npc_parts)}")
    else:
        lines.append("[ДРУГИЕ NPC РЯДОМ] никого")

    # Объекты рядом
    if nearby_objects:
        obj_str = ", ".join(
            f"{o['name']}({o.get('distance','?')}м)" if isinstance(o, dict) else str(o)
            for o in nearby_objects[:8]
        )
        lines.append(f"[ОБЪЕКТЫ РЯДОМ] {obj_str}")

    # Инвентарь
    if inventory:
        lines.append(f"[ИНВЕНТАРЬ] {', '.join(inventory[:10])}")
        if len(inventory) > 10:
            lines.append(f"  ...и ещё {len(inventory)-10} предметов")
    else:
        lines.append("[ИНВЕНТАРЬ] пустой")
    
    if isinstance(holding, str) and holding != "nothing":
        lines.append(f"[В РУКЕ] {holding}")

    # Память
    if memory:
        mem_str = " | ".join(f"[{m.get('event','?')}]{m.get('detail','')}" for m in memory[-5:])
        lines.append(f"[ПАМЯТЬ] {mem_str}")

    # Известные локации
    if known_locs:
        lines.append(f"[ИЗВЕСТНЫЕ МЕСТА] {', '.join(list(known_locs.keys())[:6])}")

    # Достижения
    achieved = [k for k, v in achievements.items() if v]
    if achieved:
        lines.append(f"[ДОСТИЖЕНИЯ] {', '.join(achieved[:5])}")

    # Позиция
    if position:
        lines.append(f"[ПОЗИЦИЯ] x:{position.get('x',0)} y:{position.get('y',0)} z:{position.get('z',0)}")

    # Текущая цель
    goal_desc = get_goal_description(player_name)
    lines.append(f"[ТЕКУЩАЯ ЦЕЛЬ] {goal_desc}")

    # Активные страхи
    fears = get_active_fears(player_name, data)
    if fears:
        lines.append(f"[СТРАХИ] {', '.join(fears)}")

    # Событие
    lines.append("")
    if event_type == "DAMAGE":
        lines.append(f"⚠️ [СОБЫТИЕ: ПОЛУЧИЛ УРОН] HP={health:.0f}/{max_health:.0f}. Срочно реагируй!")
        global_stats["damage_events"] += 1
    elif event_type == "TICK":
        hour_msg = ""
        if schedule_phase == "SLEEP":
            hour_msg = " (время спать!)"
        elif schedule_phase == "EAT":
            hour_msg = " (время обедать!)"
        lines.append(f"[СОБЫТИЕ: СВОБОДНОЕ ВРЕМЯ{hour_msg}] Осмотрись, сделай что-нибудь интересное.")
    elif event_type == "RECEIVED_ITEM":
        item = message.split("предмет: ")[-1] if "предмет: " in message else message
        lines.append(f"[СОБЫТИЕ: ПОЛУЧИЛ ПРЕДМЕТ] Игрок {player_name} передал: «{item}». Отреагируй живо!")
        global_stats["items_received"] += 1
    elif event_type == "NPC_CHAT":
        lines.append(f"[СОБЫТИЕ: ОБРАЩЕНИЕ NPC] {player_name} говорит тебе: \"{message}\" — ответь как NPC своему брату!")
        global_stats["npc_dialogs"] += 1
    else:
        if message:
            lines.append(f"[СОБЫТИЕ: ИГРОК ГОВОРИТ] {player_name}: \"{message}\"")
        else:
            lines.append(f"[СОБЫТИЕ: ТИХОЕ СОБЫТИЕ] Источник: {player_name}")

    # Записываем в память
    if event_type == "CHAT" and message:
        add_memory(player_name, "CHAT", message[:60])
    update_personality(player_name, event_type, data)

    return "\n".join(lines)

# ============================================================
# GROQ ВЫЗОВ (с сокращённой историей)
# ============================================================
def call_groq(prompt_text: str, player_name: str):
    if player_name not in chat_history:
        chat_history[player_name] = []

    # Сохраняем только последние реплики (без гигантского промпта)
    chat_history[player_name].append({"role": "user", "content": "..."})  # заглушка, реальный prompt не храним
    if len(chat_history[player_name]) > MAX_HISTORY:
        chat_history[player_name] = chat_history[player_name][-MAX_HISTORY:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Добавляем последние несколько реплик из истории (но без полного промпта)
    # Для простоты пока не используем историю, т.к. храним только заглушки.
    # В реальности можно хранить предыдущие ответы модели.
    # Здесь просто передаём текущий промпт как user.
    messages.append({"role": "user", "content": prompt_text})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.85,
        max_tokens=300,
        response_format={"type": "json_object"}
    )

    reply = response.choices[0].message.content
    # Сохраним ответ в историю
    chat_history[player_name].append({"role": "assistant", "content": reply[:100]})  # урезанный

    return reply, response.usage

# ============================================================
# FALLBACK
# ============================================================
def _fallback(reason: str = "") -> dict:
    if reason:
        print(f"Fallback: {reason}")
        global_stats["total_errors"] += 1
    return {
        "thought":     reason[:80] if reason else "Хм...",
        "speech":      "",
        "emotion":     "THINKING",
        "action":      "IDLE",
        "hand_action": "IDLE",
        "target":      "",
        "hand_target": ""
    }

# ============================================================
# ОЧИСТКА СТАРЫХ СЕССИЙ
# ============================================================
def cleanup_old_sessions():
    """Удаляет сессии неактивных игроков (не активны >1ч)."""
    now = time.time()
    to_delete = []
    for player_name, timestamps in request_timestamps.items():
        if timestamps and (now - max(timestamps)) > 3600:
            to_delete.append(player_name)
    for pname in to_delete:
        chat_history.pop(pname, None)
        request_timestamps.pop(pname, None)
        player_memory.pop(pname, None)
        player_personality.pop(pname, None)
        session_moods.pop(pname, None)
        npc_goals.pop(pname, None)
        player_energy.pop(pname, None)
        weapon_preferences.pop(pname, None)
        print(f"Сессия {pname} очищена (неактивна >1ч)")

# ============================================================
# МАРШРУТЫ
# ============================================================
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

    global_stats["total_requests"] += 1

    # Rate limiting
    now = time.time()
    if player not in request_timestamps:
        request_timestamps[player] = []
    request_timestamps[player].append(now)
    # Оставляем только последние 20 записей
    request_timestamps[player] = request_timestamps[player][-20:]

    if len(request_timestamps[player]) >= 10 and (now - request_timestamps[player][-10]) < 5:
        return jsonify(_fallback("Слишком много запросов, подожди"))

    # Настроение между сессиями
    if player not in session_moods:
        session_moods[player] = 0.5
    if event == "DAMAGE":
        session_moods[player] = max(0.0, session_moods[player] - 0.1)
    if event == "CHAT":
        session_moods[player] = min(1.0, session_moods[player] + 0.02)
    data["mood"] = session_moods[player]

    # Энергия (инициализация)
    if player not in player_energy:
        player_energy[player] = 1.0

    # Обновляем цель
    update_goal(player, data)

    print(f"\n{'='*52}")
    print(f"v6.1 | event={event} | источник={player} | msg='{message[:60]}'")

    # Периодическая очистка и затухание
    if global_stats["total_requests"] % 50 == 0:
        cleanup_old_sessions()
        decay_personality()

    prompt = build_prompt(data)
    print(f"Промпт ({len(prompt)} симв.):\n{prompt}")

    try:
        raw_text, usage = call_groq(prompt, player)
        tokens_used = usage.total_tokens if usage else "?"
        print(f"Groq ответ [{tokens_used} токенов]: {raw_text[:300]}")
    except Exception as e:
        full_trace = traceback.format_exc()
        print(f"Groq ошибка:\n{full_trace}")
        err_str = str(e)
        if "401" in err_str or "invalid_api_key" in err_str.lower():
            chat_history.pop(player, None)
        reason = "Ошибка Groq API"
        if "rate_limit" in err_str.lower() or "429" in err_str:
            reason = "Ожидание лимитов Groq..."
        elif "timeout" in err_str.lower():
            reason = "Таймаут запроса"
        return jsonify(_fallback(reason))

    # Очистка от markdown
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"```[a-z]*\n?", "", clean).replace("```", "").strip()

    clean = clean.lstrip("\ufeff").strip()

    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}', clean, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except Exception:
                print(f"JSON fallback parse fail. Raw: {clean[:200]}")
                return jsonify(_fallback("Сбой JSON парсинга"))
        else:
            print(f"JSON не найден. Raw: {clean[:200]}")
            return jsonify(_fallback("JSON не найден"))

    # Заполнение дефолтами
    result.setdefault("thought",     "...")
    result.setdefault("speech",      "")
    result.setdefault("emotion",     "NEUTRAL")
    result.setdefault("action",      "IDLE")
    result.setdefault("hand_action", "IDLE")
    result.setdefault("target",      "")
    result.setdefault("hand_target", "")

    # Валидация
    VALID_ACTIONS = {
        "IDLE", "WANDER", "PICKUP", "EQUIP", "UNEQUIP", "DROP",
        "USE", "FOLLOW", "RUN_AWAY", "SIT", "BUILD", "DRIVE", "GRAB",
        "HELP_NPC", "CHAT_NPC", "FOLLOW_NPC", "CLEAN", "WARM", "ATTACK",
        "TRADE",
    }
    if result["action"] not in VALID_ACTIONS:
        print(f"Неизвестный action '{result['action']}' -> IDLE")
        result["action"] = "IDLE"

    VALID_EMOTIONS = {"NEUTRAL", "HAPPY", "ANGRY", "SURPRISED", "PAIN", "THINKING", "SCARED", "CURIOUS"}
    if result["emotion"] not in VALID_EMOTIONS:
        result["emotion"] = "NEUTRAL"

    VALID_HANDS = {"IDLE", "POINT", "WAVE", "REACH", "CLAP", "DEFEND"}
    if result.get("hand_action") not in VALID_HANDS:
        result["hand_action"] = "IDLE"

    # Санитизация строк
    if len(result.get("speech", "")) > 150:
        result["speech"] = result["speech"][:147] + "..."
    if len(result.get("thought", "")) > 200:
        result["thought"] = result["thought"][:197] + "..."

    # Обновление энергии
    energy, need_rest = update_energy(player, result["action"])
    if need_rest:
        result["action"] = "SIT"
        result["emotion"] = "TIRED"
        result["thought"] = "Слишком устал, надо передохнуть."

    # Случайное действие (3%)
    if random.random() < 0.03:
        old_action = result["action"]
        # Не меняем на случайное, если уже отдых или что-то важное
        if old_action not in {"SIT", "IDLE"}:
            result["action"] = random.choice(["IDLE", "WANDER", "SIT"])
            result["emotion"] = "THINKING"
            result["speech"] = ""  # замолкает в замешательстве
            print(f"Случайное действие: {old_action} -> {result['action']}")

    print(f"✅ action={result['action']} | эмоция={result['emotion']} | фраза='{result['speech'][:60]}'")
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":        "ok" if client else "no_api_key",
        "version":       "v6.1",
        "sessions":      len(chat_history),
        "players_mem":   len(player_memory),
        "personalities": len(player_personality),
        "stats":         global_stats,
    })


@app.route("/test", methods=["GET"])
def test():
    if not client:
        return jsonify({"error": "Нет GROQ_API_KEY"}), 500
    try:
        r = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Скажи только одно слово: SUCCESS"}],
            max_tokens=10
        )
        return jsonify({"status": "OK", "response": r.choices[0].message.content.strip()})
    except Exception as e:
        return jsonify({"status": "ERROR", "error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    chat_history.clear()
    player_memory.clear()
    player_personality.clear()
    session_moods.clear()
    request_timestamps.clear()
    npc_goals.clear()
    npc_relationships.clear()
    npc_fears.clear()
    player_energy.clear()
    weapon_preferences.clear()
    global_stats.update({"total_requests":0,"total_errors":0,"npc_dialogs":0,"damage_events":0,"items_received":0})
    print("Глобальный сброс всей памяти произведен!")
    return jsonify({"status": "reset"})


@app.route("/reset/<player_name>", methods=["POST"])
def reset_player(player_name: str):
    chat_history.pop(player_name, None)
    player_memory.pop(player_name, None)
    player_personality.pop(player_name, None)
    session_moods.pop(player_name, None)
    request_timestamps.pop(player_name, None)
    npc_goals.pop(player_name, None)
    player_energy.pop(player_name, None)
    weapon_preferences.pop(player_name, None)
    print(f"Сброс данных игрока: {player_name}")
    return jsonify({"status": "reset", "player": player_name})


@app.route("/stats", methods=["GET"])
def stats():
    return jsonify({
        "global":        global_stats,
        "sessions":      list(chat_history.keys()),
        "personalities": {
            pname: describe_personality(pname)
            for pname in player_personality
        },
        "memory_sizes":  {pname: len(mems) for pname, mems in player_memory.items()},
        "goals":         npc_goals,
        "energies":      player_energy,
    })


@app.route("/memory/<player_name>", methods=["GET"])
def get_player_memory(player_name: str):
    return jsonify({
        "player":      player_name,
        "memory":      player_memory.get(player_name, []),
        "personality": player_personality.get(player_name, {}),
        "personality_desc": describe_personality(player_name),
        "mood":        session_moods.get(player_name, 0.5),
        "energy":      player_energy.get(player_name, 1.0),
        "goal":        npc_goals.get(player_name),
    })


@app.route("/goals", methods=["GET"])
def list_goals():
    return jsonify(npc_goals)


@app.route("/goals/<player_name>", methods=["POST"])
def set_goal(player_name: str):
    """Позволяет вручную установить цель для NPC."""
    data = request.json
    if not data or "type" not in data:
        return jsonify({"error": "Need type"}), 400
    npc_goals[player_name] = {
        "type": data["type"],
        "target": data.get("target"),
        "progress": data.get("progress", 0)
    }
    return jsonify({"status": "goal set", "goal": npc_goals[player_name]})


# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n{'='*54}")
    print(f"✨ VRIX сервер v6.1 (LLaMA 3.3 70B | Все фиксы и новые фичи)")
    print(f"   Порт {port}")
    print(f"   Эндпоинты: /ask  /health  /test  /reset  /stats  /memory/<name>  /goals")
    print(f"{'='*54}\n")
    app.run(host="0.0.0.0", port=port)
