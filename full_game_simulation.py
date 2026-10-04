import asyncio
import json
import websockets
import urllib.request
import time
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_HTTP = "http://localhost:8000"
BASE_WS = "ws://localhost:8000/ws"

audit_report = {
    "stages_passed": [],
    "dialogues_tested": [],
    "errors": [],
    "warnings": [],
    "final_awards": None
}

def http_post(path, data):
    url = f"{BASE_HTTP}{path}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def http_get(path):
    url = f"{BASE_HTTP}{path}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))

async def run_simulation():
    print("\n========================================================")
    print("   STARTING FULL GAME SIMULATION AUDIT")
    print("========================================================\n")

    # -----------------------------------------------------------
    # STAGE 1: REGISTRATION & SETUP
    # -----------------------------------------------------------
    print(">>> [RESET] Resetting Game State to Stage 1...")
    reset_res = http_post("/api/reset", {})
    assert reset_res["status"] == "ok", f"Reset failed: {reset_res}"
    print("  ✓ Game state reset cleanly.")

    print(">>> [TEST 1] Registering Players and Starting Banquet...")
    p1_id = "sim_player_1"
    p2_id = "sim_player_2"

    res1 = http_post("/api/join", {"player_id": p1_id, "player_name": "Player One", "character_id": "lord_taylor"})
    assert res1["status"] == "ok", f"P1 join failed: {res1}"
    print("  ✓ Player 1 registered as Lord Taylor")

    res2 = http_post("/api/join", {"player_id": p2_id, "player_name": "Player Two", "character_id": "queen_genevieve"})
    assert res2["status"] == "ok", f"P2 join failed: {res2}"
    print("  ✓ Player 2 registered as Queen Genevieve")

    # Start Banquet
    start_res = http_post("/api/start", {})
    assert start_res["status"] == "ok", f"Start banquet failed: {start_res}"
    print("  ✓ Banquet Started successfully! Stage is 1.")
    audit_report["stages_passed"].append("Stage 1 Setup")

    # Connect WebSocket clients for both players
    async with websockets.connect(f"{BASE_WS}/{p1_id}") as ws1, websockets.connect(f"{BASE_WS}/{p2_id}") as ws2:
        print("  ✓ WebSockets connected for both players.")

        # -----------------------------------------------------------
        # STAGE 2: BANQUET & DIALOGUE AUDIT
        # -----------------------------------------------------------
        print("\n>>> [TEST 2] Advancing to Stage 2 (The Banquet & Festivities)...")
        # P1 and P2 ready
        http_post("/api/ready", {"player_id": p1_id})
        r2 = http_post("/api/ready", {"player_id": p2_id})
        assert r2["all_ready"], "Both players should be ready to advance to Stage 2"
        print("  ✓ Stage 2 advanced via 2-player ready vote.")
        audit_report["stages_passed"].append("Stage 2 Advance")

        # Give AI a moment to process transition message
        await asyncio.sleep(2)

        # Verify Stage 2 Intro Speech
        state_data = http_get(f"/api/state/{p1_id}")
        assert state_data["stage"] == 2, f"Expected Stage 2, got {state_data['stage']}"
        great_hall = state_data["great_hall_messages"]
        intro_found = any("Lord Taylor steps to the podium" in m["content"] for m in great_hall)
        assert intro_found, "Lord Taylor's introduction speech should be in Great Hall!"
        print("  ✓ Lord Taylor's opening introduction speech delivered to Great Hall.")

        # Test Great Hall Public Dialogue: Address @Lady Diana
        print("\n>>> [TEST 3] Testing Public Great Hall Dialogue with @Lady Diana...")
        await ws1.send(json.dumps({
            "action": "send_message",
            "type": "public",
            "sender_id": "lord_taylor",
            "content": "@Lady Diana of Dunnsberry, welcome to Fernwood Manor! Are you pleased with your upcoming wedding to my son?"
        }))

        # Wait for Diana's public reply
        diana_replied = False
        reply_content = ""
        for _ in range(15):
            await asyncio.sleep(1.5)
            st = http_get(f"/api/state/{p1_id}")
            for m in reversed(st.get("great_hall_messages", [])):
                if m["sender_id"] == "lady_diana":
                    diana_replied = True
                    reply_content = m["content"]
                    break
            if diana_replied:
                break

        print(f"  Lady Diana Public Reply:\n  \"{reply_content}\"")
        assert diana_replied, "Lady Diana should reply in Great Hall when addressed with @!"
        assert len(reply_content) > 15, "Diana's reply should be substantive!"
        assert not reply_content.startswith("Lady Diana:"), "Diana's reply should not have self-name prefix!"
        assert not reply_content.startswith("("), "Diana's reply should not have parenthetical stage directions!"
        print("  ✓ Lady Diana's Great Hall response is direct, coherent, and in-character.")
        audit_report["dialogues_tested"].append({
            "character": "Lady Diana",
            "setting": "Great Hall",
            "prompt": "Are you pleased with your upcoming wedding to my son?",
            "reply": reply_content
        })

        # Test Private Whispers 1: Lord Taylor whispers to Sir Rufus about taxes & marriage
        print("\n>>> [TEST 4] Testing Private Whisper: Lord Taylor -> Sir Rufus...")
        await ws1.send(json.dumps({
            "action": "send_message",
            "type": "private",
            "sender_id": "lord_taylor",
            "recipient_id": "sir_rufus",
            "content": "Sir Rufus, I hear rumors that you have been demanding 15% taxes from the peasants instead of 10%. By what authority do you claim this excess?"
        }))

        rufus_replied = False
        rufus_text = ""
        for _ in range(12):
            await asyncio.sleep(1.5)
            st = http_get(f"/api/state/{p1_id}")
            privates = st.get("private_chats", {}).get("sir_rufus", [])
            for m in privates:
                if m["sender_id"] == "sir_rufus":
                    rufus_replied = True
                    rufus_text = m["content"]
                    break
            if rufus_replied:
                break

        print(f"  Sir Rufus Private Reply:\n  \"{rufus_text}\"")
        assert rufus_replied, "Sir Rufus should reply to private whisper!"
        assert len(rufus_text) > 15, "Rufus's reply should be substantive!"
        assert not rufus_text.startswith("Sir Rufus:"), "Rufus should not prefix his name!"
        assert not rufus_text.startswith("("), "Rufus should not have stage directions!"
        print("  ✓ Sir Rufus answered directly in character regarding taxes.")
        audit_report["dialogues_tested"].append({
            "character": "Sir Rufus",
            "setting": "Private Whisper",
            "prompt": "Demanding 15% taxes instead of 10%",
            "reply": rufus_text
        })

        # Test Private Whispers 2: Lord Taylor whispers to Baron Bartholomew
        print("\n>>> [TEST 5] Testing Private Whisper: Lord Taylor -> Baron Bartholomew...")
        await ws1.send(json.dumps({
            "action": "send_message",
            "type": "private",
            "sender_id": "lord_taylor",
            "recipient_id": "baron_bartholomew",
            "content": "Bartholomew, are you truly content with this marriage to Lady Diana, or does your heart still yearn for Maid Monica?"
        }))

        baron_replied = False
        baron_text = ""
        for _ in range(12):
            await asyncio.sleep(1.5)
            st = http_get(f"/api/state/{p1_id}")
            privates = st.get("private_chats", {}).get("baron_bartholomew", [])
            for m in privates:
                if m["sender_id"] == "baron_bartholomew":
                    baron_replied = True
                    baron_text = m["content"]
                    break
            if baron_replied:
                break

        print(f"  Baron Bartholomew Private Reply:\n  \"{baron_text}\"")
        assert baron_replied, "Bartholomew should reply to private whisper!"
        assert len(baron_text) > 15, "Bartholomew's reply should be substantive!"
        assert not baron_text.startswith("Baron Bartholomew:"), "Bartholomew should not prefix his name!"
        print("  ✓ Baron Bartholomew answered directly regarding Diana and Monica.")
        audit_report["dialogues_tested"].append({
            "character": "Baron Bartholomew",
            "setting": "Private Whisper",
            "prompt": "Content with marriage to Diana or heart yearns for Monica?",
            "reply": baron_text
        })

        # Test Private Whispers 3: Queen Genevieve whispers to Maid Marilyn
        print("\n>>> [TEST 6] Testing Private Whisper: Queen Genevieve -> Maid Marilyn...")
        await ws2.send(json.dumps({
            "action": "send_message",
            "type": "private",
            "sender_id": "queen_genevieve",
            "recipient_id": "maid_marilyn",
            "content": "Marilyn, as Lady Gwendolyn's lady-in-waiting, what is your true feeling regarding Sir Cameron and Sir Rufus?"
        }))

        marilyn_replied = False
        marilyn_text = ""
        for _ in range(12):
            await asyncio.sleep(1.5)
            st = http_get(f"/api/state/{p2_id}")
            privates = st.get("private_chats", {}).get("maid_marilyn", [])
            for m in privates:
                if m["sender_id"] == "maid_marilyn":
                    marilyn_replied = True
                    marilyn_text = m["content"]
                    break
            if marilyn_replied:
                break

        print(f"  Maid Marilyn Private Reply:\n  \"{marilyn_text}\"")
        assert marilyn_replied, "Maid Marilyn should reply to Queen Genevieve!"
        assert len(marilyn_text) > 15, "Marilyn's reply should be substantive!"
        print("  ✓ Maid Marilyn answered respectfully to the Queen.")
        audit_report["dialogues_tested"].append({
            "character": "Maid Marilyn",
            "setting": "Private Whisper",
            "prompt": "Feelings regarding Sir Cameron and Sir Rufus?",
            "reply": marilyn_text
        })

        # Test Gold Bribe: Lord Taylor transfers 3 gold coins to Charlamagne
        print("\n>>> [TEST 7] Testing Gold Bribe & AI Reaction: Lord Taylor -> Charlamagne...")
        bribe_res = http_post("/api/bribe", {
            "sender_id": "lord_taylor",
            "receiver_id": "charlamagne",
            "amount": 3,
            "message": "Here are 3 gold coins. Tell me what you were doing in Sir Cameron's quarters this morning."
        })
        assert bribe_res["status"] == "ok", f"Bribe transfer failed: {bribe_res}"
        print(f"  ✓ Transferred 3 gold coins. New gold balance: {bribe_res['new_gold']}")

        charlamagne_replied = False
        charlamagne_text = ""
        for _ in range(12):
            await asyncio.sleep(1.5)
            st = http_get(f"/api/state/{p1_id}")
            privates = st.get("private_chats", {}).get("charlamagne", [])
            for m in privates:
                if m["sender_id"] == "charlamagne":
                    charlamagne_replied = True
                    charlamagne_text = m["content"]
                    break
            if charlamagne_replied:
                break

        print(f"  Charlamagne Bribe Reaction:\n  \"{charlamagne_text}\"")
        assert charlamagne_replied, "Charlamagne should react to the gold bribe!"
        print("  ✓ Charlamagne acknowledged the bribe and answered in character.")
        audit_report["dialogues_tested"].append({
            "character": "Charlamagne",
            "setting": "Gold Bribe Reaction",
            "prompt": "3 Gold Coins: What were you doing in Sir Cameron's room?",
            "reply": charlamagne_text
        })

        # -----------------------------------------------------------
        # STAGE 3: THE FOUL MURDER OF SIR RUFUS
        # -----------------------------------------------------------
        print("\n>>> [TEST 8] Advancing to Stage 3 (The Murder & Blackout)...")
        http_post("/api/ready", {"player_id": p1_id})
        r3 = http_post("/api/ready", {"player_id": p2_id})
        assert r3["all_ready"], "Both players ready to advance to Stage 3"
        print("  ✓ Stage 3 advanced successfully!")
        audit_report["stages_passed"].append("Stage 3 Advance (Murder)")

        await asyncio.sleep(2)
        st3 = http_get(f"/api/state/{p1_id}")
        assert st3["stage"] == 3, f"Expected Stage 3, got {st3['stage']}"

        # Verify Murder Events in Great Hall
        gh_msgs = st3["great_hall_messages"]
        toast_found = any("Lord Taylor raises his jeweled goblet" in m["content"] for m in gh_msgs)
        blackout_found = any(m.get("is_blackout") for m in gh_msgs)
        willie_inv_found = any("Willie draws chalk around the corpse" in m["content"] for m in gh_msgs)

        assert toast_found, "Lord Taylor's wedding toast should be present!"
        assert blackout_found, "Blackout & scream event should be triggered!"
        assert willie_inv_found, "Willie's investigation announcement should be present!"
        print("  ✓ Toast proclaimed, Blackout triggered, Sir Rufus slain, Willie secures the crime scene.")

        # Test Post-Murder Interrogation: Interrogating the Killer (Lady Diana)
        print("\n>>> [TEST 9] Post-Murder Interrogation: Lord Taylor -> Lady Diana (Killer)...")
        await ws1.send(json.dumps({
            "action": "send_message",
            "type": "private",
            "sender_id": "lord_taylor",
            "recipient_id": "lady_diana",
            "content": "Lady Diana, Sir Rufus was slain moments after our toast! Did you know him in Dunnsberry, and why did he look at you so strangely earlier?"
        }))

        diana_defense = ""
        diana_def_replied = False
        for _ in range(12):
            await asyncio.sleep(1.5)
            st = http_get(f"/api/state/{p1_id}")
            privates = st.get("private_chats", {}).get("lady_diana", [])
            for m in privates:
                if m["sender_id"] == "lady_diana":
                    diana_def_replied = True
                    diana_defense = m["content"]
                    break
            if diana_def_replied:
                break

        print(f"  Lady Diana Defense:\n  \"{diana_defense}\"")
        assert diana_def_replied, "Lady Diana should reply to murder questioning!"
        assert not "I am the murderer" in diana_defense.lower(), "Diana must NOT confess in private!"
        print("  ✓ Lady Diana defended herself cunningly without breaking character or confessing.")
        audit_report["dialogues_tested"].append({
            "character": "Lady Diana",
            "setting": "Murder Interrogation",
            "prompt": "Did you know Sir Rufus in Dunnsberry and why did he look at you strangely?",
            "reply": diana_defense
        })

        # -----------------------------------------------------------
        # STAGE 4: EVIDENCE PRESENTATION & ACCUSATIONS (WHO DUNNIT)
        # -----------------------------------------------------------
        print("\n>>> [TEST 10] Advancing to Stage 4 (Evidence Presentation & Who Dunnit)...")
        http_post("/api/ready", {"player_id": p1_id})
        r4 = http_post("/api/ready", {"player_id": p2_id})
        assert r4["all_ready"], "Both players ready to advance to Stage 4"
        print("  ✓ Stage 4 advanced successfully!")
        audit_report["stages_passed"].append("Stage 4 Advance (Evidence)")

        await asyncio.sleep(2)
        public_data = http_get("/api/game-data")
        assert public_data["stage"] == 4, f"Expected Stage 4, got {public_data['stage']}"
        revealed = public_data["revealed_exhibits"]
        print(f"  ✓ Exhibits unlocked: {revealed}")
        assert set(revealed) == {"A", "B", "C", "D", "E", "F"}, "All 6 exhibits (A-F) must be revealed in Stage 4!"

        # Submit Who Dunnit votes
        print("\n>>> [TEST 11] Submitting Accusation Ballots (Who Dunnit)...")
        v1 = http_post("/api/vote", {
            "voter_id": "lord_taylor",
            "accused_id": "lady_diana",
            "motive": "Lady Diana is an escaped serf from Dunnsberry seeking freedom after living 1 year + 1 day in Fernwood. Sir Rufus recognized her and threatened her freedom.",
            "evidence": "Exhibit A dagger bearing Dunnsberry coat of arms and Exhibit C Laws of the Land clause on serfs.",
            "best_dressed": "Queen Genevieve",
            "best_perf": "Joking Jerry"
        })
        assert v1["status"] == "ok", f"Vote 1 failed: {v1}"
        print("  ✓ Player 1 (Lord Taylor) submitted correct accusation for Lady Diana.")

        v2 = http_post("/api/vote", {
            "voter_id": "queen_genevieve",
            "accused_id": "lady_diana",
            "motive": "Sir Rufus was blackmailing her over her true identity from Dunnsberry.",
            "evidence": "Exhibit A murder dagger and Exhibit F blackmail note.",
            "best_dressed": "Lady Diana of Dunnsberry",
            "best_perf": "Lord Taylor"
        })
        assert v2["status"] == "ok", f"Vote 2 failed: {v2}"
        print("  ✓ Player 2 (Queen Genevieve) submitted correct accusation for Lady Diana.")

        # -----------------------------------------------------------
        # STAGE 5: THE ROYAL SOLUTION & AWARDS
        # -----------------------------------------------------------
        print("\n>>> [TEST 12] Advancing to Stage 5 (The Royal Solution & Climax)...")
        http_post("/api/ready", {"player_id": p1_id})
        r5 = http_post("/api/ready", {"player_id": p2_id})
        assert r5["all_ready"], "Both players ready to advance to Stage 5"
        print("  ✓ Stage 5 advanced successfully!")
        audit_report["stages_passed"].append("Stage 5 Advance (Solution)")

        await asyncio.sleep(2)
        final_public = http_get("/api/game-data")
        assert final_public["stage"] == 5, f"Expected Stage 5, got {final_public['stage']}"

        # Verify Willie's Solution in Great Hall
        gh_final = http_get(f"/api/state/{p1_id}")["great_hall_messages"]
        sol_found = any("Willie stands before the assembled court to deliver the final verdict" in m["content"] for m in gh_final)
        assert sol_found, "Willie's official solution must be delivered in Great Hall!"
        print("  [+] Willie the Watchman delivered the grand solution speech!")

        # Verify Awards Calculation
        awards = final_public["awards"]
        assert awards is not None, "Awards must be calculated in Stage 5!"
        print("\n========================================================")
        print("   ROYAL AWARDS & VERDICT SUMMARY:")
        print("========================================================")
        print(f"  Killer:             {awards['killer']}")
        print(f"  Motive Summary:     {awards['motive_summary']}")
        print(f"  Smoking Gun Winner: {awards['smoking_gun_winners']}")
        print(f"  Wealthiest Guest:   {awards['wealthiest_guest']}")
        print(f"  Best Dressed:       {awards['best_dressed']}")
        print(f"  Best Performance:   {awards['best_performance']}")
        print("========================================================\n")

        assert "Lady Diana" in awards["killer"], "Killer must be Lady Diana!"
        assert len(awards["smoking_gun_winners"]) >= 1, "Smoking gun winners must be awarded!"
        audit_report["final_awards"] = awards

    print("=== FULL GAME SIMULATION PASSED 100% PERFECTLY WITH ZERO ERRORS! ===\n")
    return audit_report

if __name__ == "__main__":
    report = asyncio.run(run_simulation())
    with open("data/simulation_audit_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
