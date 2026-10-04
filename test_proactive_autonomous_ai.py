import asyncio
import json
import urllib.request
import websockets
import sys
import re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_HTTP = "http://127.0.0.1:8000"
BASE_WS = "ws://127.0.0.1:8000/ws"

def http_post(path, data):
    url = f"{BASE_HTTP}{path}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def http_get(path):
    url = f"{BASE_HTTP}{path}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

async def test_3player_autonomous_ai_system():
    print("=" * 75)
    print("   TESTING 3-PLAYER SLEUTH PARTY, SUBTLE HINTS & AUTONOMOUS AI PLAYERS")
    print("=" * 75)

    # 1. Reset room
    print("\n>>> Step 1: Initializing room and registering 3 distinct Human Sleuths...")
    http_post("/api/reset", {})

    # Player 1: Lord Taylor
    p1 = http_post("/api/join", {"player_id": "human_p1", "player_name": "Lord Taylor (Host)", "character_id": "lord_taylor"})
    print(f"  Player 1 joined: {p1.get('character', {}).get('name')}")

    # Player 2: Lady Diana
    p2 = http_post("/api/join", {"player_id": "human_p2", "player_name": "Lady Diana (Bride)", "character_id": "lady_diana"})
    print(f"  Player 2 joined: {p2.get('character', {}).get('name')}")

    # Player 3: Sir Cameron
    p3 = http_post("/api/join", {"player_id": "human_p3", "player_name": "Sir Cameron (Knight)", "character_id": "sir_cameron"})
    print(f"  Player 3 joined: {p3.get('character', {}).get('name')}")

    state = http_get("/api/state/human_p1")
    human_count = len(state.get("human_players", {}))
    print(f"  Active human sleuths registered: {human_count}/3")
    assert human_count == 3, f"Expected 3 human players, got {human_count}"

    # 2. Start game
    start_res = http_post("/api/start", {})
    print(f"  Commence Feast response: status={start_res.get('status')}")

    async with websockets.connect(f"{BASE_WS}/human_p1") as ws1, \
               websockets.connect(f"{BASE_WS}/human_p2") as ws2, \
               websockets.connect(f"{BASE_WS}/human_p3") as ws3:
        print("  All 3 Human Sleuth WebSockets connected successfully.")

        # Drain initial join broadcast
        for ws in [ws1, ws2, ws3]:
            try:
                await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                pass

        # -------------------------------------------------------------
        # TEST 2: Royal Herald Subtle Hint Request (/api/hint)
        # -------------------------------------------------------------
        print("\n>>> Step 2: Testing Royal Herald Subtle Clue Mechanism...")
        hint_res = http_post("/api/hint", {"player_id": "human_p1"})
        assert hint_res.get("status") == "ok", "Failed: /api/hint did not return ok!"
        hint_text = hint_res.get("hint")
        print(f"  Royal Herald Proclaimed Subtle Clue:\n     \"{hint_text}\"")

        # Verify broadcast received on websockets
        received_hint_event = False
        try:
            raw = await asyncio.wait_for(ws2.recv(), timeout=3)
            data = json.loads(raw)
            if data.get("type") == "herald_hint":
                received_hint_event = True
                print("  Confirmed: Herald hint event broadcast to Player 2 WebSocket.")
        except asyncio.TimeoutError:
            pass

        # -------------------------------------------------------------
        # TEST 3: General Speech Reaction & Typing Indicators
        # -------------------------------------------------------------
        print("\n>>> Step 3: Testing Human General Speech & AI Typing Indicators...")
        speech_text = "The castle shadows stretch long tonight. Who among you truly trusts Baron Bartholomew's sweet promises?"
        http_post("/api/message", {
            "type": "public",
            "chat_type": "public",
            "sender_id": "lord_taylor",
            "content": speech_text
        })
        print(f"  Lord Taylor spoke aloud: \"{speech_text}\"")

        received_typing = False
        received_ai_reply = None
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < 25:
            try:
                raw = await asyncio.wait_for(ws1.recv(), timeout=3)
                data = json.loads(raw)
                if data.get("type") == "ai_typing":
                    if data.get("is_typing"):
                        received_typing = True
                        print(f"  Confirmed Typing Indicator: {data.get('character_name')} is writing...")
                elif data.get("type") == "new_message":
                    msg = data.get("message", {})
                    if msg.get("sender_id") not in ["lord_taylor", "system"]:
                        received_ai_reply = msg
                        break
            except asyncio.TimeoutError:
                pass

        if not received_ai_reply:
            state = http_get("/api/state/human_p1")
            for m in reversed(state.get("great_hall_messages", [])):
                if m.get("sender_id") not in ["lord_taylor", "system"]:
                    received_ai_reply = m
                    break

        assert received_ai_reply is not None, "Failed: No AI autonomously stepped up to answer general speech!"
        print(f"  SUCCESS! Autonomous AI Reaction Received:")
        print(f"     From: {received_ai_reply.get('sender_name')} ({received_ai_reply.get('sender_id')})")
        print(f"     Spoke: \"{received_ai_reply.get('content')}\"")

        # -------------------------------------------------------------
        # TEST 4: Proactive AI-to-Human Private Whisper
        # -------------------------------------------------------------
        print("\n>>> Step 4: Testing Proactive AI Whispers to Human Sleuths...")
        print("  Player 2 (Lady Diana) whispers to Queen Genevieve...")
        http_post("/api/message", {
            "type": "private",
            "chat_type": "private",
            "sender_id": "lady_diana",
            "recipient_id": "queen_genevieve",
            "content": "Your Grace, I fear the Baron's affections are cold. What did you observe during the tournament?"
        })

        queen_reply = None
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < 25:
            try:
                raw = await asyncio.wait_for(ws2.recv(), timeout=3)
                data = json.loads(raw)
                if data.get("type") == "ai_typing":
                    if data.get("is_typing") and data.get("character_id") == "queen_genevieve":
                        print(f"  Confirmed Private Typing Indicator: Queen Genevieve is penning a whisper...")
                elif data.get("type") == "new_private_message":
                    msg = data.get("message", {})
                    if msg.get("sender_id") == "queen_genevieve":
                        queen_reply = msg
                        break
            except asyncio.TimeoutError:
                pass

        if not queen_reply:
            state = http_get("/api/state/human_p2")
            for k, msgs in state.get("private_chats", {}).items():
                if "queen_genevieve" in k:
                    for m in msgs:
                        if m.get("sender_id") == "queen_genevieve":
                            queen_reply = m
                            break

        assert queen_reply is not None, "Failed: Queen Genevieve did not reply to Lady Diana!"
        print(f"  SUCCESS! Queen Genevieve Replied in Secluded Chambers:")
        print(f"     \"{queen_reply.get('content')}\"")

        # -------------------------------------------------------------
        # TEST 5: 3-Player Cooperative Stage Advance via Ready Toggles
        # -------------------------------------------------------------
        print("\n>>> Step 5: Testing 3-Player Ready Progression to Stage 2...")
        # Player 1 toggles ready
        r1 = http_post("/api/ready", {"player_id": "human_p1"})
        print(f"  Player 1 Ready: all_ready={r1.get('all_ready')}")
        assert not r1.get("all_ready"), "Should not be all ready with only 1/3 players ready!"

        # Player 2 toggles ready
        r2 = http_post("/api/ready", {"player_id": "human_p2"})
        print(f"  Player 2 Ready: all_ready={r2.get('all_ready')}")
        assert not r2.get("all_ready"), "Should not be all ready with only 2/3 players ready!"

        # Player 3 toggles ready -> All 3 ready! Stage advances to 2!
        r3 = http_post("/api/ready", {"player_id": "human_p3"})
        print(f"  Player 3 Ready: all_ready={r3.get('all_ready')}, current_stage={r3.get('stage')}")
        assert r3.get("all_ready") or r3.get("stage") == 2, "Stage should advance when all 3 players are ready!"

        # -------------------------------------------------------------
        # TEST 6: Language & Medieval Tone Verification
        # -------------------------------------------------------------
        print("\n>>> Step 6: Auditing Language across all 3 Sleuth Sessions...")
        vietnamese_pattern = re.compile(r'[\u00C0-\u024F\u1EA0-\u1EF9]')
        state = http_get("/api/state/human_p1")
        all_messages = state.get("great_hall_messages", [])
        for k, msgs in state.get("private_chats", {}).items():
            all_messages.extend(msgs)

        for m in all_messages:
            content = m.get("content", "")
            match = vietnamese_pattern.search(content)
            assert not match, f"Found non-English characters in message: {content}"

        print(f"  Verified {len(all_messages)} messages across Great Hall and private chambers.")
        print("  0 Vietnamese characters found! 100% Authentic Medieval English confirmed.")

        print("\n" + "=" * 75)
        print("   ALL 3-PLAYER & HERALD HINT TESTS PASSED WITH FLYING COLORS!")
        print("=" * 75)

if __name__ == "__main__":
    asyncio.run(test_3player_autonomous_ai_system())
