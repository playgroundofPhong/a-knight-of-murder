import os
import json
import time
from datetime import datetime
import logging
from typing import Dict, List, Any, Optional, Set

logger = logging.getLogger("game_state")

STAGE_TITLES = {
    1: "Giai Đoạn 1: Đón Tiếp Khách Mời (Phong Bì A)",
    2: "Giai Đoạn 2: Khai Mạc Đại Yến & Giao Lưu",
    3: "Giai Đoạn 3: Án Mạng Sir Rufus (Phong Bì B)",
    4: "Giai Đoạn 4: Công Bố Tang Vật & Bỏ Phiếu Kết Án",
    5: "Giai Đoạn 5: Phán Quyết Triều Đình & Vạch Trần Hung Thủ"
}

class GameStateManager:
    def __init__(self, data_file: str = "data/game_data.json", state_file: str = "data/room_state.json"):
        self.data_file = data_file
        self.state_file = state_file
        self.raw_data: Dict[str, Any] = {}
        self.load_raw_data()

        # State fields
        self.room_code = "FERNWOOD"
        self.cast_size = 12 # 8 or 12
        self.stage = 1
        self.is_game_started = False
        self.characters: Dict[str, Dict[str, Any]] = {}
        self.human_players: Dict[str, Dict[str, Any]] = {} # player_id -> {id, name, character_id, ready}
        self.great_hall_messages: List[Dict[str, Any]] = []
        self.private_chats: Dict[str, List[Dict[str, Any]]] = {} # "id1__id2" -> [msg]
        self.gold_transactions: List[Dict[str, Any]] = []
        self.revealed_exhibits: Set[str] = set()
        self.votes: Dict[str, Dict[str, Any]] = {}
        self.awards: Optional[Dict[str, Any]] = None
        self.api_key: str = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        self.llm_provider: str = "gemini" if (os.getenv("GEMINI_API_KEY") or not os.getenv("OPENAI_API_KEY")) else "openai"

        # Initialize characters from raw data
        self.init_characters()

        # Try to restore persisted state if available
        self.load_state()

    def load_raw_data(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, "r", encoding="utf-8") as f:
                self.raw_data = json.load(f)
        else:
            raise FileNotFoundError(f"Missing {self.data_file}")

    def init_characters(self):
        for c in self.raw_data.get("characters", []):
            cid = c["id"]
            self.characters[cid] = {
                **c,
                "current_gold": c.get("coins", 12),
                "is_human": False,
                "player_id": None,
                "checked_objectives": []
            }

    def reset(self):
        """Reset the game state to stage 1, clearing players, messages, votes, awards."""
        self.stage = 1
        self.is_game_started = False
        self.human_players = {}
        self.great_hall_messages = []
        self.private_chats = {}
        self.gold_transactions = []
        self.revealed_exhibits = set()
        self.votes = {}
        self.awards = None
        self.init_characters()
        self.save_state()
        logger.info("Game state has been reset.")

    def set_cast_size(self, size: int):
        self.cast_size = 8 if size == 8 else 12
        self.save_state()

    def get_active_characters(self) -> List[Dict[str, Any]]:
        """Return list of active characters based on cast size."""
        all_chars = list(self.characters.values())
        if self.cast_size == 8:
            # 8 character list excludes Jerry, Gwendolyn, Monica, and Charlamagne or Willie
            # Active 8: Queen Genevieve, Lord Taylor, Baron Bartholomew, Lady Diana, Maid Marilyn, Sir Cameron, Sir Rufus, Willie the Watchman
            keep_ids = {"queen_genevieve", "lord_taylor", "baron_bartholomew", "lady_diana", "maid_marilyn", "sir_cameron", "sir_rufus", "willie_watchman"}
            return [c for c in all_chars if c["id"] in keep_ids]
        return all_chars

    def register_human_player(self, player_id: str, player_name: str, character_id: str) -> bool:
        """Register or update a human player's chosen character."""
        # Check if character is available and not Sir Rufus (locked to AI)
        if character_id == "sir_rufus":
            return False

        # If another human player has this character, disallow
        for pid, pdata in self.human_players.items():
            if pid != player_id and pdata.get("character_id") == character_id:
                return False

        # Release previous character if any
        if player_id in self.human_players:
            old_cid = self.human_players[player_id].get("character_id")
            if old_cid and old_cid in self.characters:
                self.characters[old_cid]["is_human"] = False
                self.characters[old_cid]["player_id"] = None

        self.human_players[player_id] = {
            "player_id": player_id,
            "player_name": player_name,
            "character_id": character_id,
            "ready_for_next_stage": False
        }

        if character_id in self.characters:
            self.characters[character_id]["is_human"] = True
            self.characters[character_id]["player_id"] = player_id

        self.save_state()
        return True

    def start_game(self):
        self.is_game_started = True
        self.stage = 1
        # Add welcome announcement from Royal Herald
        intro_announcement = {
            "id": f"msg_{int(time.time()*1000)}",
            "sender_id": "game_master",
            "sender_name": "Quản Trò Hoàng Gia",
            "sender_avatar": "🎺",
            "content": (
                "Hỡi các Lãnh chúa và Phu nhân tôn kính! Chào mừng quý vị đã đến với Lâu Đài Fernwood! "
                "Giải đấu thương mã vừa khép lại, và các vị khách quý đã tề tựu đông đủ tại Đại Sảnh. "
                "Xin hãy xem Phong Bì A trong Hồ Sơ Bí Mật, tuân thủ Luật Lệ Lâu Đài và chuẩn bị sẵn sàng tâm trí!"
            ),
            "timestamp": time.time(),
            "type": "announcement"
        }
        self.great_hall_messages.append(intro_announcement)
        self.save_state()

    def toggle_player_ready(self, player_id: str) -> bool:
        """Toggle player ready status. Return True if all human players are ready."""
        if player_id in self.human_players:
            cur = self.human_players[player_id].get("ready_for_next_stage", False)
            self.human_players[player_id]["ready_for_next_stage"] = not cur
            self.save_state()

        # Check if all human players are ready
        if len(self.human_players) > 0 and all(p.get("ready_for_next_stage", False) for p in self.human_players.values()):
            return True
        return False

    def advance_stage(self) -> Dict[str, Any]:
        """Advance game to next stage, resetting ready flags and posting dramatic events."""
        if self.stage >= 5:
            return {"advanced": False, "stage": self.stage}

        self.stage += 1
        for p in self.human_players.values():
            p["ready_for_next_stage"] = False

        event_data = {"advanced": True, "new_stage": self.stage, "title": STAGE_TITLES[self.stage]}

        # Stage specific scripted events
        if self.stage == 2:
            # Stage 2: Banquet opens, Lord Taylor delivers Introduction
            intro_speech = self.raw_data.get("speeches", {}).get("introduction", "")
            msg = {
                "id": f"msg_{int(time.time()*1000)}",
                "sender_id": "lord_taylor",
                "sender_name": "Lãnh chúa Taylor",
                "sender_avatar": "🏰",
                "content": f"[Lãnh chúa Taylor bước lên bục cao, mở cuộn da dê và phát biểu khai mạc đại yến]:\n\n{intro_speech}",
                "timestamp": time.time(),
                "type": "event"
            }
            self.great_hall_messages.append(msg)

        elif self.stage == 3:
            # Stage 3: Murder! Lord Taylor's toast -> Blackout -> Sir Rufus slain -> Willie's announcement
            toast_speech = self.raw_data.get("speeches", {}).get("toast", "")
            toast_msg = {
                "id": f"msg_{int(time.time()*1000)}_1",
                "sender_id": "lord_taylor",
                "sender_name": "Lãnh chúa Taylor",
                "sender_avatar": "🏰",
                "content": f"[Lãnh chúa Taylor nâng cao chiếc ly nạm ngọc chúc mừng hôn lễ]:\n\n\"{toast_speech}\"",
                "timestamp": time.time(),
                "type": "event"
            }
            blackout_msg = {
                "id": f"msg_{int(time.time()*1000)}_2",
                "sender_id": "system",
                "sender_name": "BÓNG ĐÊM CHẾT CHÓC",
                "sender_avatar": "⚡",
                "content": (
                    "⚡ TIẾNG SẤM NỔ VANG RỀN! TOÀN BỘ ÁNH NẾN VÀ ĐUỐC PHỤT TẮT! "
                    "BÓNG ĐÊM ĐEN KỊT BAO TRÙM ĐẠI SẢNH! MỘT TIẾNG THÉT KINH HOÀNG VANG LÊN XÉ TOẠC MÀN ĐÊM!\n\n"
                    "Khi ánh đuốc bừng sáng trở lại... Hiệp sĩ Rufus đã nằm bất động trên sàn đá lạnh lẽo, "
                    "một thanh đoản kiếm nạm ngọc đâm xuyên qua áo giáp vào thẳng tim! HIỆP SĨ RUFUS ĐÃ BỊ SÁT HẠI!"
                ),
                "timestamp": time.time() + 1,
                "type": "event",
                "is_blackout": True
            }
            inv_speech = self.raw_data.get("speeches", {}).get("investigation", "")
            willie_msg = {
                "id": f"msg_{int(time.time()*1000)}_3",
                "sender_id": "willie_watchman",
                "sender_name": "Willie the Watchman",
                "sender_avatar": "🗝️",
                "content": f"[Willie vẽ phấn quanh thi thể, rút gậy sắt và ra lệnh phong tỏa lâu đài]:\n\n{inv_speech}",
                "timestamp": time.time() + 2,
                "type": "event"
            }
            self.great_hall_messages.extend([toast_msg, blackout_msg, willie_msg])
            event_data["is_blackout"] = True

        elif self.stage == 4:
            # Stage 4: Willie presents Exhibits A through F
            ev_speech = self.raw_data.get("speeches", {}).get("evidence_presentation", "")
            msg = {
                "id": f"msg_{int(time.time()*1000)}",
                "sender_id": "willie_watchman",
                "sender_name": "Willie the Watchman",
                "sender_avatar": "🗝️",
                "content": f"[Willie bước lên và trải các tang vật A đến F lên bàn đại yến]:\n\n{ev_speech}",
                "timestamp": time.time(),
                "type": "event"
            }
            self.great_hall_messages.append(msg)
            # Reveal all exhibits A to F
            self.revealed_exhibits = {"A", "B", "C", "D", "E", "F"}

        elif self.stage == 5:
            # Stage 5: Solution reveal & calculate awards
            sol_speech = self.raw_data.get("speeches", {}).get("solution", "")
            msg = {
                "id": f"msg_{int(time.time()*1000)}",
                "sender_id": "willie_watchman",
                "sender_name": "Willie the Watchman",
                "sender_avatar": "🗝️",
                "content": f"[Willie đứng trước toàn thể triều đình và dõng dạc tuyên đọc lời giải chính thức]:\n\n{sol_speech}",
                "timestamp": time.time(),
                "type": "event"
            }
            self.great_hall_messages.append(msg)
            self.calculate_awards()

        self.save_state()
        return event_data

    def transfer_gold(self, sender_cid: str, receiver_cid: str, amount: int, message: str) -> bool:
        """Transfer gold coins from one character to another."""
        if sender_cid not in self.characters or receiver_cid not in self.characters:
            return False
        if amount <= 0 or self.characters[sender_cid]["current_gold"] < amount:
            return False

        self.characters[sender_cid]["current_gold"] -= amount
        self.characters[receiver_cid]["current_gold"] += amount

        record = {
            "from_id": sender_cid,
            "from_name": self.characters[sender_cid]["name"],
            "to_id": receiver_cid,
            "to_name": self.characters[receiver_cid]["name"],
            "amount": amount,
            "message": message,
            "timestamp": time.time()
        }
        self.gold_transactions.append(record)
        self.save_state()
        return True

    def get_transactions_between(self, cid1: str, cid2: str) -> List[Dict[str, Any]]:
        """Return all gold transactions between two specific characters."""
        return [
            t for t in self.gold_transactions
            if (t.get("from_id") == cid1 and t.get("to_id") == cid2)
            or (t.get("from_id") == cid2 and t.get("to_id") == cid1)
        ]

    def submit_vote(self, voter_id: str, accused_id: str, motive: str, evidence: str, best_dressed: str, best_perf: str):
        self.votes[voter_id] = {
            "voter_id": voter_id,
            "accused_id": accused_id,
            "motive": motive,
            "evidence": evidence,
            "best_dressed": best_dressed,
            "best_performance": best_perf,
            "timestamp": time.time()
        }
        self.save_state()

    def calculate_awards(self):
        """Calculate final awards based on votes and game state."""
        correct_accusers = []
        for vid, vdata in self.votes.items():
            if vdata.get("accused_id") == "lady_diana":
                voter_name = self.characters.get(vid, {}).get("name", vid)
                for pid, pdata in self.human_players.items():
                    if pid == vid or pdata.get("character_id") == vid:
                        voter_name = f"{pdata['player_name']} ({self.characters.get(pdata['character_id'], {}).get('name')})"
                correct_accusers.append(voter_name)

        # Wealthiest guest
        active_chars = self.get_active_characters()
        wealthiest = sorted(active_chars, key=lambda c: c["current_gold"], reverse=True)
        top_wealth = wealthiest[0] if wealthiest else None

        # Tally Best Dressed & Best Performance
        dress_tally: Dict[str, int] = {}
        perf_tally: Dict[str, int] = {}
        for v in self.votes.values():
            d = v.get("best_dressed")
            if d:
                dress_tally[d] = dress_tally.get(d, 0) + 1
            p = v.get("best_performance")
            if p:
                perf_tally[p] = perf_tally.get(p, 0) + 1

        top_dressed = max(dress_tally, key=dress_tally.get) if dress_tally else "Lady Diana of Dunnsberry"
        top_perf = max(perf_tally, key=perf_tally.get) if perf_tally else "Joking Jerry"

        self.awards = {
            "killer": "Lady Diana of Dunnsberry",
            "motive_summary": "Diana is an escaped serf from Dunnsberry seeking freedom after living 1 year + 1 day in Fernwood. Sir Rufus recognized her and threatened her freedom.",
            "smoking_gun_winners": correct_accusers if correct_accusers else ["No one guessed correctly! Lady Diana's web of deception held!"],
            "wealthiest_guest": f"{top_wealth['name']} ({top_wealth['current_gold']} Gold Coins)" if top_wealth else "Unknown",
            "best_dressed": top_dressed,
            "best_performance": top_perf
        }
        self.save_state()

    def get_private_chat_key(self, cid1: str, cid2: str) -> str:
        pair = sorted([cid1, cid2])
        return f"{pair[0]}__{pair[1]}"

    def add_message(self, sender_id: str, content: str, chat_type: str = "public", recipient_id: Optional[str] = None, is_undercover: bool = False) -> Dict[str, Any]:
        """Add a public or private message."""
        sender_char = self.characters.get(sender_id)
        sender_name = sender_char["name"] if sender_char else sender_id
        sender_avatar = sender_char.get("avatar_icon", "👤") if sender_char else "👤"

        msg = {
            "id": f"msg_{int(time.time()*1000)}",
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_avatar": sender_avatar,
            "content": content,
            "timestamp": time.time(),
            "type": chat_type,
            "recipient_id": recipient_id,
            "is_undercover": is_undercover
        }

        if chat_type == "public":
            self.great_hall_messages.append(msg)
        else:
            if recipient_id:
                key = self.get_private_chat_key(sender_id, recipient_id)
                if key not in self.private_chats:
                    self.private_chats[key] = []
                self.private_chats[key].append(msg)

        self.save_state()
        self.save_chatlog_to_disk()
        return msg

    def get_chatlog_data(self, player_id: Optional[str] = None, is_host: bool = False) -> Dict[str, Any]:
        """Returns structured chat log categorized into Great Hall, Human Whispers, AI Undercover Whispers, Ledger, and Votes.
        
        If is_host is True or game has concluded (stage >= 5), full court chronicles are unlocked.
        If a regular player views this during stages 1-4, AI Undercover Whispers and other players' private matters are sealed.
        """
        can_view_all = is_host or (self.stage >= 5)

        my_cid = None
        if player_id and player_id in self.human_players:
            my_cid = self.human_players[player_id].get("character_id")

        human_cids = {p.get("character_id") for p in self.human_players.values() if p.get("character_id")}

        human_whispers = {}
        ai_undercover_whispers = {}
        total_undercover_count = 0

        for chat_key, msgs in self.private_chats.items():
            parts = chat_key.split("__")
            if len(parts) != 2:
                continue
            c1, c2 = parts[0], parts[1]
            c1_name = self.characters.get(c1, {}).get("name", c1)
            c2_name = self.characters.get(c2, {}).get("name", c2)
            c1_avatar = self.characters.get(c1, {}).get("avatar_icon", "👤")
            c2_avatar = self.characters.get(c2, {}).get("avatar_icon", "👤")
            pair_label = f"{c1_name} ↔ {c2_name}"

            is_ai_undercover = (c1 not in human_cids and c2 not in human_cids)

            chat_info = {
                "chat_key": chat_key,
                "pair": [c1, c2],
                "pair_names": [c1_name, c2_name],
                "pair_avatars": [c1_avatar, c2_avatar],
                "pair_label": pair_label,
                "is_ai_undercover": is_ai_undercover,
                "messages": msgs
            }

            if is_ai_undercover:
                total_undercover_count += 1
                if can_view_all:
                    ai_undercover_whispers[chat_key] = chat_info
            else:
                if can_view_all or (my_cid and (my_cid == c1 or my_cid == c2)):
                    human_whispers[chat_key] = chat_info

        # Transactions
        if can_view_all:
            transactions = self.gold_transactions
        elif my_cid:
            transactions = [
                tx for tx in self.gold_transactions 
                if tx.get("from_id") == my_cid or tx.get("to_id") == my_cid
            ]
        else:
            transactions = []

        # Votes & Awards
        if can_view_all:
            votes = self.votes
            awards = self.awards
        else:
            votes = {k: v for k, v in self.votes.items() if (player_id and k == player_id) or (my_cid and k == my_cid)}
            awards = {} if self.stage < 5 else self.awards

        return {
            "room_code": self.room_code,
            "stage": self.stage,
            "stage_title": STAGE_TITLES.get(self.stage, ""),
            "cast_size": self.cast_size,
            "can_view_all": can_view_all,
            "is_host": is_host,
            "my_character_id": my_cid,
            "total_undercover_count": total_undercover_count,
            "great_hall": self.great_hall_messages,
            "human_whispers": human_whispers,
            "ai_undercover_whispers": ai_undercover_whispers,
            "transactions": transactions,
            "votes": votes,
            "awards": awards,
            "human_players": self.human_players
        }

    def export_chatlog_text(self, player_id: Optional[str] = None, is_host: bool = False) -> str:
        """Format the game session into a readable chronicle with appropriate permission seals."""
        data = self.get_chatlog_data(player_id=player_id, is_host=is_host)
        lines = []
        lines.append("=" * 80)
        lines.append("                   A KNIGHT OF MURDER - COURT CHRONICLES")
        lines.append("              Official Transcripts of Fernwood Manor Banquet")
        lines.append("=" * 80)
        lines.append(f"Mã Phòng:         {data['room_code']}")
        lines.append(f"Quy Mô Khách Mời: {data['cast_size']} Khách mời")
        lines.append(f"Giai Đoạn Hiện Tại: Giai đoạn {data['stage']} - {STAGE_TITLES.get(data['stage'], '')}")
        lines.append(f"Cấp Quyền:       {'👑 Quản Trò / Chủ Phòng (Toàn Tri)' if data['can_view_all'] else '👤 Khách Mời (Bảo mật)'}")
        lines.append(f"Thời Gian Xuất:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        lines.append("[DANH SÁCH KHÁCH MỜI & NGƯỜI CHƠI]")
        for pid, pdata in self.human_players.items():
            cid = pdata.get("character_id")
            cname = self.characters.get(cid, {}).get("name", "Chưa rõ")
            lines.append(f"  • {cname} (Điều khiển bởi Người chơi: {pdata.get('player_name', pid)})")
        for cid, cinfo in self.characters.items():
            if not cinfo.get("is_human"):
                lines.append(f"  • {cinfo['name']} ({cinfo['title']}) [Tác Nhân AI]")
        lines.append("")

        # 1. Great Hall
        lines.append("-" * 80)
        lines.append("                     I. ĐẠI SẢNH (BIÊN NIÊN SỬ ĐẠI YẾN)")
        lines.append("-" * 80)
        if not data["great_hall"]:
            lines.append("  (Chưa có lời phát biểu nào tại Đại Sảnh.)")
        else:
            for m in data["great_hall"]:
                t_str = datetime.fromtimestamp(m.get("timestamp", time.time())).strftime("%H:%M:%S")
                s_name = m.get("sender_name", "Ẩn danh")
                m_type = m.get("type", "public")
                if m_type in ["announcement", "event"]:
                    lines.append(f"\n[{t_str}] [TUYÊN BỐ - {s_name}]:")
                    lines.append(f"  {m.get('content')}\n")
                else:
                    lines.append(f"[{t_str}] {s_name}: {m.get('content')}")
        lines.append("")

        # 2. AI Undercover Whispers
        lines.append("-" * 80)
        lines.append("         II. MẬT ĐÀM NGẦM GIỮA CÁC AI (GIAO LƯU BÍ MẬT NỘI BỘ LÂU ĐÀI)")
        lines.append("              [Hội thoại riêng tư được trao đổi ngầm giữa các cư dân AI]")
        lines.append("-" * 80)
        if not data["can_view_all"]:
            lines.append("  🔒 [NIÊM PHONG SÁP ĐỎ HOÀNG GIA - TÀI LIỆU BẢO MẬT]")
            lines.append("  Các trao đổi ngầm giữa các nhân vật lâu đài được niêm phong để bảo đảm tính")
            lines.append("  công bằng và bất ngờ kịch tính trong suốt quá trình điều tra phá án.")
            lines.append("  -> Chỉ có Quản Trò xem được, hoặc mở niêm phong toàn thể tại Giai đoạn 5.")
        elif not data["ai_undercover_whispers"]:
            lines.append("  (Chưa có cuộc gặp gỡ bí mật nào giữa các AI.)")
        else:
            for k, info in data["ai_undercover_whispers"].items():
                lines.append(f"\n>>> Phòng Kín: {info['pair_label']}")
                for m in info["messages"]:
                    t_str = datetime.fromtimestamp(m.get("timestamp", time.time())).strftime("%H:%M:%S")
                    s_name = m.get("sender_name", "Chưa rõ")
                    lines.append(f"  [{t_str}] {s_name}: {m.get('content')}")
        lines.append("")

        # 3. Private Whispers
        lines.append("-" * 80)
        lines.append("              III. MẬT ĐÀM RIÊNG (PHÒNG KÍN CỦA NGƯỜI CHƠI)")
        lines.append("-" * 80)
        if not data["human_whispers"]:
            lines.append("  (Chưa có mật đàm riêng nào được ghi nhận.)")
        else:
            for k, info in data["human_whispers"].items():
                lines.append(f"\n>>> Phòng Riêng: {info['pair_label']}")
                for m in info["messages"]:
                    t_str = datetime.fromtimestamp(m.get("timestamp", time.time())).strftime("%H:%M:%S")
                    s_name = m.get("sender_name", "Chưa rõ")
                    lines.append(f"  [{t_str}] {s_name}: {m.get('content')}")
        lines.append("")

        # 4. Treasury Ledger
        lines.append("-" * 80)
        lines.append("                   IV. SỔ CÁI NGÂN KHỐ (TIỀN VÀNG & HỐI LỘ)")
        lines.append("-" * 80)
        if not data["transactions"]:
            lines.append("  (Chưa có giao dịch tiền vàng nào.)")
        else:
            for tx in data["transactions"]:
                t_str = datetime.fromtimestamp(tx.get("timestamp", time.time())).strftime("%H:%M:%S")
                lines.append(f"  [{t_str}] {tx['from_name']} -> {tx['to_name']}: {tx['amount']} Đồng Vàng | Ghi chú: \"{tx.get('message', '')}\"")
        lines.append("")

        # 5. Votes & Solution
        lines.append("-" * 80)
        lines.append("                    V. PHIẾU KẾT ÁN & DANH HIỆU CHUNG CUỘC")
        lines.append("-" * 80)
        if not data["votes"]:
            lines.append("  (Chưa có phiếu kết án nào được nộp.)")
        else:
            for vid, v in data["votes"].items():
                voter_name = self.characters.get(vid, {}).get("name", vid)
                accused_name = self.characters.get(v.get("accused_id"), {}).get("name", v.get("accused_id"))
                lines.append(f"  • {voter_name} cáo buộc {accused_name}: Động cơ: {v.get('motive')} | Tang vật: {v.get('evidence')}")

        if data["awards"]:
            lines.append("\n[KẾT QUẢ PHÁN QUYẾT & GIẢI THƯỞNG]")
            lines.append(f"  Hung Thủ Thực Sự:         {data['awards'].get('killer')}")
            lines.append(f"  Tóm Tắt Động Cơ:          {data['awards'].get('motive_summary')}")
            lines.append(f"  Thám Tử Phá Án Xuất Sắc:  {', '.join(data['awards'].get('smoking_gun_winners', []))}")
            lines.append(f"  Người Giàu Có Nhất:       {data['awards'].get('wealthiest_guest')}")
            lines.append(f"  Trang Phục Lộng Lẫy Nhất: {data['awards'].get('best_dressed')}")
            lines.append(f"  Diễn Xuất Ấn Tượng Nhất:  {data['awards'].get('best_performance')}")

        lines.append("\n" + "=" * 80)
        lines.append("                     KẾT THÚC BIÊN NIÊN SỬ TRIỀU ĐÌNH")
        lines.append("=" * 80)
        return "\n".join(lines)

    def save_chatlog_to_disk(self):
        try:
            os.makedirs("chatlogs", exist_ok=True)
            text_chronicle = self.export_chatlog_text()
            with open("chatlogs/chatlog_latest.txt", "w", encoding="utf-8") as f:
                f.write(text_chronicle)

            with open("chatlogs/chatlog_latest.json", "w", encoding="utf-8") as f:
                json.dump(self.get_chatlog_data(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving chatlog to disk: {e}")

    def save_state(self):
        state = {
            "room_code": self.room_code,
            "cast_size": self.cast_size,
            "stage": self.stage,
            "is_game_started": self.is_game_started,
            "characters": self.characters,
            "human_players": self.human_players,
            "great_hall_messages": self.great_hall_messages,
            "private_chats": self.private_chats,
            "gold_transactions": self.gold_transactions,
            "revealed_exhibits": list(self.revealed_exhibits),
            "votes": self.votes,
            "awards": self.awards,
            "api_key": "",
            "llm_provider": self.llm_provider
        }
        try:
            if os.path.dirname(self.state_file):
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    self.room_code = state.get("room_code", "FERNWOOD")
                    self.cast_size = state.get("cast_size", 12)
                    self.stage = state.get("stage", 1)
                    self.is_game_started = state.get("is_game_started", False)
                    if "characters" in state:
                        self.characters = state["characters"]
                    if "human_players" in state:
                        self.human_players = state["human_players"]
                    if "great_hall_messages" in state:
                        self.great_hall_messages = state["great_hall_messages"]
                    if "private_chats" in state:
                        self.private_chats = state["private_chats"]
                    if "gold_transactions" in state:
                        self.gold_transactions = state["gold_transactions"]
                    if "revealed_exhibits" in state:
                        self.revealed_exhibits = set(state["revealed_exhibits"])
                    if "votes" in state:
                        self.votes = state["votes"]
                    if "awards" in state:
                        self.awards = state["awards"]
                    if state.get("api_key"):
                        self.api_key = state["api_key"]
                    if state.get("llm_provider"):
                        self.llm_provider = state["llm_provider"]
                    logger.info("Restored persisted game state.")
            except Exception as e:
                logger.error(f"Error loading state: {e}")
