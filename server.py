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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel

from game_state import GameStateManager
from ai_engine import AIEngine, build_character_system_prompt, build_host_system_prompt, build_herald_hint_prompt

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

# Active WebSocket connections: player_id -> Set[WebSocket]
connected_websockets: Dict[str, set] = {}

CHARACTER_ALIASES = {
    "queen_genevieve": ["queen genevieve", "genevieve", "queen", "her majesty", "the queen", "nữ hoàng genevieve", "nữ hoàng", "hoàng hậu", "bệ hạ"],
    "lord_taylor": ["lord taylor", "taylor", "lord", "host", "the lord", "lãnh chúa taylor", "lãnh chúa", "chủ tiệc"],
    "lady_gwendolyn": ["lady gwendolyn", "gwendolyn", "lady", "phu nhân gwendolyn", "quý bà gwendolyn", "quý cô gwendolyn"],
    "baron_bartholomew": ["baron bartholomew", "bartholomew", "baron", "groom", "the baron", "nam tước bartholomew", "nam tước", "chú rể"],
    "lady_diana": ["lady diana of dunnsberry", "lady diana", "diana", "bride", "dunnsberry", "tiểu thư diana", "quý cô diana", "cô dâu"],
    "maid_marilyn": ["maid marilyn", "marilyn", "maid", "handkerchief", "hầu nữ marilyn", "marilyn"],
    "sir_cameron": ["sir cameron", "cameron", "knight", "hiệp sĩ cameron", "cameron"],
    "sir_rufus": ["sir rufus", "rufus", "victim", "champion", "hiệp sĩ rufus", "rufus", "nạn nhân"],
    "willie_watchman": ["willie the watchman", "willie", "watchman", "bailiff", "the watchman", "willie cai quản", "willie người canh gác", "willie", "cai quản", "người canh gác"],
    "charlamagne": ["charlamagne", "chambermaid", "hầu phòng charlamagne"],
    "maid_monica": ["maid monica", "monica", "hầu nữ monica"],
    "joking_jerry": ["joking jerry", "jerry", "jester", "fool", "the jester", "chú hề jerry", "hề jerry", "chú hề", "gã hề"],
}

def find_mentioned_character(content: str, active_chars: List[Dict[str, Any]], sender_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    content_lower = content.lower()

    def is_targetable_ai(c: Dict[str, Any]) -> bool:
        if sender_id and c["id"] == sender_id:
            return False
        # STRICT CONTROL: If character is claimed by a human, AI NEVER replies on their behalf!
        if game_state.is_character_human(c["id"]) or c.get("is_human"):
            return False
        if c.get("is_victim") and game_state.stage >= 3:
            return False
        return True

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
            if a in ["lord", "queen", "baron", "lady", "maid", "knight", "host", "bride", "groom", "victim", "fool", "nữ hoàng", "lãnh chúa", "nam tước", "tiểu thư", "hầu nữ", "hiệp sĩ", "chú hề"]:
                continue # Do not match generic titles without @
            if re.search(r'\b' + re.escape(a) + r'\b', content_lower):
                return c

    return None

async def broadcast(message_data: Dict[str, Any]):
    """Broadcast JSON message to all connected clients."""
    payload = json.dumps(message_data)
    disconnected = []
    for pid, sockets in list(connected_websockets.items()):
        for ws in list(sockets):
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append((pid, ws))
    for pid, ws in disconnected:
        if pid in connected_websockets:
            connected_websockets[pid].discard(ws)
            if not connected_websockets[pid]:
                connected_websockets.pop(pid, None)

DRAMA_TOPICS_STAGE_1_2 = [
    {
        "pair": ("sir_cameron", "sir_rufus"),
        "provocation": "Hiệp sĩ Rufus! Chiến thắng rỗng tuếch của ngươi trong hội thương kích bốc mùi lừa lọc bỉ ổi! Chiến mã của ta đột nhiên đổ bệnh ngay trước giờ xuất đấu, vậy mà ngươi dám trơ tráo đòi dải lụa thêu của Hầu nữ Marilyn!",
        "response_prompt": "Hiệp sĩ Cameron đang công khai đối đầu với bạn tại Đại Sảnh, tố cáo bạn đầu độc ngựa của hắn và đòi dải lụa của Marilyn. Hãy đáp trả đầy ngạo nghễ theo phong thái hiệp sĩ kiêu hãnh, viện dẫn Điều khoản 5b trong Luật Lệ Vương Triều."
    },
    {
        "pair": ("maid_monica", "baron_bartholomew"),
        "provocation": "Nam tước Bartholomew! Chàng nghĩ những chiếc nhẫn vàng và nhung lụa cưới có thể xóa sạch lời thề nguyền chúng ta từng trao dưới rặng liễu lâu đài sao? Yến tiệc xa hoa này chỉ là một sự phản bội được dát vàng mà thôi!",
        "response_prompt": "Hầu nữ Monica đang công khai chất vấn bạn tại Đại Sảnh về tình cảm xưa cũ và cuộc hôn nhân sắp đặt với Quý cô Diana. Hãy khuyên nàng giữ kín đáo bằng phong thái quý tộc dằn vặt, nhắc nàng rằng chiếu chỉ của phụ thân là luật pháp tại Fernwood."
    },
    {
        "pair": ("lady_gwendolyn", "charlamagne"),
        "provocation": "Con hầu Charlamagne kia! Ngươi lấy quyền gì mà lén lút lẻn ra khỏi tẩm phòng của Hiệp sĩ Cameron lúc trời chưa hửng sáng? Khai báo thật thà trước toàn thể chư vị tôn quý ở đây mau!",
        "response_prompt": "Phu nhân Gwendolyn đang gặng hỏi vì sao ngươi rời khỏi phòng Hiệp sĩ Cameron. Hãy khiêm nhường lảng tránh nhưng ngấm ngầm ẩn ý rằng một người hầu phòng nghe được rất nhiều bí mật động trời nơi hành lang đêm tối."
    },
    {
        "pair": ("maid_monica", "lady_diana"),
        "provocation": "Hãy nhìn tân nương vĩ đại đến từ Dunnsberry xem! Nói cho chúng ta hay, 'Tiểu thư' Diana, từ khi nào một quý cô dòng dõi quý tộc lại tự hào về việc nấu món hầm thô lậu của thường dân và tự tay cầm kim chỉ vá áo cưới vậy?",
        "response_prompt": "Hầu nữ Monica đang công khai nghi vấn xuất thân cao quý của bạn tại Đại Sảnh. Hãy tự vệ bằng phong thái thanh cao sắc sảo, gạt bỏ lời nàng ta như sự đố kỵ cay đắng của một kẻ bị ruồng bỏ."
    },
    {
        "pair": ("maid_marilyn", "sir_rufus"),
        "provocation": "Hiệp sĩ Rufus! Ta yêu cầu ngài trả lại chiếc khăn lụa ngài đã giật khỏi tay ta! Chiếu chỉ của Nữ hoàng có thể cho kẻ thắng cuộc phần thưởng, nhưng tấm lòng ta không bao giờ trao cho một kẻ ức hiếp lê dân!",
        "response_prompt": "Hầu nữ Marilyn đang công khai đòi lại chiếc khăn lụa trước mặt mọi người. Hãy từ chối bằng giọng điệu trào phúng, khẳng định quyền của nhà vô địch là bất khả xâm phạm dưới luật lệ Nữ hoàng."
    },
    {
        "pair": ("joking_jerry", "sir_rufus"),
        "provocation": "Kính thưa các quý ngài quý bà! Viên quan thu thuế tôn quý Hiệp sĩ Rufus thu mười phần trăm nộp lên Nữ hoàng, nhưng lại vặt thêm mười lăm phần trăm của dân đen! Thảo nào mấy đồng tiền thừa cứ dính chặt vào túi nhung của ngài ấy!",
        "response_prompt": "Hề Jerry đang công khai châm chọc thói bòn rút thuế của bạn tại Đại Sảnh. Hãy đe dọa gã bằng sự lạnh lùng của hiệp sĩ, cảnh cáo gã hề rằng cái lưỡi sắc lẻm rất dễ phải nếm mùi gông sắt."
    }
]

DRAMA_TOPICS_STAGE_3_4 = [
    {
        "pair": ("willie_watchman", "sir_cameron"),
        "provocation": "Đứng yên đó, Hiệp sĩ Cameron! Hiệp sĩ Rufus đã bị sát hại, và Vật chứng D cho thấy một lọ độc dược cho ngựa ngay trong chuồng của ngài! Đứng nguyên vị trí cho đến khi lính gác xác minh hành tung của ngài lúc đèn tắt phụt!",
        "response_prompt": "Willie Cai Quản đang công khai chỉ đích danh bạn là nghi phạm giết người tại Đại Sảnh. Hãy phẫn nộ bảo vệ danh dự hiệp sĩ, thề rằng hiệp sĩ chiến đấu bằng thương và khiên nơi sáng tỏ, chứ không dùng dao găm đâm lén trong bóng tối."
    },
    {
        "pair": ("maid_monica", "lady_diana"),
        "provocation": "Hãy nhìn tân nương của chúng ta kìa! Ngay trước khi đuốc bị dập tắt, Hiệp sĩ Rufus đã nhìn cô như thể nhìn thấy một bóng ma từ Dunnsberry! Hắn nắm giữ bí mật gì về cô, hỡi Diana, khiến hắn phải trả giá bằng mạng sống?!",
        "response_prompt": "Hầu nữ Monica đang công khai cáo buộc bạn có động cơ bí mật giết Hiệp sĩ Rufus. Hãy phản kích quyết liệt, tố cáo Monica đang lợi dụng thảm kịch này vì lòng hằn học với hôn lễ."
    },
    {
        "pair": ("charlamagne", "lady_gwendolyn"),
        "provocation": "Kính thưa Phu nhân Gwendolyn, giữa cơn kinh hoàng này, kẻ hèn nghĩ rằng có những bậc quý tộc sẽ trả giá rất cao để biết những lọ thuốc độc và thư từ hăm dọa nào đã được lén lút mang qua hành lang trước giờ khai tiệc!",
        "response_prompt": "Charlamagne đang bóng gió tại Đại Sảnh về các lọ độc dược và thư từ hăm dọa. Hãy dùng quyền uy quý tộc răn đe nàng hầu, hạ lệnh cho nàng mang mọi hiểu biết trình báo ngay cho Cai Quản."
    },
    {
        "pair": ("baron_bartholomew", "lady_diana"),
        "provocation": "Diana, nàng hỡi... Vật chứng A mang gia huy rõ rệt của trang viên Dunnsberry nhà nàng! Nhân danh Chúa lòng lành, xin nàng hãy thề trước đại sảnh rằng nàng không dính líu gì đến lưỡi dao vấy máu này!",
        "response_prompt": "Nam tước Bartholomew đang vô cùng đau đớn và chất vấn bạn trước công chúng về con dao Dunnsberry. Hãy dịu dàng an ủi chàng, thề rằng mình vô tội và gợi ý rằng kẻ sát nhân thực sự đã gài bẫy Dunnsberry để hủy hoại mối lương duyên này."
    },
    {
        "pair": ("joking_jerry", "willie_watchman"),
        "provocation": "Một bữa tiệc u ám làm sao! Hiệp sĩ Rufus đến như một dũng sĩ vô địch và ra đi trong tấm vải liệm! Willie, nói cho chúng ta nghe xem, liệu lính canh của ngươi có tóm được lưỡi kiếm bóng ma, hay sẽ lại có một chén rượu khác chan đầy máu?",
        "response_prompt": "Hề Jerry đang bông đùa u ám về vụ án mạng. Hãy nghiêm nghị chấn chỉnh trật tự, cam đoan với hội đồng rằng công lý hoàng gia tại Fernwood sẽ không để kẻ thủ ác trốn thoát."
    }
]

PROACTIVE_HUMAN_INQUIRIES_STAGE_1_2 = [
    {
        "ai_id": "lord_taylor",
        "question": "Chào mừng vị khách quý @{name}! Chuyến đi đến Fernwood của ngài thế nào? Đêm nay ngài ngồi giữa những nhân vật xuất chúng nhất vương quốc—ngài đã nếm thử món thịt nai tẩm hương liệu của chúng ta chưa?"
    },
    {
        "ai_id": "baron_bartholomew",
        "question": "Xin gửi lời chào nồng nhiệt nhất đến ngài, @{name}! Ngài thấy tân nương yêu kiều của ta, Quý cô Diana thế nào? Nhan sắc của nàng há chẳng làm lu mờ mọi bảo ngọc trong cõi đời này sao?"
    },
    {
        "ai_id": "sir_cameron",
        "question": "Hãy nói thật lòng cho ta nghe, @{name}: ngài có theo dõi võ đài thương kích chiều nay không? Tên Rufus chỉ thắng nhờ may mắn gian xảo, vậy mà hắn huênh hoang như thể vừa chém rồng!"
    },
    {
        "ai_id": "joking_jerry",
        "question": "Một câu đố cho vị khách quý tộc, @{name}! Thứ gì sắc hơn ngọn thương hiệp sĩ, bay nhanh hơn một mũi tên, và thường trôi nổi dưới đáy bình rượu?"
    },
    {
        "ai_id": "queen_genevieve",
        "question": "Trẫm đã quan sát sự hiện diện của khanh, @{name}. Tại quê hương khanh, thần dân có tôn kính công lý của Hoàng gia với lòng kính cẩn như lời tuyên thệ tại Fernwood này không?"
    },
    {
        "ai_id": "maid_monica",
        "question": "Hạ tớ có một câu hỏi nhỏ dành cho ngài, @{name}... Ngài có tin rằng những cuộc hôn nhân sắp đặt bằng vàng bạc và điền trang có thể chất chứa tình cảm chân thành trong tim chăng?"
    }
]

PROACTIVE_HUMAN_INQUIRIES_STAGE_3_4 = [
    {
        "ai_id": "willie_watchman",
        "question": "Đứng lại đó, @{name}! Lính canh cần ghi nhận lời khai của từng vị khách! Ngài đã nhìn thấy kẻ nào đứng gần ghế của Hiệp sĩ Rufus trước khi nến tắt?!"
    },
    {
        "ai_id": "lady_diana",
        "question": "Xin thấu cho nỗi lòng của ta, @{name}! Mọi người đang nhìn sự đau đớn của ta với ánh mắt ngờ vực, nhưng đôi tay ta hoàn toàn thanh sạch không vấy máu! Ngài có tin những lời vu khống cay độc đó không?!"
    },
    {
        "ai_id": "sir_cameron",
        "question": "Ngài hãy nhìn Vật chứng A xem, @{name}! Một con dao găm nạm ngọc—vũ khí của một kẻ hèn hạ! Một hiệp sĩ chân chính giao đấu bằng thép lạnh nơi thanh thiên bạch nhật. Kẻ nào trong đại sảnh này dám đâm lén từ bóng tối?"
    },
    {
        "ai_id": "maid_marilyn",
        "question": "Ôi trời, @{name}, tòa lâu đài đang chìm trong kinh hoàng tột độ! Không một người hầu hay quý tộc nào được an toàn chừng nào kẻ sát hại Hiệp sĩ Rufus còn nhởn nhơ! Ngài đã tìm thấy manh mối nào chưa?"
    },
    {
        "ai_id": "lord_taylor",
        "question": "Một bi kịch đen tối đã làm vấy bẩn yến tiệc của ta, @{name}! Là một vị khách danh dự công tâm, ngài nhận định thế nào về các chứng cứ được trình lên bàn nghị án?"
    }
]

PROACTIVE_HUMAN_WHISPERS_STAGE_1_2 = [
    {
        "ai_id": "maid_monica",
        "content": "*[Khẽ bước vào góc tối, liếc mắt nhìn quanh]* \"Thưa Lãnh chúa... hạ thần xin mạn phép thầm thì đôi lời tuyệt mật. Tôi biết những điều về tân nương của Bartholomew có thể khiến ngài lạnh sống lưng đấy. Đêm nay hãy mở to mắt và lắng tai nghe...\""
    },
    {
        "ai_id": "joking_jerry",
        "content": "*[Bất ngờ nhô ra từ sau bức thảm thêu dày, lắc lắc chùm chuông leng keng]* \"Suỵt, bạn hiền tôn quý! Chỉ cần một đồng tiền vàng sáng loáng thả vào hầu bao của gã hề nghèo này, tôi sẽ thì thầm cho ngài biết kẻ nào đã lén lút lướt qua sân trong trước giờ khai tiệc! Ngài nghĩ sao?\""
    },
    {
        "ai_id": "lady_diana",
        "content": "*[Thì thào với vẻ bồn chồn run rẩy]* \"Ta cảm nhận được một tấm lòng cao thượng và đáng tin cậy ở ngài. Hiệp sĩ Rufus liên tục buông những lời đe dọa hiểm độc nhằm vào ta... Nếu chẳng may có chuyện gì xảy ra, xin ngài đừng phán xét ta khi chưa thấu hiểu sự thật.\""
    },
    {
        "ai_id": "sir_cameron",
        "content": "*[Tựa người vào vòm đá, hạ giọng]* \"Một người bạn tri kỷ thật hiếm hoi giữa cái hang ổ đầy mưu mô được dát vàng này. Chiến thắng của tên Rufus chỉ là trò bịp bợm. Hãy giữ thanh đoản kiếm thật sắc và cái đầu thật tỉnh táo, chiến hữu.\""
    },
    {
        "ai_id": "charlamagne",
        "content": "*[Khẽ tiến lại gần với một ly rượu vang]* \"Thứ lỗi, vị khách tôn quý... Một người hầu phòng luôn thấy những điều các bậc lãnh chúa bỏ qua. Nếu ngài muốn biết ai ghé thăm phòng ai khi đèn nến đã tắt, tôi sẵn lòng chia sẻ... chỉ cần một chút bạc hoặc vàng thù lao.\""
    },
    {
        "ai_id": "baron_bartholomew",
        "content": "*[Kéo ngài vào một mật thất riêng]* \"Ta rất mừng vì ngài đã đến dự yến tiệc của ta. Giữa phụ thân ta và dân chúng đang có những mâu thuẫn rất căng thẳng. Ta có thể trông cậy vào lời khuyên can của ngài nếu tối nay có ai mất bình tĩnh chăng?\""
    }
]

PROACTIVE_HUMAN_WHISPERS_STAGE_3_4 = [
    {
        "ai_id": "willie_watchman",
        "content": "*[Chặn đường ngài tại hành lang bằng cây trượng bịt sắt]* \"Đứng lại, vị khách tôn quý! Theo lệnh của quản gia lâu đài, tất cả người dự tiệc phải giải trình hành tung khi nến bị dập tắt! Đôi tay ngài ở đâu lúc Hiệp sĩ Rufus nhận nhát đâm chí mạng?!\""
    },
    {
        "ai_id": "lady_diana",
        "content": "*[Gạt nước mắt, run rẩy nắm lấy tay ngài]* \"Họ đang đồn thổi về con dao găm mang gia huy trang viên Dunnsberry! Ta xin thề trước Chúa tối cao, ta hoàn toàn vô tội! Ai đó đang muốn hủy hoại cuộc đời ta! Xin ngài... hãy nói là ngài không nghi ngờ ta!\""
    },
    {
        "ai_id": "maid_marilyn",
        "content": "*[Thì thầm với đôi môi run rẩy]* \"Tôi vừa tìm thấy một thứ gần lò sưởi... một dải lụa thêu sũng dầu. Tôi rất sợ Willie sẽ tra khảo nếu ông ta tìm thấy nó. Tôi nên làm gì với nó bây giờ, thưa ngài?\""
    },
    {
        "ai_id": "sir_cameron",
        "content": "*[Siết chặt găng tay sắt đầy căm phẫn]* \"Lão Willie dòm ngó ta như một gã đồ tể hạ lưu chỉ vì con ngựa của ta bị trúng độc! Ngài xem Vật chứng A kìa—đó là dao găm nạm ngọc của đàn bà, không phải kiếm lệnh hiệp sĩ! Xin ngài hãy giúp ta rửa sạch danh dự!\""
    },
    {
        "ai_id": "maid_monica",
        "content": "*[Nở nụ cười toan tính lạnh lùng]* \"Hiệp sĩ Rufus đã chết, và cả lâu đài đang run rẩy! Tôi đã thấy một bóng người lượn lờ quanh chén rượu của hắn ngay trước khi bóng tối bao trùm. Với hai đồng vàng, tôi sẽ ghé tai ngài nói rõ tên người đó...\""
    }
]

async def autonomous_mingling_loop():
    """Periodically triggers dramatic AI-to-AI exchanges or addresses human players in the Great Hall if quiet."""
    used_drama_indices = set()
    used_human_inquiry_indices = set()
    while True:
        await asyncio.sleep(35) # Check every 35 seconds
        try:
            if not game_state.is_game_started or game_state.stage >= 5:
                continue

            # Check time since last message
            now = time.time()
            if game_state.great_hall_messages:
                last_msg_time = game_state.great_hall_messages[-1]["timestamp"]
                if now - last_msg_time < 30:
                    continue # Not quiet yet

            human_players = [p for p in game_state.human_players.values() if p.get("character_id")]
            import random

            # 40% chance to proactively address a human player in the Great Hall if present!
            if human_players and random.random() < 0.40:
                target_human = random.choice(human_players)
                target_char = game_state.characters.get(target_human["character_id"])
                target_name = target_char["name"] if target_char else target_human.get("player_name", "Noble Guest")

                pool = PROACTIVE_HUMAN_INQUIRIES_STAGE_1_2 if game_state.stage in [1, 2] else PROACTIVE_HUMAN_INQUIRIES_STAGE_3_4
                valid_inquiries = []
                for idx, inq in enumerate(pool):
                    speaker = game_state.characters.get(inq["ai_id"])
                    if not speaker or speaker.get("is_human"):
                        continue
                    if speaker.get("is_victim") and game_state.stage >= 3:
                        continue
                    if speaker["id"] == target_human["character_id"]:
                        continue
                    valid_inquiries.append((idx, inq, speaker))

                if valid_inquiries:
                    fresh = [x for x in valid_inquiries if x[0] not in used_human_inquiry_indices]
                    if not fresh:
                        used_human_inquiry_indices.clear()
                        fresh = valid_inquiries
                    c_idx, chosen_inq, speaker_ai = random.choice(fresh)
                    used_human_inquiry_indices.add(c_idx)

                    question_text = chosen_inq["question"].replace("{name}", target_name)
                    msg = game_state.add_message(speaker_ai["id"], question_text, chat_type="public")
                    await broadcast({"type": "new_message", "message": msg})
                    continue

            # Otherwise, AI-to-AI dramatic exchange
            topic_pool = DRAMA_TOPICS_STAGE_1_2 if game_state.stage in [1, 2] else DRAMA_TOPICS_STAGE_3_4

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
                        f"Bạn đang giao lưu nơi Đại Sảnh trong Giai đoạn {game_state.stage}. "
                        "Hãy đưa ra một nhận xét ngắn gọn, giàu không khí trung cổ (1-2 câu), tán gẫu về một vị khách khác, "
                        "hoặc cất lời hỏi han ai đó bằng tiếng Việt cổ phong trung cổ trang trọng."
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
        "provocation": "Nam tước Bartholomew, xin hãy lắng nghe lời thì thầm kín đáo của thiếp trước khi lời thề hôn lễ được ấn định! Trái tim thiếp chưa từng quên những lời hẹn ước dưới bóng cây râm mát. Chàng thực sự định cưới cô gái xứ Dunnsberry xa lạ này và thả trôi ký ức chúng ta theo gió sao?",
        "response_prompt": "Hầu nữ Monica đang thì thầm riêng với bạn về mối tình xưa cũ. Hãy nhắc nàng về quân lệnh nghiêm khắc của phụ thân và hôn nhân sắp đặt vì thanh danh Fernwood, van nài nàng giữ kín miệng và thận trọng.",
        "bribe_coins": 1,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Người ta thấy Hầu nữ Monica và Nam tước Bartholomew đang nói chuyện gấp gáp, thì thầm to nhỏ bên hành lang khuất bóng..."
    },
    {
        "pair": ("charlamagne", "lady_gwendolyn"),
        "provocation": "Thưa Phu nhân Gwendolyn, xin cho kẻ hạ thần nói một lời kín đáo. Khi dọn dẹp các tẩm phòng, mắt kẻ hèn vô tình nhìn thấy vài loại thảo mộc khô và những lá thư bí ẩn. Một quý bà tôn quý hẳn sẽ không muốn mắt của Cai Quản Willie dòm ngó vào rương hành lý của mình... chỉ cần một chút quà mọn, môi hạ thần sẽ khóa chặt.",
        "response_prompt": "Hầu phòng Charlamagne đang ngấm ngầm tống tiền bạn về những bí mật tìm thấy trong phòng khách. Hãy phản ứng với vẻ lạnh lùng kiêu sa của quý tộc, cảnh cáo con hầu về thói vô lễ, nhưng hãy kín đáo đưa một đồng vàng để bịt miệng nàng ta.",
        "bribe_coins": 2,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Người hầu phòng Charlamagne bị bắt gặp đang lén lút nhét một mẩu thư niêm phong vào tay áo Phu nhân Gwendolyn..."
    },
    {
        "pair": ("sir_cameron", "maid_marilyn"),
        "provocation": "Hầu nữ Marilyn, xin nàng nán lại một lát tránh xa các lãnh chúa đang chè chén. Hãy nói thật cho ta biết: nàng có thấy tên Rufus lởn vởn quanh chuồng ngựa trước giải đấu không? Chiến mã của ta đột nhiên lờ đờ trước mũi thương của hắn, ta lấy danh dự thề rằng có trò bẩn thỉu ở đây!",
        "response_prompt": "Hiệp sĩ Cameron đang hỏi riêng bạn về chuồng ngựa và Hiệp sĩ Rufus trước giờ xuất đấu. Hãy trả lời thận trọng, chia sẻ rằng quả thực có thấy Rufus quanh quẩn gần phòng yên cương, nhưng khuyên Cameron nên dè chừng.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Hiệp sĩ Cameron được nhìn thấy đang thì thầm nghiêm nghị cùng Hầu nữ Marilyn bên ngọn đuốc kho vũ khí..."
    },
    {
        "pair": ("joking_jerry", "baron_bartholomew"),
        "provocation": "Suỵt, Nam tước trẻ tuổi của tôi! Tiếng chuông của gã hề nghe được nhiều điều mà ngai vàng bỏ lỡ đấy. Một cánh chim thì thào kể với tôi rằng vài món nợ quán rượu và những lời thề non hẹn biển đang được đem ra đổi chác giữa đám hầu gái đêm nay...",
        "response_prompt": "Hề Jerry đang tinh nghịch dò la bí mật riêng tư của bạn. Hãy dùng uy nghi quý tộc cảnh cáo gã hề đừng chõ mũi vào chuyện gia tộc, rồi nhét cho gã một đồng tiền vàng để gã ngậm miệng lại.",
        "bribe_coins": 1,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Hề Jerry bị thấy đang ghé tai thì thầm một trò đùa vào tai Nam tước Bartholomew, đổi lại một cái lườm sắc lẹm..."
    },
    {
        "pair": ("queen_genevieve", "lord_taylor"),
        "provocation": "Lãnh chúa Taylor, bước vào góc khuất này. Chúng ta cần nói chuyện không để đám cận thần nghe lén. Ngân khố Hoàng gia ghi nhận sự thâm hụt lớn trong khoản cống nạp của Fernwood. Nếu bữa dạ yến xa hoa và của hồi môn này được trích từ vàng thuế Hoàng gia, khanh sẽ phải trả lời trước bàn phân xử của Quốc vương.",
        "response_prompt": "Nữ hoàng Genevieve đang dồn bạn vào chân tường về số tiền thuế thất thoát và chi phí lễ hội. Hãy trấn an Nữ hoàng với lòng kính cẩn tột bậc của triều thần, cam đoan rằng vụ thu hoạch mùa thu sẽ bù đắp đầy đủ cho sổ sách Hoàng triều.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Nữ hoàng Genevieve và Lãnh chúa Taylor bị bắt gặp đang bí mật nghị sự căng thẳng trên hành lang thượng tầng..."
    },
    {
        "pair": ("lady_diana", "sir_cameron"),
        "provocation": "Hiệp sĩ Cameron, ta thấy cách Hiệp sĩ Rufus nhìn ta bằng ánh mắt xấc xược và buông lời bóng gió về Dunnsberry. Là một hiệp sĩ thề bảo vệ lẽ phải, ngài có sẵn lòng đứng ra bảo vệ ta nếu cái lưỡi độc địa của hắn vượt qua giới hạn danh dự chăng?",
        "response_prompt": "Quý cô Diana đang bí mật cầu xin sự bảo hộ hiệp sĩ của bạn trước Hiệp sĩ Rufus. Hãy nguyện dùng thanh kiếm của mình bảo vệ danh dự tân nương bằng tinh thần hào hiệp cao quý, khẳng định sự khinh bỉ của bạn đối với Rufus.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Quý cô Diana và Hiệp sĩ Cameron vừa trao nhau lời thề thầm kín trang nghiêm bên cạnh lò sưởi lớn..."
    }
]

UNDERCOVER_PLOTS_STAGE_3_4 = [
    {
        "pair": ("willie_watchman", "maid_monica"),
        "provocation": "Dừng bước, Monica. Với tư cách cai quản và lính canh, ta để ý thấy ngươi lẻn qua hành lang phía tây đúng lúc đèn đuốc bị dập tắt. Hiệp sĩ Rufus nghẹn ngào vì trúng độc chỉ vài khoảnh khắc sau đó. Thứ gì đã đưa bước chân ngươi tiến về phía bàn thượng yến trong bóng tối?",
        "response_prompt": "Willie Cai Quản đang dồn hỏi bạn về hành tung lúc cúp điện. Hãy hoảng sợ tự vệ, thề rằng mình chỉ đi lấy thêm rượu tẩm hương liệu và cố gắng chĩa sự nghi ngờ về phía Quý cô Diana.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Willie Cai Quản bị thấy đang tra hỏi gắt gao Hầu nữ Monica dưới bóng vòm phòng chứa lương thực..."
    },
    {
        "pair": ("willie_watchman", "sir_cameron"),
        "provocation": "Hiệp sĩ Cameron, xin cho nói riêng một lời. Vật chứng D là một lọ cà độc dược tìm thấy trong đống rơm rạ chuồng ngựa của ngài. Rufus đã chết. Tại sao ta không nên xích ngài vào cùm sắt lâu đài ngay lúc này?",
        "response_prompt": "Willie Cai Quản đang cáo buộc bạn sở hữu độc dược giết người. Hãy phẫn nộ khẳng định sự trong sạch hiệp sĩ của mình, yêu cầu ông ta kiểm tra kỹ Vật chứng A (con dao găm Dunnsberry).",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Willie Cai Quản và Hiệp sĩ Cameron bị phát hiện đang tranh cãi gay gắt, hạ thấp giọng gần trạm gác..."
    },
    {
        "pair": ("lady_diana", "baron_bartholomew"),
        "provocation": "Bartholomew, chàng hỡi... thiếp vô cùng sợ hãi. Con dao găm đó mang gia huy của trang viên phụ thân thiếp, nhưng thiếp thề trước Chúa rằng thiếp không hề ra tay với Hiệp sĩ Rufus! Ai đó đang muốn hủy hoại cuộc hôn nhân của chúng ta và đổ tội cho Dunnsberry!",
        "response_prompt": "Quý cô Diana đang run rẩy tâm sự riêng với bạn về hung khí giết người. Hãy dịu dàng an ủi người vợ sắp cưới bằng tất cả tấm lòng son sắt, hứa rằng bạn sẽ bảo vệ nàng khỏi những lời vu cáo sai sự thật.",
        "bribe_coins": 0,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Nam tước Bartholomew và Quý cô Diana được thấy đang nắm chặt tay nhau trong hốc cửa sổ vắng lặng..."
    },
    {
        "pair": ("charlamagne", "willie_watchman"),
        "provocation": "Thưa Cai Quản, bí mật lâu đài quá nặng nề để một mình kẻ hèn này gánh vác. Nếu đội lính gác bảo đảm an toàn và ban thưởng một túi tiền vàng, kẻ hèn có thể tiết lộ đôi giày của ai đã dính đầy sáp nến và bùn đất sau khi đèn phụt tắt...",
        "response_prompt": "Hầu phòng Charlamagne đang chào bán manh mối về vụ án mạng. Hãy nghiêm giọng yêu cầu bằng chứng dưới luật lệ của Nữ hoàng, hứa hẹn một khoản tiền thưởng nhỏ nếu tin tức là chân thật.",
        "bribe_coins": 1,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Hầu phòng Charlamagne bị bắt gặp đang ghé tai thì thào điều gì đó với Willie Cai Quản..."
    },
    {
        "pair": ("joking_jerry", "lady_diana"),
        "provocation": "Thưa Quý cô xứ Dunnsberry... hay tôi nên gọi là quý cô của muôn vàn cái tên? Ký ức của lão Jerry dài hơn những trò đùa của lão nhiều. Tôi còn nhớ một thiếu nữ nơi hội chợ phương bắc không hề sinh ra trong nhung lụa... Nam tước sẽ nghĩ sao nếu gã hề này cất tiếng hát?",
        "response_prompt": "Hề Jerry đang bóng gió rằng gã biết bạn là một nông nô trốn chạy. Hãy giấu nỗi kinh hãi tột độ sau vẻ quý phái lạnh lùng, dúi vàng cho gã để gã ngậm chặt miệng lại.",
        "bribe_coins": 2,
        "clue_hint": "🗝️ [Lời Đồn Lâu Đài] Hề Jerry bị thấy đang nhận một bổng lộc kín đáo từ Quý cô Diana kèm theo cái cúi chào ranh mãnh..."
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

last_human_proactive_whisper_time = 0

async def autonomous_human_whisper_loop():
    """Periodically triggers an AI character to autonomously reach out and whisper directly to a human player."""
    global last_human_proactive_whisper_time
    used_whisper_indices = set()

    while True:
        await asyncio.sleep(18) # Check every 18s
        try:
            if not game_state.is_game_started or game_state.stage >= 5:
                continue

            human_players = [p for p in game_state.human_players.values() if p.get("character_id")]
            if not human_players:
                continue

            now = time.time()
            if now - last_human_proactive_whisper_time < 28:
                continue # 28-second cooldown between proactive whispers

            pool = PROACTIVE_HUMAN_WHISPERS_STAGE_1_2 if game_state.stage in [1, 2] else PROACTIVE_HUMAN_WHISPERS_STAGE_3_4

            import random
            target_human = random.choice(human_players)
            human_cid = target_human["character_id"]

            valid_items = []
            for idx, item in enumerate(pool):
                ai_char = game_state.characters.get(item["ai_id"])
                if not ai_char or ai_char.get("is_human"):
                    continue
                if ai_char.get("is_victim") and game_state.stage >= 3:
                    continue
                if ai_char["id"] == human_cid:
                    continue
                valid_items.append((idx, item, ai_char))

            if not valid_items:
                continue

            fresh = [x for x in valid_items if x[0] not in used_whisper_indices]
            if not fresh:
                used_whisper_indices.clear()
                fresh = valid_items

            chosen_idx, chosen_item, sender_ai = random.choice(fresh)
            used_whisper_indices.add(chosen_idx)

            chat_key = game_state.get_private_chat_key(sender_ai["id"], human_cid)

            # Broadcast typing indicator first to make it feel completely human
            await broadcast({
                "type": "ai_typing",
                "character_id": sender_ai["id"],
                "character_name": sender_ai["name"],
                "chat_type": "private",
                "chat_key": chat_key,
                "recipient_id": human_cid,
                "is_typing": True
            })
            await asyncio.sleep(2.5)
            await broadcast({
                "type": "ai_typing",
                "character_id": sender_ai["id"],
                "character_name": sender_ai["name"],
                "chat_type": "private",
                "chat_key": chat_key,
                "recipient_id": human_cid,
                "is_typing": False
            })

            # Send proactive private whisper from AI to the Human Player
            msg = game_state.add_message(
                sender_ai["id"],
                chosen_item["content"],
                chat_type="private",
                recipient_id=human_cid,
                is_undercover=False
            )
            await broadcast({
                "type": "new_private_message",
                "chat_key": chat_key,
                "message": msg,
                "recipient_id": human_cid,
                "is_undercover": False
            })
            last_human_proactive_whisper_time = now
            logger.info(f"AI {sender_ai['name']} proactively whispered to human player {human_cid}")
        except Exception as e:
            logger.error(f"Error in autonomous_human_whisper_loop: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background mingling task, undercover AI whisper task, and proactive human whisper task
    mingle_task = asyncio.create_task(autonomous_mingling_loop())
    undercover_task = asyncio.create_task(autonomous_undercover_loop())
    human_whisper_task = asyncio.create_task(autonomous_human_whisper_loop())
    yield
    mingle_task.cancel()
    undercover_task.cancel()
    human_whisper_task.cancel()

app = FastAPI(title="A Knight of Murder", lifespan=lifespan)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

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
    port = int(os.getenv("PORT", 8000))
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
        return {
            "registered": False,
            "stage": game_state.stage,
            "is_game_started": game_state.is_game_started,
            "great_hall_messages": game_state.great_hall_messages,
            "human_players": game_state.human_players
        }

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

@app.get("/api/sync/{player_id}")
async def sync_game_state(player_id: str, hall_count: int = 0):
    pdata = game_state.human_players.get(player_id)
    cid = pdata.get("character_id") if pdata else None

    total_hall = len(game_state.great_hall_messages)
    if hall_count < total_hall:
        new_hall = game_state.great_hall_messages[hall_count:]
        all_hall = None
    elif hall_count > total_hall:
        new_hall = []
        all_hall = game_state.great_hall_messages
    else:
        new_hall = []
        all_hall = None

    player_privates = {}
    if cid:
        for key, msgs in game_state.private_chats.items():
            if cid in key.split("__"):
                parts = key.split("__")
                other_id = parts[1] if parts[0] == cid else parts[0]
                player_privates[other_id] = msgs
                player_privates[key] = msgs

    my_gold = None
    if cid and cid in game_state.characters:
        my_gold = game_state.characters[cid].get("current_gold")

    return {
        "status": "ok",
        "stage": game_state.stage,
        "is_game_started": game_state.is_game_started,
        "total_hall": total_hall,
        "new_hall_messages": new_hall,
        "all_hall_messages": all_hall,
        "private_chats": player_privates,
        "human_players": game_state.human_players,
        "my_gold": my_gold,
        "revealed_exhibits": list(game_state.revealed_exhibits),
        "awards": game_state.awards
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
    global last_drama_time, last_human_proactive_whisper_time
    last_drama_time = 0
    last_human_proactive_whisper_time = 0
    game_state.reset()
    await broadcast({
        "type": "game_reset",
        "stage": game_state.stage,
        "characters": game_state.get_active_characters(),
        "human_players": game_state.human_players
    })
    return {
        "status": "ok",
        "stage": game_state.stage,
        "characters": game_state.get_active_characters(),
        "human_players": game_state.human_players
    }

@app.post("/api/join")
async def join_room(payload: Dict[str, Any] = Body(...)):
    player_id = payload.get("player_id")
    player_name = payload.get("player_name", "Noble Guest")
    character_id = payload.get("character_id")

    if not player_id or not character_id:
        raise HTTPException(status_code=400, detail="Missing player_id or character_id")

    if character_id not in game_state.characters:
        raise HTTPException(status_code=400, detail=f"Invalid character ID '{character_id}'")

    success = game_state.register_human_player(player_id, player_name, character_id)
    if not success:
        if player_id not in game_state.human_players and len(game_state.human_players) >= 3:
            raise HTTPException(
                status_code=403, 
                detail="Bàn tiệc đã đủ 3 Thám tử Người thật! Tất cả các vai còn lại đều do AI hoàng gia đảm nhận. Bạn có thể theo dõi ván chơi với vai trò Khán giả triều đình."
            )
        raise HTTPException(status_code=400, detail="Nhân vật này đã có người chọn hoặc đang bị khóa (Sir Rufus).")

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
    sys_note = f"💰 [HỐI LỘ/GIAO DỊCH] {game_state.characters[sender_id]['name']} đã trao {amount} Đồng Vàng cho {game_state.characters[receiver_id]['name']}. Lời nhắn: \"{message}\""
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
            f"BẠN LÀ NỮ HOÀNG GENEVIEVE, ĐẤNG QUÂN VƯƠNG TỐI CAO. {sender_char['name']} đã to gan cả mật dám đưa {amount} đồng vàng cho người với lời nhắn: \"{bribe_note}\". "
            "Một vị Nữ hoàng TUYỆT ĐỐI KHÔNG nhận của hối lộ từ thần dân! Hãy đối đãi cử chỉ này với sự phẫn nộ uy nghiêm và khinh thị tột cùng. "
            "Nhắc cho họ nhớ Điều 1 Luật Lệ Vương Triều quy định tội phản nghịch Nữ hoàng xử trảm! "
            "Tuyên bố tịch thu số vàng này vào Ngân khố Hoàng gia như một khoản tiền phạt cho sự xấc xược, và nghiêm giọng tra hỏi mục đích thực sự của họ."
        )
    elif tier == "noble":
        if amount < 3:
            tier_guidance = (
                f"Bạn là một quý tộc/hiệp sĩ kiêu hãnh ({ai_char['title']}). {sender_char['name']} chỉ đưa vẻn vẹn {amount} đồng vàng với lời nhắn: \"{bribe_note}\". "
                "Đối với một người mang dòng máu cao quý và sở hữu gia tài kếch xù, đây là một sự sỉ nhục buồn cười và bố thí ti tiện! "
                "Hãy phản ứng với sự giễu cợt trịch thượng, châm biếm quý tộc hoặc sự khinh miệt lạnh lùng. "
                "Từ chối tiết lộ bất kỳ bí mật thầm kín nào vì vài đồng tiền cắc, dù có thể buông một lời mỉa mai hoặc yêu cầu lễ vật xứng tầm quý tộc."
            )
        else:
            tier_guidance = (
                f"Bạn là một quý tộc/hiệp sĩ ({ai_char['title']}). {sender_char['name']} vừa dâng tặng một khoản cống nạp hậu hĩnh và kính cẩn gồm {amount} đồng vàng với lời nhắn: \"{bribe_note}\". "
                "Ghi nhận lễ vật đáng kính này với sự trân trọng đàng hoàng và sự kín đáo của bậc quý tộc. "
                "Để đền đáp, hãy chia sẻ một bí mật có giá trị, lời đồn hoặc quan sát chiến thuật phù hợp với mục tiêu của nhân vật bạn."
            )
    elif tier == "law":
        tier_guidance = (
            f"Bạn là Willie Cai Quản, người tuyên thệ bảo vệ trật tự lâu đài và đang điều tra án mạng. {sender_char['name']} vừa trao cho bạn {amount} đồng vàng với lời nhắn: \"{bribe_note}\". "
            "Nếu lời nhắn này có vẻ hối lộ để làm ngơ tội ác hoặc hủy bỏ tra khảo, hãy quở trách gay gắt rằng công lý hoàng gia và máu của Hiệp sĩ Rufus không thể mua bằng tiền! "
            "Nếu đó là sự hỗ trợ hay cống nạp tôn kính cho đội lính gác, hãy chấp nhận với tính thực tế cộc cằn và chia sẻ một quan sát khách quan về các vị khách hoặc vật chứng."
        )
    else: # commoner
        if amount == 1:
            tier_guidance = (
                f"Bạn là một người hầu/chú hề khiêm nhường ({ai_char['title']}). {sender_char['name']} vừa cho bạn 1 đồng vàng với lời nhắn: \"{bribe_note}\". "
                "Hãy cất đồng vàng vào túi với lòng biết ơn, cảm ơn họ và chia sẻ một tin đồn hoặc manh mối nhỏ hữu ích để đền đáp."
            )
        else:
            tier_guidance = (
                f"Bạn là một người hầu/chú hề khiêm nhường ({ai_char['title']}). {sender_char['name']} vừa hào phóng ban thưởng cho bạn một khoản tiền kếch xù gồm {amount} đồng vàng với lời nhắn: \"{bribe_note}\". "
                "Hãy cực kỳ phấn khích và khắc cốt ghi tâm món nợ ân tình này! Với số tài sản lớn như vậy, hãy háo hức thì thầm bí mật nóng hổi nhất, những tin đồn động trời hoặc đồng ý trung thành hỗ trợ họ."
            )

    instruction = (
        f"{sender_char['name']} vừa kín đáo dúi vào tay bạn {amount} đồng vàng với lời nhắn: \"{bribe_note}\".\n"
        f"CHỈ DẪN: {tier_guidance}\n"
        "Hãy nói từ 2 đến 3 câu hoàn chỉnh bằng tiếng Việt cổ phong trung cổ đúng chuẩn phong thái của nhân vật bạn."
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
        else:
            # If a human player spoke publicly, but didn't tag any specific AI,
            # an active AI noble/servant will autonomously react so the human is never ignored!
            sender_char = game_state.characters.get(sender_id)
            if sender_char and sender_char.get("is_human"):
                valid_ais = [
                    c for c in active_chars
                    if not c.get("is_human") and not (c.get("is_victim") and game_state.stage >= 3)
                ]
                if valid_ais:
                    import random
                    responding_ai = random.choice(valid_ais)
                    asyncio.create_task(generate_ai_great_hall_reply(responding_ai, sender_id, content, is_general_speech=True))
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

        # If recipient is genuinely an AI, trigger immediate private reply!
        recipient_char = game_state.characters.get(recipient_id)
        is_target_ai = recipient_char and not game_state.is_character_human(recipient_id) and not recipient_char.get("is_human")
        if is_target_ai:
            asyncio.create_task(generate_ai_private_reply(recipient_char, sender_id, content))
        return msg

    return None

@app.post("/api/message")
async def send_message_http_endpoint(payload: Dict[str, Any] = Body(...)):
    chat_type = payload.get("type") or payload.get("chat_type") or "public"
    content = payload.get("content", "").strip()
    sender_id = payload.get("sender_id")
    recipient_id = payload.get("recipient_id")

    if not content or not sender_id:
        raise HTTPException(status_code=400, detail="Missing content or sender_id")

    msg = await process_incoming_message(chat_type, sender_id, content, recipient_id)
    if not msg:
        raise HTTPException(status_code=400, detail="Invalid message delivery parameters")
    return {"status": "ok", "message": msg}

SUBTLE_HERALD_HINTS = {
    1: [
        "Quản Trò Hoàng Gia cất lời quan sát: 'Hãy chú ý xem Hiệp sĩ Cameron trừng mắt hằn học ra sao về phía kẻ thắng giải đấu thương kích. Thất bại trên đấu trường quả là cay đắng, nhưng ngọn lửa ghen tuông còn thiêu đốt dữ dội hơn.'",
        "Quản Trò Hoàng Gia khẽ thì thào: 'Cha xứ Gabriel cao giọng giảng về lòng nhân ái, nhưng ánh mắt ngài lại thấp thỏm hướng về kho vàng của Lãnh chúa. Hãy thăm dò xem món nợ chưa trả nào đang đè nặng lên giáo xứ.'",
        "Quản Trò Hoàng Gia lẩm bẩm: 'Tấm voan cưới của Tiểu thư Diana che giấu nhiều điều hơn là sự e lệ của thiếu nữ. Một thiên kim dòng dõi Dunnsberry không đời nào chịu thành hôn nếu không có những điều khoản trói buộc và lời hứa bí mật.'",
        "Quản Trò Hoàng Gia nhắc nhở: 'Rượu nồng làm mở miệng, nhưng vàng ròng mới mở toang những cánh cửa khóa chặt. Một chút tiền hối lộ cho kẻ tôi tớ thường vén màn những gì các bậc quý tộc cố tình chôn giấu.'"
    ],
    2: [
        "Quản Trò Hoàng Gia cảnh báo: 'Yến tiệc của Lãnh chúa Taylor tràn ngập rượu ngon, nhưng tin đồn về việc sưu thuế tăng vọt đang khuấy đảo nỗi bất bình trong dân chúng. Hãy để ý xem ai đang vừa nói vừa nghiến răng nắm chặt nắm đấm.'",
        "Quản Trò Hoàng Gia thì thầm: 'Hầu nữ Monica rót rượu với đôi bàn tay run rẩy mỗi khi chú rể bước qua. Một mối tình bị ruồng bỏ là vị khách nguy hiểm nhất trong một đám cưới xa hoa.'",
        "Quản Trò Hoàng Gia quan sát: 'Hiệp sĩ Rufus huênh hoang khoe túi tiền thưởng và dải lụa của Nàng Marilyn trước bàn dân thiên hạ. Kẻ chuốc lấy quá nhiều kẻ thù trước giờ khai tiệc hiếm khi ngủ yên mà không có lính canh gác.'",
        "Quản Trò Hoàng Gia gợi ý: 'Hãy để ý xem ai đã lén rời khỏi đại sảnh dạ yến bước vào các phòng kín ngay trước khi nghi thức nâng ly chúc mừng bắt đầu.'"
    ],
    3: [
        "Quản Trò Hoàng Gia hướng mắt về chiếc ghế tối tăm: 'Khi đuốc vụt tắt, mắt chẳng thấy nhưng tai vẫn tỏ. Ai là kẻ ngồi gần Hiệp sĩ Rufus nhất, và tiếng ghế của ai đã kéo lê trên nền đá?'",
        "Quản Trò Hoàng Gia thì thầm: 'Lính canh Willie đã phong tỏa các lối ra trước khi vệt máu kịp đông lại. Kẻ thủ ác không hề trốn thoát qua hào nước; hắn vẫn đang đứng ngay giữa chúng ta, thản nhiên lau tay vào vạt áo lụa là.'",
        "Quản Trò Hoàng Gia cảnh báo: 'Một nhát dao đâm lén trong bóng tối chỉ cần cự ly áp sát, chứ không đòi hỏi sức mạnh ngút ngàn. Đừng chỉ nhìn vào các hiệp sĩ giáp sắt, mà hãy để ý những kẻ luồn lách vô hình giữa các bàn tiệc.'",
        "Quản Trò Hoàng Gia khuyên nhủ: 'Hãy đối chiếu lời khai hành tung của từng vị khách trong lúc nâng ly. Kẻ nào có chứng cứ ngoại phạm phụ thuộc vào lời làm chứng của người khác thì rất có thể đã mua chuộc sự im lặng bằng vàng.'"
    ],
    4: [
        "Quản Trò Hoàng Gia kiểm tra tang vật: 'Dải lụa thêu chữ và con dao găm Dunnsberry kể hai câu chuyện hoàn toàn đối nghịch. Một vật bị đánh rơi trong hoảng loạn, nhưng liệu vật kia có phải bị cố tình gài bẫy để đánh lạc hướng?'",
        "Quản Trò Hoàng Gia lưu ý: 'Hãy xem lại sổ sách lâu đài. Một cuộc chuyển giao tiền vàng đột ngột trong dạ yến thường vạch trần kẻ tống tiền vừa nhận bổng lộc hoặc một tay đồng lõa vừa được bịt miệng.'",
        "Quản Trò Hoàng Gia thì thầm: 'Nỗi sợ hãi tột cùng của kẻ sát nhân không phải là bị nghi ngờ cơn thịnh nộ, mà là việc thân phận thật sự của hắn bị lột trần. Hãy tra hỏi xem ai có nhiều thứ để mất nhất nếu quá khứ bị phơi bày.'",
        "Quản Trò Hoàng Gia hé lộ: 'Hãy lần tìm quá khứ của nạn nhân tại Dunnsberry. Hiệp sĩ Rufus đã nhận ra một người ở đây—kẻ từ lâu đã đội lốt một thân phận giả mạo.'"
    ],
    5: [
        "Quản Trò Hoàng Gia gõ mạnh quyền trượng xuống sàn đá: 'Giờ phán xét đã điểm! Hãy đặt chứng cứ thực tế lên bàn cân cùng lời tuyên thệ, và để chân lý toàn thắng trước mọi mưu mô xảo quyệt!'"
    ]
}

@app.post("/api/hint")
async def get_herald_hint_endpoint(payload: Dict[str, Any] = Body(...)):
    player_id = payload.get("player_id", "")
    stage = game_state.stage
    pool = SUBTLE_HERALD_HINTS.get(stage, SUBTLE_HERALD_HINTS[1])

    hint_text = None
    try:
        sys_prompt = build_herald_hint_prompt(stage, game_state.revealed_exhibits, list(game_state.human_players.keys()))
        recent_history = [
            {"role": "user", "content": f"{m['sender_name']}: {m['content']}"}
            for m in game_state.great_hall_messages[-6:]
        ]
        recent_history.append({"role": "user", "content": "Hãy đưa ra một gợi ý/quan sát trung cổ tinh tế, thi vị, không tiết lộ hung thủ (không spoiler) bằng tiếng Việt cho các thám tử!"})
        reply = await asyncio.wait_for(ai_engine.generate_response(sys_prompt, recent_history, max_tokens=140), timeout=6.0)
        if reply and len(reply) > 20 and not "AI Error" in reply and not reply.startswith("("):
            hint_text = f"Quản Trò Hoàng Gia hé lộ manh mối kín đáo: \"{reply.strip()}\""
    except Exception as e:
        logger.info(f"Dynamic hint fallback to pool: {e}")

    if not hint_text:
        import random
        hint_text = random.choice(pool)

    msg = game_state.add_message("system", hint_text, chat_type="public")
    await broadcast({
        "type": "herald_hint",
        "message": msg,
        "hint": hint_text,
        "stage": stage
    })
    return {"status": "ok", "hint": hint_text, "message": msg}

@app.websocket("/ws/{player_id}")
async def websocket_endpoint(websocket: WebSocket, player_id: str):
    await websocket.accept()
    connected_websockets.setdefault(player_id, set()).add(websocket)
    logger.info(f"WebSocket connected for player: {player_id}")

    try:
        while True:
            raw_text = await websocket.receive_text()
            data = json.loads(raw_text)
            action = data.get("action")

            if action == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "time": time.time()}))
                continue

            elif action == "send_message":
                chat_type = data.get("type", "public")
                content = data.get("content", "").strip()
                sender_id = data.get("sender_id")
                recipient_id = data.get("recipient_id")
                await process_incoming_message(chat_type, sender_id, content, recipient_id)
            elif action == "request_hint":
                await get_herald_hint_endpoint({"player_id": player_id})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for {player_id}: {e}")
    finally:
        if player_id in connected_websockets:
            connected_websockets[player_id].discard(websocket)
            if not connected_websockets[player_id]:
                connected_websockets.pop(player_id, None)
        logger.info(f"WebSocket disconnected for player: {player_id}")

async def generate_ai_great_hall_reply(ai_char: Dict[str, Any], sender_cid: str, user_content: str, is_general_speech: bool = False):
    sender_char = game_state.characters.get(sender_cid)
    sender_name = sender_char["name"] if sender_char else sender_cid

    # Broadcast typing indicator
    await broadcast({
        "type": "ai_typing",
        "character_id": ai_char["id"],
        "character_name": ai_char["name"],
        "chat_type": "public",
        "is_typing": True
    })

    try:
        # Realistic human-like delay
        await asyncio.sleep(2.5)

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
        if is_general_speech:
            mention_instruction = (
                f"Bạn là {ai_char['name']}. {sender_name} vừa cất lời công khai trước toàn thể Đại Sảnh: \"{user_content}\". "
                "Hãy phản hồi trực tiếp lời nói của họ bằng tiếng Việt cổ phong trung cổ đúng theo thân phận và mục tiêu bí mật của bạn. Lên tiếng lịch thiệp hoặc chất vấn gay gắt tùy theo địa vị. Nói từ 2 đến 3 câu hoàn chỉnh."
            )
        else:
            mention_instruction = (
                f"Bạn là {ai_char['name']}. {sender_name} vừa cất lời gọi đích danh bạn tại Đại Sảnh: \"{user_content}\". "
                "Hãy trực tiếp đáp lời họ công khai trước dạ yến bằng tiếng Việt cổ phong trung cổ đúng theo thân phận của bạn. Nói từ 2 đến 3 câu hoàn chỉnh."
            )
        history.append({"role": "user", "content": mention_instruction})

        reply = await ai_engine.generate_response(sys_prompt, history, max_tokens=500)
        ai_msg = game_state.add_message(ai_char["id"], reply, chat_type="public")
        await broadcast({"type": "new_message", "message": ai_msg})
    finally:
        await broadcast({
            "type": "ai_typing",
            "character_id": ai_char["id"],
            "character_name": ai_char["name"],
            "chat_type": "public",
            "is_typing": False
        })

async def generate_ai_private_reply(ai_char: Dict[str, Any], sender_cid: str, user_content: str):
    sender_char = game_state.characters.get(sender_cid)
    sender_name = sender_char["name"] if sender_char else sender_cid
    chat_key = game_state.get_private_chat_key(ai_char["id"], sender_cid)

    # Broadcast typing indicator
    await broadcast({
        "type": "ai_typing",
        "character_id": ai_char["id"],
        "character_name": ai_char["name"],
        "chat_type": "private",
        "chat_key": chat_key,
        "recipient_id": sender_cid,
        "is_typing": True
    })

    try:
        # Realistic human typing delay
        await asyncio.sleep(2.5)

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
    finally:
        await broadcast({
            "type": "ai_typing",
            "character_id": ai_char["id"],
            "character_name": ai_char["name"],
            "chat_type": "private",
            "chat_key": chat_key,
            "recipient_id": sender_cid,
            "is_typing": False
        })

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
