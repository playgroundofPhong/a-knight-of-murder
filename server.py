import os
import sys
import json
import time
import socket
import asyncio
import logging
import re
from typing import Dict, List, Any, Optional
from contextlib import asynccontextmanager

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel

from game_state import GameStateManager
from ai_engine import AIEngine, build_character_system_prompt, build_host_system_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("server")

def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

game_state = GameStateManager()
ai_engine = AIEngine(api_key=game_state.api_key, provider=game_state.llm_provider)

# Active WebSocket connections: player_id -> WebSocket
connected_websockets: Dict[str, WebSocket] = {}

CHARACTER_ALIASES = {
    "queen_genevieve": ["queen genevieve", "genevieve", "queen", "nữ hoàng", "nu hoang", "hoàng hậu", "hoang hau"],
    "lord_taylor": ["lord taylor", "taylor", "lord", "lãnh chúa", "lanh chua"],
    "lady_gwendolyn": ["lady gwendolyn", "gwendolyn", "quý bà", "quy ba", "phu nhân", "phu nhan"],
    "baron_bartholomew": ["baron bartholomew", "bartholomew", "baron", "nam tước", "nam tuoc", "chú rể", "chu re"],
    "lady_diana": ["lady diana of dunnsberry", "lady diana", "diana", "tiểu thư diana", "tieu thu", "cô dâu", "co dau"],
    "maid_marilyn": ["maid marilyn", "marilyn", "hầu gái marilyn", "thị nữ marilyn"],
    "sir_cameron": ["sir cameron", "cameron", "hiệp sĩ cameron", "hiep si cameron"],
    "sir_rufus": ["sir rufus", "rufus", "hiệp sĩ rufus", "hiep si rufus"],
    "willie_watchman": ["willie the watchman", "willie", "watchman", "bailiff", "thị vệ", "thi ve", "cai ngục", "cai nguc"],
    "charlamagne": ["charlamagne", "chambermaid", "hầu phòng", "hau phong"],
    "maid_monica": ["maid monica", "monica", "thị nữ monica", "thi nu monica"],
    "joking_jerry": ["joking jerry", "jerry", "jester", "fool", "chàng hề", "chang he", "hề"],
}

def find_mentioned_character(content: str, active_chars: List[Dict[str, Any]], sender_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    content_lower = content.lower()

    def is_targetable_ai(c: Dict[str, Any]) -> bool:
        if sender_id and c["id"] == sender_id:
            return False
        if not c.get("is_human"):
            return True
        # If marked as human, but player is disconnected or offline, AI will reply as proxy!
        pid = c.get("player_id")
        return not pid or (pid not in connected_websockets)

    # Step 1: Explicit @ mentions (e.g. @baron, @diana, @Lady Diana, @cameron)
    for c in active_chars:
        if not is_targetable_ai(c):
            continue
        cid = c["id"]
        if f"@{cid}" in content_lower:
            return c
        aliases = sorted(CHARACTER_ALIASES.get(cid, [cid, c["name"].lower()]), key=len, reverse=True)
        for a in aliases:
            if re.search(r'@' + re.escape(a) + r'\b', content_lower) or f"@{a}" in content_lower:
                return c

    # Step 2: Directly addressed character name (e.g. "Diana, what say you?")
    for c in active_chars:
        if not is_targetable_ai(c):
            continue
        cid = c["id"]
        aliases = sorted(CHARACTER_ALIASES.get(cid, [cid, c["name"].lower()]), key=len, reverse=True)
        for a in aliases:
            if a in ["lord", "queen", "baron", "he", "hề"]:
                continue # Do not match generic honorifics without @
            if re.search(r'\b' + re.escape(a) + r'\b', content_lower):
                return c

    return None

async def broadcast(message_data: Dict[str, Any]):
    """Broadcast JSON message to all connected clients."""
    payload = json.dumps(message_data)
    disconnected = []
    for pid, ws in connected_websockets.items():
        try:
            await ws.send_text(payload)
        except Exception:
            disconnected.append(pid)
    for pid in disconnected:
        connected_websockets.pop(pid, None)

DRAMA_TOPICS_STAGE_1_2 = [
    {
        "pair": ("sir_cameron", "sir_rufus"),
        "provocation": "Sir Rufus! Thy hollow victory in the joust stinks of foul deceit! My stallion was taken ill moments before the tilt, yet thou hast the gall to demand Maid Marilyn's silk favor!",
        "response_prompt": "Sir Cameron is publicly confronting you in the Great Hall, accusing you of poisoning his horse and demanding Maid Marilyn's favor. Defend your triumph with arrogant knightly pride, citing Clause 5b of the Laws of the Land."
    },
    {
        "pair": ("maid_monica", "baron_bartholomew"),
        "provocation": "Baron Bartholomew! Dost thou believe that golden rings and wedding silks can erase the solemn vows we whispered beneath the castle willows? Thy lavish banquet is but a gilded betrayal!",
        "response_prompt": "Maid Monica is publicly confronting you in the Great Hall about your past romance and your arranged marriage to Lady Diana. Urge discretion with torn nobility, reminding her that your father's decree is law in Fernwood."
    },
    {
        "pair": ("lady_gwendolyn", "charlamagne"),
        "provocation": "Charlamagne, girl! By what right wert thou seen creeping forth from Sir Cameron's private chambers before dawn? Speak plain truth before this noble gathering!",
        "response_prompt": "Lady Gwendolyn is demanding to know why you were seen leaving Sir Cameron's room. Deflect humbly while hinting slyly that a chambermaid overhears many grave secrets in the quiet hallways."
    },
    {
        "pair": ("maid_monica", "lady_diana"),
        "provocation": "Behold the grand bride from Dunnsberry! Tell us, 'Lady' Diana, since when doth a true noblewoman boast of cooking coarse peasant stews and sewing her own wedding attire with needle and thread?",
        "response_prompt": "Maid Monica is publicly questioning your noble pedigree in the Great Hall. Defend yourself with haughty grace and sharp wit, dismissing her words as the bitter envy of a spurned maiden."
    },
    {
        "pair": ("maid_marilyn", "sir_rufus"),
        "provocation": "Sir Rufus! I demand thou return the silk handkerchief thou didst wrest from my hands! The queen's decree may grant a victor his whim, but my loyalty shall never attend an oppressor of common folk!",
        "response_prompt": "Maid Marilyn is publicly demanding her handkerchief back before everyone. Refuse with mocking chivalry, stating that the champion's right is sacred under the Queen's laws."
    },
    {
        "pair": ("joking_jerry", "sir_rufus"),
        "provocation": "Hark ye, lords and ladies! The noble tax-collector Sir Rufus taketh ten percent for the Queen, but fifteen percent from the peasants! Methinks the extra coins stick nicely to his own velvet purse!",
        "response_prompt": "Joking Jerry is publicly mocking your tax extortion in the Great Hall. Threaten him with cold knightly menace, warning the jester that a sharp tongue often finds the iron pillory."
    }
]

DRAMA_TOPICS_STAGE_3_4 = [
    {
        "pair": ("willie_watchman", "sir_cameron"),
        "provocation": "Hold fast, Sir Cameron! Sir Rufus lies slain, and Exhibit D reveals a vial of horse poison in thy stall! Stand firm until the watch has accounted for thy movements during the blackout!",
        "response_prompt": "Willie the Watchman is publicly naming you a prime murder suspect in the Great Hall. Defend your knightly honor with fierce fury, swearing that a true knight fights with lance and shield, not with cowards' daggers in the dark."
    },
    {
        "pair": ("maid_monica", "lady_diana"),
        "provocation": "Look upon our bride! Just before the torches were snuffed, Sir Rufus looked upon thee as if he beheld a ghost from Dunnsberry! What secret did he hold over thee, Diana, that cost him his breath?",
        "response_prompt": "Maid Monica is publicly accusing you of having a secret motive to murder Sir Rufus. Defend yourself fiercely, accusing Monica of weaponizing this tragedy out of sheer spite over the wedding."
    },
    {
        "pair": ("charlamagne", "lady_gwendolyn"),
        "provocation": "My Lady Gwendolyn, amid this horror, methinks certain nobles would pay handsomely to learn what poisoned phials and threatening notes were carried through the corridors before the feast!",
        "response_prompt": "Charlamagne is hinting aloud in the Great Hall about poisoned phials and notes. Command her with aristocratic authority to bring all knowledge to the Watchman at once."
    },
    {
        "pair": ("baron_bartholomew", "lady_diana"),
        "provocation": "Diana, my love... Exhibit A bears the distinct crest of thy home manor of Dunnsberry! In Heaven's name, tell the court thou hadst no part in this bloody weapon!",
        "response_prompt": "Baron Bartholomew is distressed and questioning you publicly about the Dunnsberry dagger. Comfort him tenderly while swearing your innocence, suggesting the true assassin framed Dunnsberry to destroy your union."
    },
    {
        "pair": ("joking_jerry", "willie_watchman"),
        "provocation": "A grim feast indeed! Sir Rufus arrived a champion and departs in a shroud! Willie, tell us, will thy watch catch the phantom blade, or shall another goblet be poured in blood?",
        "response_prompt": "Joking Jerry is jesting grimly about the murder. Command order and solemnity, assuring the assembly that royal justice in Fernwood Manor will brook no escape for the killer."
    }
]

async def autonomous_mingling_loop():
    """Periodically triggers dramatic AI-to-AI exchanges in the Great Hall if quiet."""
    used_drama_indices = set()
    while True:
        await asyncio.sleep(40) # Check every 40 seconds
        try:
            if not game_state.is_game_started or game_state.stage >= 5:
                continue

            # Check time since last message
            now = time.time()
            if game_state.great_hall_messages:
                last_msg_time = game_state.great_hall_messages[-1]["timestamp"]
                if now - last_msg_time < 35:
                    continue # Not quiet yet

            # Select appropriate topic pool
            topic_pool = DRAMA_TOPICS_STAGE_1_2 if game_state.stage in [1, 2] else DRAMA_TOPICS_STAGE_3_4

            # Find valid drama topics where both characters are active AI and alive
            valid_dramas = []
            for idx, drama in enumerate(topic_pool):
                c1_id, c2_id = drama["pair"]
                c1 = game_state.characters.get(c1_id)
                c2 = game_state.characters.get(c2_id)
                if not c1 or not c2:
                    continue
                if c1.get("is_human") or c2.get("is_human"):
                    continue
                if (c1.get("is_victim") or c2.get("is_victim")) and game_state.stage >= 3:
                    continue
                valid_dramas.append((idx, drama, c1, c2))

            if valid_dramas:
                fresh_dramas = [d for d in valid_dramas if d[0] not in used_drama_indices]
                if not fresh_dramas:
                    used_drama_indices.clear()
                    fresh_dramas = valid_dramas

                import random
                chosen_idx, chosen_drama, char_a, char_b = random.choice(fresh_dramas)
                used_drama_indices.add(chosen_idx)

                # Character A delivers the opening provocation
                msg_a = game_state.add_message(char_a["id"], chosen_drama["provocation"], chat_type="public")
                await broadcast({"type": "new_message", "message": msg_a})

                # Pause 3.5 seconds to simulate Character B reacting
                await asyncio.sleep(3.5)

                # Character B generates dynamic retort
                sys_prompt_b = build_character_system_prompt(
                    char_b,
                    game_state.stage,
                    game_state.get_active_characters(),
                    char_b["current_gold"],
                    game_state.gold_transactions,
                    is_private_chat=False
                )
                recent_msgs = [
                    {"role": "user" if m["sender_id"] != char_b["id"] else "model", "content": f"{m['sender_name']}: {m['content']}"}
                    for m in game_state.great_hall_messages[-10:]
                ]
                recent_msgs.append({
                    "role": "user",
                    "content": f"{char_a['name']} just challenged you publicly in the Great Hall with: \"{chosen_drama['provocation']}\". {chosen_drama['response_prompt']}"
                })
                retort = await ai_engine.generate_response(sys_prompt_b, recent_msgs, max_tokens=250)
                if retort and not retort.startswith("(") and len(retort) > 10:
                    msg_b = game_state.add_message(char_b["id"], retort, chat_type="public")
                    await broadcast({"type": "new_message", "message": msg_b})

            else:
                # Fallback to single atmospheric mingling if no pair available
                active_chars = [
                    c for c in game_state.get_active_characters() 
                    if not c.get("is_human") and not (c.get("is_victim") and game_state.stage >= 3)
                ]
                if active_chars:
                    import random
                    speaker = random.choice(active_chars)
                    sys_prompt = build_character_system_prompt(
                        speaker,
                        game_state.stage,
                        game_state.get_active_characters(),
                        speaker["current_gold"],
                        game_state.gold_transactions,
                        is_private_chat=False
                    )
                    mingle_instruction = (
                        f"You are mingling in the Great Hall during Stage {game_state.stage}. "
                        "Make a brief, atmospheric observation (1-2 sentences), gossip about another guest, "
                        "or question someone aloud in authentic medieval speech."
                    )
                    recent_msgs = [
                        {"role": "user" if m["sender_id"] != speaker["id"] else "model", "content": f"{m['sender_name']}: {m['content']}"}
                        for m in game_state.great_hall_messages[-8:]
                    ]
                    recent_msgs.append({"role": "user", "content": mingle_instruction})
                    reply = await ai_engine.generate_response(sys_prompt, recent_msgs, max_tokens=180)
                    if reply and not reply.startswith("(") and len(reply) > 10:
                        msg = game_state.add_message(speaker["id"], reply, chat_type="public")
                        await broadcast({"type": "new_message", "message": msg})
        except Exception as e:
            logger.error(f"Error in mingling drama loop: {e}")

UNDERCOVER_PLOTS_STAGE_1_2 = [
    {
        "pair": ("maid_monica", "baron_bartholomew"),
        "provocation": "Baron Bartholomew, heed my quiet whisper ere the wedding vows are sealed! My heart hath not forgotten the promises made beneath the boughs. Dost thou truly intend to wed this foreign lady of Dunnsberry and cast our memories to the wind?",
        "response_prompt": "Maid Monica is privately whispering to you about your past romance. Remind her of your father's strict decree and the arranged marriage for Fernwood's honor, begging for her silence and discretion.",
        "bribe_coins": 1,
        "clue_hint": "🗝️ [Castle Whisper] Maid Monica and Baron Bartholomew were seen speaking in urgent, hushed tones near the shadowed corridor..."
    },
    {
        "pair": ("charlamagne", "lady_gwendolyn"),
        "provocation": "My Lady Gwendolyn, a discreet word in thy ear. While preparing the bedchambers, my eyes chanced upon certain dried herbs and mysterious correspondence. A noble lady surely would not wish the Watchman's eyes turned toward her luggage... for a modest token, my lips are sealed.",
        "response_prompt": "Chambermaid Charlamagne is subtly blackmailing you over secrets found in the guest chambers. React with haughty noble chill, warning the servant against insolence, but discreetly offering a coin for her silence.",
        "bribe_coins": 2,
        "clue_hint": "🗝️ [Castle Whisper] Chambermaid Charlamagne was seen slipping a sealed note into Lady Gwendolyn's sleeve..."
    },
    {
        "pair": ("sir_cameron", "maid_marilyn"),
        "provocation": "Maid Marilyn, pray grant me a moment aside from the feasting lords. Tell me truly: didst thou see Sir Rufus enter the stables before the tournament tilt? My charger fell sluggish before his lance, and I swear upon my honor foul play was at work!",
        "response_prompt": "Sir Cameron is privately asking you about the stables and Sir Rufus before the joust. Answer cautiously, sharing that Rufus was indeed lingering near the tack room, but warn Cameron to tread carefully.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Castle Whisper] Sir Cameron was observed whispering intently with Maid Marilyn beside the armory torches..."
    },
    {
        "pair": ("joking_jerry", "baron_bartholomew"),
        "provocation": "Psst, my young Lord Baron! The jester's bells hear many things the throne misses. A whispered bird tells me certain tavern debts and older vows are being traded as currency among the scullery maids this night...",
        "response_prompt": "Joking Jerry is playfully probing your secrets in private. Threaten the fool with stern nobility to keep his jests away from family matters, slipping him a coin to hold his tongue.",
        "bribe_coins": 1,
        "clue_hint": "🗝️ [Castle Whisper] Joking Jerry was seen whispering a jest into Baron Bartholomew's ear, receiving a quick glare..."
    },
    {
        "pair": ("queen_genevieve", "lord_taylor"),
        "provocation": "Lord Taylor, step into this alcove. We must speak without prying courtiers. The crown's coffers have noted the deficit in Fernwood's levies. If this lavish banquet and bridal dowry have been funded by royal tax gold, your lordship shall answer to the King's bench.",
        "response_prompt": "Queen Genevieve is cornering you in private about missing taxes and festival expenses. Reassure Her Majesty with utmost courtly deference, promising that the autumn harvest will balance the crown's ledger.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Castle Whisper] Queen Genevieve and Lord Taylor were seen in private conference in the high gallery..."
    },
    {
        "pair": ("lady_diana", "sir_cameron"),
        "provocation": "Sir Cameron, I observe how Sir Rufus looks upon me with insolent eyes, whispering of Dunnsberry. As a knight sworn to chivalry, would you stand as my defender should his venomous tongue cross the line of honor?",
        "response_prompt": "Lady Diana is secretly asking for your knightly protection against Sir Rufus. Pledge your sword to protect the bride's honor with noble chivalry, declaring your contempt for Rufus.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Castle Whisper] Lady Diana and Sir Cameron shared a brief, solemn whispered pledge near the grand hearth..."
    }
]

UNDERCOVER_PLOTS_STAGE_3_4 = [
    {
        "pair": ("willie_watchman", "maid_monica"),
        "provocation": "Halt thy steps, Monica. As bailiff and watchman, I noticed thee slipping through the west corridor right as the torches were extinguished. Sir Rufus choked upon poison moments later. What carried thee toward the high table in the dark?",
        "response_prompt": "Willie the Watchman is cornering you about your movements during the blackout. Defend yourself nervously, swearing you were only fetching spiced wine and pointing suspicion towards Lady Diana.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Castle Whisper] Willie the Watchman was seen grilling Maid Monica in the shadow of the pantry archway..."
    },
    {
        "pair": ("willie_watchman", "sir_cameron"),
        "provocation": "Sir Cameron, a word in private. Exhibit D is a vial of nightshade found among the straw of thy stall. Rufus is dead. Why should I not put thee in the manor irons this very instant?",
        "response_prompt": "Willie the Watchman is accusing you of possessing the murder poison. Proclaim your knightly innocence with fiery indignation, demanding he examine Exhibit A (the Dunnsberry dagger).",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Castle Whisper] Willie the Watchman and Sir Cameron were seen in a heated, low-voiced argument near the guardpost..."
    },
    {
        "pair": ("lady_diana", "baron_bartholomew"),
        "provocation": "Bartholomew, my beloved... I am terrified. That dagger bears the crest of my father's house, yet I swear before God I did not strike Sir Rufus down! Someone seeks to destroy our union and frame Dunnsberry!",
        "response_prompt": "Lady Diana is trembling and confiding in you privately about the murder weapon. Comfort your betrothed with tender devotion, promising that you will protect her from false accusations.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Castle Whisper] Baron Bartholomew and Lady Diana were seen clutching hands in a secluded window recess..."
    },
    {
        "pair": ("charlamagne", "willie_watchman"),
        "provocation": "Master Watchman, castle secrets are heavy to bear alone. If the watch offers protection and a purse of coin, I might disclose whose slippers were stained with candlewax and mud after the blackout...",
        "response_prompt": "Chambermaid Charlamagne is offering to sell clues about the murder. Demand her evidence sternly under the Queen's law, offering a small bounty for genuine truth.",
        "bribe_coins": 1,
        "clue_hint": "🗝️ [Castle Whisper] Chambermaid Charlamagne was seen murmuring into Willie the Watchman's ear..."
    },
    {
        "pair": ("joking_jerry", "lady_diana"),
        "provocation": "Lady of Dunnsberry... or shall I say, the lady with many names? Old Jerry's memory is longer than his jokes. I recall a certain maiden from the northern fairs who was not born into silk... what would the Baron say if the jester began to sing?",
        "response_prompt": "Joking Jerry is hinting that he knows you are an escaped serf. React with hidden dread beneath a cool aristocratic facade, slipping him gold to keep his mouth firmly shut.",
        "bribe_coins": 2,
        "clue_hint": "🗝️ [Castle Whisper] Joking Jerry was seen accepting a quiet token from Lady Diana with a sly bow..."
    }
]

async def autonomous_undercover_loop():
    """Periodically triggers clandestine secret whispers between AI characters."""
    used_indices = set()
    while True:
        await asyncio.sleep(45)
        try:
            if not game_state.is_game_started or game_state.stage >= 5:
                continue

            pool = UNDERCOVER_PLOTS_STAGE_1_2 if game_state.stage in [1, 2] else UNDERCOVER_PLOTS_STAGE_3_4

            # Filter valid plots where both are active AI characters and alive
            valid_plots = []
            for idx, p in enumerate(pool):
                c1_id, c2_id = p["pair"]
                c1 = game_state.characters.get(c1_id)
                c2 = game_state.characters.get(c2_id)
                if not c1 or not c2:
                    continue
                if c1.get("is_human") or c2.get("is_human"):
                    continue
                if (c1.get("is_victim") or c2.get("is_victim")) and game_state.stage >= 3:
                    continue
                valid_plots.append((idx, p, c1, c2))

            if not valid_plots:
                continue

            fresh = [p for p in valid_plots if p[0] not in used_indices]
            if not fresh:
                used_indices.clear()
                fresh = valid_plots

            import random
            chosen_idx, plot, char_a, char_b = random.choice(fresh)
            used_indices.add(chosen_idx)

            # Character A whispers to Character B
            msg_a = game_state.add_message(
                char_a["id"], 
                plot["provocation"], 
                chat_type="private", 
                recipient_id=char_b["id"],
                is_undercover=True
            )
            chat_key = game_state.get_private_chat_key(char_a["id"], char_b["id"])
            await broadcast({
                "type": "new_private_message",
                "chat_key": chat_key,
                "message": msg_a,
                "recipient_id": char_b["id"],
                "is_undercover": True
            })

            # Wait 3.5 seconds before reply
            await asyncio.sleep(3.5)

            # Character B generates dynamic retort
            sys_prompt_b = build_character_system_prompt(
                char_b,
                game_state.stage,
                game_state.get_active_characters(),
                char_b["current_gold"],
                game_state.gold_transactions,
                is_private_chat=True,
                private_partner_name=char_a["name"]
            )
            history_b = [
                {"role": "user", "content": f"{char_a['name']}: {plot['provocation']}"},
                {"role": "user", "content": f"{plot['response_prompt']} Respond in 2-3 sentences in authentic medieval speech."}
            ]
            retort = await ai_engine.generate_response(sys_prompt_b, history_b, max_tokens=250)
            if retort and len(retort) > 10:
                msg_b = game_state.add_message(
                    char_b["id"], 
                    retort, 
                    chat_type="private", 
                    recipient_id=char_a["id"],
                    is_undercover=True
                )
                await broadcast({
                    "type": "new_private_message",
                    "chat_key": chat_key,
                    "message": msg_b,
                    "recipient_id": char_a["id"],
                    "is_undercover": True
                })

            # Handle clandestine gold transfer if specified
            bribe_amount = plot.get("bribe_coins", 0)
            if bribe_amount > 0 and char_a["current_gold"] >= bribe_amount:
                game_state.transfer_gold(
                    char_a["id"],
                    char_b["id"],
                    bribe_amount,
                    f"Clandestine agreement between {char_a['name']} and {char_b['name']}"
                )

        except Exception as e:
            logger.error(f"Error in undercover whisper loop: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background mingling task and undercover AI whisper task
    mingle_task = asyncio.create_task(autonomous_mingling_loop())
    undercover_task = asyncio.create_task(autonomous_undercover_loop())
    yield
    mingle_task.cancel()
    undercover_task.cancel()

app = FastAPI(title="A Knight of Murder", lifespan=lifespan)

# Mount static folder
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    resp = FileResponse("static/index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.get("/api/network-info")
async def get_network_info():
    ip = get_lan_ip()
    port = 8000
    lan_url = f"http://{ip}:{port}"
    return {
        "ip": ip,
        "port": port,
        "lan_url": lan_url
    }

@app.get("/api/game-data")
async def get_public_game_data():
    return {
        "title": game_state.raw_data.get("title"),
        "setting": game_state.raw_data.get("setting"),
        "cast_size": game_state.cast_size,
        "stage": game_state.stage,
        "stage_title": game_state.stage,
        "is_game_started": game_state.is_game_started,
        "characters": game_state.get_active_characters(),
        "human_players": game_state.human_players,
        "revealed_exhibits": list(game_state.revealed_exhibits),
        "exhibits": game_state.raw_data.get("exhibits", []),
        "laws_of_the_land": game_state.raw_data.get("speeches", {}).get("laws_of_the_land", ""),
        "awards": game_state.awards,
        "llm_provider": game_state.llm_provider,
        "has_api_key": bool(game_state.api_key)
    }

@app.get("/api/chatlog")
async def get_chatlog(player_id: Optional[str] = None, host_key: Optional[str] = None):
    is_host = bool(host_key and host_key.strip().upper() == game_state.room_code.upper())
    return game_state.get_chatlog_data(player_id=player_id, is_host=is_host)

@app.get("/api/chatlog/export")
async def export_chatlog(player_id: Optional[str] = None, host_key: Optional[str] = None):
    is_host = bool(host_key and host_key.strip().upper() == game_state.room_code.upper())
    text = game_state.export_chatlog_text(player_id=player_id, is_host=is_host)
    return PlainTextResponse(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=court_chronicles_{game_state.room_code}.txt"}
    )

@app.post("/api/chatlog/save")
async def save_chatlog():
    game_state.save_chatlog_to_disk()
    return {"status": "ok", "message": "Saved to chatlogs/chatlog_latest.txt"}

@app.post("/api/undercover-trigger")
async def trigger_undercover_endpoint():
    """Trigger an autonomous undercover whisper between two AIs immediately."""
    pool = UNDERCOVER_PLOTS_STAGE_1_2 if game_state.stage in [1, 2] else UNDERCOVER_PLOTS_STAGE_3_4
    valid_plots = []
    for p in pool:
        c1_id, c2_id = p["pair"]
        c1 = game_state.characters.get(c1_id)
        c2 = game_state.characters.get(c2_id)
        if not c1 or not c2 or c1.get("is_human") or c2.get("is_human"):
            continue
        if (c1.get("is_victim") or c2.get("is_victim")) and game_state.stage >= 3:
            continue
        valid_plots.append((p, c1, c2))

    if not valid_plots:
        raise HTTPException(status_code=400, detail="No valid AI pair available for undercover whisper")

    import random
    plot, char_a, char_b = random.choice(valid_plots)

    # Character A whispers to Character B
    msg_a = game_state.add_message(
        char_a["id"], 
        plot["provocation"], 
        chat_type="private", 
        recipient_id=char_b["id"],
        is_undercover=True
    )
    chat_key = game_state.get_private_chat_key(char_a["id"], char_b["id"])
    await broadcast({
        "type": "new_private_message",
        "chat_key": chat_key,
        "message": msg_a,
        "recipient_id": char_b["id"],
        "is_undercover": True
    })

    # Character B responds via Gemini
    sys_prompt_b = build_character_system_prompt(
        char_b,
        game_state.stage,
        game_state.get_active_characters(),
        char_b["current_gold"],
        game_state.gold_transactions,
        is_private_chat=True,
        private_partner_name=char_a["name"]
    )
    history_b = [
        {"role": "user", "content": f"{char_a['name']}: {plot['provocation']}"},
        {"role": "user", "content": f"{plot['response_prompt']} Respond in 2-3 sentences in authentic medieval speech."}
    ]
    retort = await ai_engine.generate_response(sys_prompt_b, history_b, max_tokens=250)
    msg_b = game_state.add_message(
        char_b["id"], 
        retort, 
        chat_type="private", 
        recipient_id=char_a["id"],
        is_undercover=True
    )
    await broadcast({
        "type": "new_private_message",
        "chat_key": chat_key,
        "message": msg_b,
        "recipient_id": char_a["id"],
        "is_undercover": True
    })

    bribe_amount = plot.get("bribe_coins", 0)
    if bribe_amount > 0 and char_a["current_gold"] >= bribe_amount:
        game_state.transfer_gold(
            char_a["id"],
            char_b["id"],
            bribe_amount,
            f"Clandestine agreement between {char_a['name']} and {char_b['name']}"
        )

    return {
        "status": "ok",
        "pair": [char_a["name"], char_b["name"]],
        "provocation": plot["provocation"],
        "reply": retort
    }

@app.get("/api/state/{player_id}")
async def get_player_state(player_id: str):
    pdata = game_state.human_players.get(player_id)
    if not pdata:
        return {"registered": False}

    cid = pdata.get("character_id")
    char_info = game_state.characters.get(cid, {})

    # Gather player's private chats
    player_privates = {}
    for key, msgs in game_state.private_chats.items():
        if cid in key.split("__"):
            # Determine other character
            parts = key.split("__")
            other_id = parts[1] if parts[0] == cid else parts[0]
            player_privates[other_id] = msgs
            player_privates[key] = msgs

    return {
        "registered": True,
        "player_id": player_id,
        "player_name": pdata.get("player_name"),
        "character": char_info,
        "ready": pdata.get("ready_for_next_stage", False),
        "private_chats": player_privates,
        "great_hall_messages": game_state.great_hall_messages,
        "stage": game_state.stage,
        "is_game_started": game_state.is_game_started,
        "human_players": game_state.human_players
    }

@app.post("/api/settings")
async def update_settings(payload: Dict[str, Any] = Body(...)):
    api_key = payload.get("api_key", "").strip()
    provider = payload.get("provider", "gemini").strip()
    cast_size = payload.get("cast_size", 12)

    if api_key:
        game_state.api_key = api_key
    game_state.llm_provider = provider
    game_state.set_cast_size(cast_size)

    ai_engine.set_config(api_key=game_state.api_key, provider=game_state.llm_provider)
    game_state.save_state()

    await broadcast({
        "type": "settings_updated",
        "cast_size": game_state.cast_size,
        "llm_provider": game_state.llm_provider,
        "has_api_key": bool(game_state.api_key)
    })
    return {"status": "ok"}

@app.post("/api/reset")
async def reset_game():
    game_state.reset()
    await broadcast({
        "type": "game_reset",
        "stage": game_state.stage,
        "characters": game_state.get_active_characters()
    })
    return {"status": "ok"}

@app.post("/api/join")
async def join_room(payload: Dict[str, Any] = Body(...)):
    player_id = payload.get("player_id")
    player_name = payload.get("player_name", "Noble Guest")
    character_id = payload.get("character_id")

    if not player_id or not character_id:
        raise HTTPException(status_code=400, detail="Missing player_id or character_id")

    success = game_state.register_human_player(player_id, player_name, character_id)
    if not success:
        raise HTTPException(status_code=400, detail="Character unavailable or locked to AI")

    await broadcast({
        "type": "player_joined",
        "human_players": game_state.human_players,
        "characters": game_state.get_active_characters()
    })
    return {"status": "ok", "character": game_state.characters[character_id]}

@app.post("/api/start")
async def start_game_endpoint():
    game_state.start_game()
    await broadcast({
        "type": "game_started",
        "stage": game_state.stage,
        "messages": game_state.great_hall_messages
    })
    return {"status": "ok"}

@app.post("/api/ready")
async def toggle_ready_endpoint(payload: Dict[str, Any] = Body(...)):
    player_id = payload.get("player_id")
    if not player_id:
        raise HTTPException(status_code=400, detail="Missing player_id")

    all_ready = game_state.toggle_player_ready(player_id)
    await broadcast({
        "type": "ready_toggled",
        "player_id": player_id,
        "human_players": game_state.human_players
    })

    # If all players ready, advance stage automatically!
    if all_ready:
        event = game_state.advance_stage()
        await broadcast({
            "type": "stage_advanced",
            "stage": game_state.stage,
            "event": event,
            "messages": game_state.great_hall_messages,
            "revealed_exhibits": list(game_state.revealed_exhibits),
            "awards": game_state.awards
        })

    return {"status": "ok", "all_ready": all_ready}

@app.post("/api/bribe")
async def transfer_gold_endpoint(payload: Dict[str, Any] = Body(...)):
    sender_id = payload.get("sender_id")
    receiver_id = payload.get("receiver_id")
    amount = int(payload.get("amount", 1))
    message = payload.get("message", "")

    success = game_state.transfer_gold(sender_id, receiver_id, amount, message)
    if not success:
        raise HTTPException(status_code=400, detail="Transfer failed: insufficient gold or invalid character.")

    # Record notification message in private or public chat
    sys_note = f"💰 [BRIBE/PAYMENT] {game_state.characters[sender_id]['name']} handed {amount} Gold Coin(s) to {game_state.characters[receiver_id]['name']}. Note: \"{message}\""
    msg = game_state.add_message(sender_id, sys_note, chat_type="private", recipient_id=receiver_id)

    await broadcast({
        "type": "gold_transferred",
        "from_id": sender_id,
        "to_id": receiver_id,
        "amount": amount,
        "sender_gold": game_state.characters[sender_id]["current_gold"],
        "receiver_gold": game_state.characters[receiver_id]["current_gold"],
        "message": msg
    })

    # If receiver is an AI, trigger AI grateful reaction!
    receiver_char = game_state.characters.get(receiver_id)
    if receiver_char and not receiver_char.get("is_human"):
        asyncio.create_task(handle_ai_bribe_reaction(receiver_id, sender_id, amount, message))

    return {"status": "ok", "new_gold": game_state.characters[sender_id]["current_gold"]}

def get_character_social_tier(char_id: str) -> str:
    if char_id == "queen_genevieve":
        return "royalty"
    elif char_id == "willie_watchman":
        return "law"
    elif char_id in ["lord_taylor", "lady_gwendolyn", "baron_bartholomew", "lady_diana", "sir_cameron", "sir_rufus"]:
        return "noble"
    else:
        return "commoner"

async def handle_ai_bribe_reaction(ai_cid: str, sender_cid: str, amount: int, bribe_note: str):
    ai_char = game_state.characters[ai_cid]
    sender_char = game_state.characters[sender_cid]
    tier = get_character_social_tier(ai_cid)
    partner_transactions = game_state.get_transactions_between(ai_cid, sender_cid)

    sys_prompt = build_character_system_prompt(
        ai_char,
        game_state.stage,
        game_state.get_active_characters(),
        ai_char["current_gold"],
        game_state.gold_transactions,
        is_private_chat=True,
        private_partner_name=sender_char["name"],
        partner_transactions=partner_transactions
    )

    if tier == "royalty":
        tier_guidance = (
            f"YOU ARE QUEEN GENEVIEVE, THE SOVEREIGN MONARCH. {sender_char['name']} has dared to offer you {amount} gold coin(s) with note: \"{bribe_note}\". "
            "A Queen does NOT accept bribes from her subjects! Treat this gesture with regal indignation and haughty disdain. "
            "Remind them that Clause 1 of the Laws of the Land decrees betrayal of the Queen is punishable by death! "
            "Declare that you are confiscating these coins into the Royal Treasury as a fine for their insolence, and sternly demand their true business."
        )
    elif tier == "noble":
        if amount < 3:
            tier_guidance = (
                f"You are a proud noble/knight ({ai_char['title']}). {sender_char['name']} offered merely {amount} gold coin(s) with note: \"{bribe_note}\". "
                "For a person of noble blood and high wealth, this is an amusingly insulting pittance! "
                "React with condescending amusement, aristocratic mockery, or cold disdain. "
                "Refuse to divulge any deep secrets for mere pocket change, though you may drop a haughty remark or demand a tribute worthy of a noble."
            )
        else:
            tier_guidance = (
                f"You are a noble/knight ({ai_char['title']}). {sender_char['name']} has presented a handsome and respectful tribute of {amount} gold coins with note: \"{bribe_note}\". "
                "Acknowledge this respectable tribute with dignified appreciation and noble discretion. "
                "In exchange, share a valuable secret, gossip, or tactical observation fitting your character's objectives."
            )
    elif tier == "law":
        tier_guidance = (
            f"You are Willie the Watchman, sworn guardian of castle peace and investigator. {sender_char['name']} handed you {amount} gold coins with note: \"{bribe_note}\". "
            "If this note seems to bribe you to look away from a crime or drop an inquiry, fiercely reprimand them that royal justice and the blood of Sir Rufus cannot be bought! "
            "If it is framed respectfully as assistance or a tribute to the castle watch, accept it with gruff practicality and share an objective, watchful observation about the guests or physical evidence."
        )
    else: # commoner
        if amount == 1:
            tier_guidance = (
                f"You are a humble servant/jester ({ai_char['title']}). {sender_char['name']} gave you 1 gold coin with note: \"{bribe_note}\". "
                "Pocket the coin gratefully, thank them, and share a small, helpful rumor or hint in return."
            )
        else:
            tier_guidance = (
                f"You are a humble servant/jester ({ai_char['title']}). {sender_char['name']} gave you a generous bounty of {amount} gold coins with note: \"{bribe_note}\". "
                "Be ecstatic and deeply indebted! For such immense wealth, eagerly whisper your most juicy secrets, rumors, or agree to assist them faithfully."
            )

    instruction = (
        f"{sender_char['name']} just slipped you {amount} gold coin(s) with the note: \"{bribe_note}\".\n"
        f"GUIDANCE: {tier_guidance}\n"
        "Deliver 2 to 3 complete sentences directly in character in authentic medieval speech."
    )
    reply = await ai_engine.generate_response(sys_prompt, [{"role": "user", "content": instruction}], max_tokens=250)
    ai_msg = game_state.add_message(ai_cid, reply, chat_type="private", recipient_id=sender_cid)
    await broadcast({
        "type": "new_private_message",
        "chat_key": game_state.get_private_chat_key(ai_cid, sender_cid),
        "message": ai_msg,
        "recipient_id": sender_cid
    })

@app.post("/api/vote")
async def submit_vote_endpoint(payload: Dict[str, Any] = Body(...)):
    voter_id = payload.get("voter_id")
    accused_id = payload.get("accused_id")
    motive = payload.get("motive", "")
    evidence = payload.get("evidence", "")
    best_dressed = payload.get("best_dressed", "")
    best_perf = payload.get("best_perf", "")

    if not voter_id or not accused_id:
        raise HTTPException(status_code=400, detail="Missing required vote fields")

    game_state.submit_vote(voter_id, accused_id, motive, evidence, best_dressed, best_perf)

    # Also automatically have all AI characters cast their secret votes!
    active_chars = game_state.get_active_characters()
    for c in active_chars:
        cid = c["id"]
        if not c.get("is_human") and cid not in game_state.votes and not (c.get("is_victim") and game_state.stage >= 3):
            # AI votes with personality
            import random
            possible_accused = [x["id"] for x in active_chars if x["id"] != cid and not x.get("is_victim")]
            # AI suspects Monica, Cameron, or Diana
            likely = ["lady_diana", "maid_monica", "sir_cameron"]
            acc = random.choice([x for x in likely if x in possible_accused] or possible_accused)
            game_state.submit_vote(
                voter_id=cid,
                accused_id=acc,
                motive="Suspicious motives and castle intrigue.",
                evidence="Exhibit A dagger and secret grudges.",
                best_dressed=random.choice([x["name"] for x in active_chars]),
                best_perf=random.choice([x["name"] for x in active_chars])
            )

    await broadcast({"type": "vote_recorded", "voter_id": voter_id})
    return {"status": "ok"}

@app.post("/api/toggle-objective")
async def toggle_objective(payload: Dict[str, Any] = Body(...)):
    player_id = payload.get("player_id")
    obj_index = payload.get("index")
    pdata = game_state.human_players.get(player_id)
    if not pdata:
        raise HTTPException(status_code=400, detail="Not registered")
    cid = pdata.get("character_id")
    char_info = game_state.characters.get(cid)
    if char_info:
        checked = set(char_info.get("checked_objectives", []))
        if obj_index in checked:
            checked.remove(obj_index)
        else:
            checked.add(obj_index)
        char_info["checked_objectives"] = list(checked)
        game_state.save_state()
    return {"status": "ok", "checked": char_info.get("checked_objectives", [])}

async def process_incoming_message(chat_type: str, sender_id: str, content: str, recipient_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Handles both public and private message dispatch, AI retorts, and broadcasting."""
    content = (content or "").strip()
    if not content or not sender_id:
        return None

    if chat_type == "public":
        msg = game_state.add_message(sender_id, content, chat_type="public")
        await broadcast({"type": "new_message", "message": msg})

        # Check if any AI character is mentioned with @ or addressed
        active_chars = game_state.get_active_characters()
        mentioned_ai = find_mentioned_character(content, active_chars, sender_id=sender_id)
        if mentioned_ai and not (mentioned_ai.get("is_victim") and game_state.stage >= 3):
            asyncio.create_task(generate_ai_great_hall_reply(mentioned_ai, sender_id, content))
        return msg

    elif chat_type == "private" and recipient_id:
        msg = game_state.add_message(sender_id, content, chat_type="private", recipient_id=recipient_id)
        chat_key = game_state.get_private_chat_key(sender_id, recipient_id)
        await broadcast({
            "type": "new_private_message",
            "chat_key": chat_key,
            "message": msg,
            "recipient_id": recipient_id
        })

        # If recipient is an AI (or offline player), trigger immediate private reply!
        recipient_char = game_state.characters.get(recipient_id)
        is_target_ai = recipient_char and (
            not recipient_char.get("is_human") or 
            not recipient_char.get("player_id") or 
            recipient_char.get("player_id") not in connected_websockets
        )
        if is_target_ai:
            asyncio.create_task(generate_ai_private_reply(recipient_char, sender_id, content))
        return msg

    return None

@app.post("/api/message")
async def send_message_http_endpoint(payload: Dict[str, Any] = Body(...)):
    chat_type = payload.get("type", "public")
    content = payload.get("content", "").strip()
    sender_id = payload.get("sender_id")
    recipient_id = payload.get("recipient_id")

    if not content or not sender_id:
        raise HTTPException(status_code=400, detail="Missing content or sender_id")

    msg = await process_incoming_message(chat_type, sender_id, content, recipient_id)
    if not msg:
        raise HTTPException(status_code=400, detail="Invalid message delivery parameters")
    return {"status": "ok", "message": msg}

@app.websocket("/ws/{player_id}")
async def websocket_endpoint(websocket: WebSocket, player_id: str):
    await websocket.accept()
    connected_websockets[player_id] = websocket
    logger.info(f"WebSocket connected for player: {player_id}")

    try:
        while True:
            raw_text = await websocket.receive_text()
            data = json.loads(raw_text)
            action = data.get("action")

            if action == "send_message":
                chat_type = data.get("type", "public")
                content = data.get("content", "").strip()
                sender_id = data.get("sender_id")
                recipient_id = data.get("recipient_id")
                await process_incoming_message(chat_type, sender_id, content, recipient_id)

    except WebSocketDisconnect:
        connected_websockets.pop(player_id, None)
        logger.info(f"WebSocket disconnected: {player_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        connected_websockets.pop(player_id, None)

async def generate_ai_great_hall_reply(ai_char: Dict[str, Any], sender_cid: str, user_content: str):
    sender_char = game_state.characters.get(sender_cid)
    sender_name = sender_char["name"] if sender_char else sender_cid

    sys_prompt = build_character_system_prompt(
        ai_char,
        game_state.stage,
        game_state.get_active_characters(),
        ai_char["current_gold"],
        game_state.gold_transactions,
        is_private_chat=False
    )
    history = [
        {"role": "model" if m["sender_id"] == ai_char["id"] else "user", "content": m["content"] if m["sender_id"] == ai_char["id"] else f"{m['sender_name']}: {m['content']}"}
        for m in game_state.great_hall_messages[-16:]
    ]
    mention_instruction = (
        f"You are {ai_char['name']}. {sender_name} just addressed you directly in the Great Hall with: \"{user_content}\". "
        "Reply directly to them aloud before the banquet in your authentic medieval voice fitting your rank. Deliver 2 to 3 complete sentences."
    )
    history.append({"role": "user", "content": mention_instruction})

    reply = await ai_engine.generate_response(sys_prompt, history, max_tokens=500)
    ai_msg = game_state.add_message(ai_char["id"], reply, chat_type="public")
    await broadcast({"type": "new_message", "message": ai_msg})

async def generate_ai_private_reply(ai_char: Dict[str, Any], sender_cid: str, user_content: str):
    try:
        sender_char = game_state.characters.get(sender_cid)
        sender_name = sender_char["name"] if sender_char else sender_cid
        chat_key = game_state.get_private_chat_key(ai_char["id"], sender_cid)
        msgs = game_state.private_chats.get(chat_key, [])
        partner_transactions = game_state.get_transactions_between(ai_char["id"], sender_cid)

        sys_prompt = build_character_system_prompt(
            ai_char,
            game_state.stage,
            game_state.get_active_characters(),
            ai_char["current_gold"],
            game_state.gold_transactions,
            is_private_chat=True,
            private_partner_name=sender_name,
            partner_transactions=partner_transactions
        )
        history = []
        # Up to 30 messages for long-term memory
        for m in msgs[-30:]:
            if m["sender_id"] == ai_char["id"]:
                history.append({"role": "model", "content": m["content"]})
            else:
                history.append({"role": "user", "content": f"{sender_name}: {m['content']}"})

        reply = await ai_engine.generate_response(sys_prompt, history, max_tokens=600)
        ai_msg = game_state.add_message(ai_char["id"], reply, chat_type="private", recipient_id=sender_cid)
        await broadcast({
            "type": "new_private_message",
            "chat_key": chat_key,
            "message": ai_msg,
            "recipient_id": sender_cid
        })
    except Exception as e:
        logger.error(f"Error in generate_ai_private_reply for {ai_char.get('id')}: {e}", exc_info=True)

if __name__ == "__main__":
    import uvicorn
    ip = get_lan_ip()
    port = int(os.getenv("PORT", 8000))
    print("\n========================================================")
    print("   A KNIGHT OF MURDER - SERVER STARTING")
    print("========================================================")
    print(f"   Local PC URL:      http://localhost:{port}")
    print(f"   Phone / LAN URL:   http://{ip}:{port}")
    print("========================================================\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
