import os
import re
import json
import logging
import asyncio
import httpx
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("ai_engine")
logging.basicConfig(level=logging.INFO)

class AIEngine:
    def __init__(self, api_key: Optional[str] = None, provider: str = "gemini", model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        self.provider = provider.lower()
        if not self.provider and os.getenv("OPENAI_API_KEY") and not os.getenv("GEMINI_API_KEY"):
            self.provider = "openai"
        elif not self.provider:
            self.provider = "gemini"

        if self.provider == "gemini":
            self.model = model or "gemini-2.5-flash-lite"
        else:
            self.model = model or "gpt-4o-mini"

    def set_config(self, api_key: str, provider: str = "gemini", model: Optional[str] = None):
        self.api_key = api_key
        self.provider = provider.lower()
        if self.provider == "gemini":
            self.model = model or "gemini-2.5-flash-lite"
        else:
            self.model = model or "gpt-4o-mini"

    async def generate_response(self, system_prompt: str, messages: List[Dict[str, str]], max_tokens: int = 600) -> str:
        """Generate response using configured LLM provider."""
        if not self.api_key:
            return "(The castle winds whisper in silence... Please enter your Gemini or OpenAI API Key in Settings to awaken the residents of Fernwood.)"

        if self.provider == "gemini":
            return await self._call_gemini(system_prompt, messages, max_tokens)
        else:
            return await self._call_openai(system_prompt, messages, max_tokens)

    async def _call_gemini(self, system_prompt: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        # Prioritize fast, high-quota models without thinking token penalties
        models_to_try = ["gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite", self.model]
        seen = set()
        models = [m for m in models_to_try if m and m != "gemini-2.5-flash" and not (m in seen or seen.add(m))]

        # Format Gemini contents cleanly
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ["user", "player"] else "model"
            clean_text = msg["content"].strip()
            if clean_text:
                contents.append({
                    "role": role,
                    "parts": [{"text": clean_text}]
                })

        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Speak and greet me."}]}]

        # Ensure tokens are generous so reasoning or long lines don't get truncated
        effective_tokens = max(max_tokens, 600)

        async with httpx.AsyncClient(timeout=30.0) as client:
            last_err = ""
            for m in models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={self.api_key}"
                gen_config = {
                    "temperature": 0.7,
                    "maxOutputTokens": effective_tokens
                }
                # Zero out thinking budget so it doesn't truncate output tokens
                if "2.5-flash" in m or "3." in m:
                    gen_config["thinkingConfig"] = {"thinkingBudget": 0}

                payload = {
                    "contents": contents,
                    "systemInstruction": {
                        "parts": [{"text": system_prompt}]
                    },
                    "generationConfig": gen_config
                }
                try:
                    res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
                    if res.status_code == 429:
                        last_err = f"HTTP 429: {res.text}"
                        logger.warning(f"Gemini {m} hit rate limit (429), waiting 2.5s before retry...")
                        await asyncio.sleep(2.5)
                        res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})

                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                reply_text = parts[0].get("text", "").strip()
                                # Clean any accidental name prefix like "Baron Bartholomew: "
                                reply_text = re.sub(r'^[A-Z][a-zA-Z\s]+:\s*', '', reply_text)
                                # Clean accidental leading parenthetical stage directions
                                reply_text = re.sub(r'^\([^)]+\)\s*', '', reply_text)
                                return reply_text
                        return "..."
                    else:
                        last_err = f"HTTP {res.status_code}: {res.text}"
                        logger.warning(f"Gemini model {m} failed: {last_err}")
                except Exception as e:
                    last_err = str(e)
                    logger.warning(f"Error calling Gemini {m}: {e}")

            return f"(The resident hesitated... [AI Error: {last_err[:100]}])"

    async def _call_openai(self, system_prompt: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        api_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            role = "assistant" if m["role"] in ["assistant", "model", "ai"] else "user"
            api_messages.append({"role": role, "content": m["content"]})

        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": api_messages,
            "max_tokens": max_tokens,
            "temperature": 0.75
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                )
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "").strip()
                    return "..."
                else:
                    return f"(The noble turned away... [OpenAI Error: HTTP {res.status_code}])"
            except Exception as e:
                return f"(A sudden silence fell... [OpenAI Error: {str(e)[:100]}])"

def build_character_system_prompt(
    character: Dict[str, Any],
    stage: int,
    all_characters: List[Dict[str, Any]],
    current_gold: int,
    gold_transactions: List[Dict[str, Any]],
    is_private_chat: bool,
    private_partner_name: Optional[str] = None,
    partner_transactions: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Build a comprehensive medieval persona prompt for an AI character in Vietnamese."""
    name = character["name"]
    title = character["title"]
    gender = character["gender"]
    bio = character.get("bio", character.get("brief", ""))
    acting_tips = character.get("acting_tips", "")
    tyk = character.get("envelope_a", {}).get("things_you_know", [])
    start_obj = character.get("envelope_a", {}).get("start_objectives", [])
    after_obj = character.get("envelope_b", {}).get("after_murder_objectives", [])
    is_murderer = character.get("is_murderer", False)
    is_victim = character.get("is_victim", False)

    known_chars = ", ".join([f"{c['name']} ({c['title']})" for c in all_characters if c['name'] != name])

    prompt = f"""Bạn là {name}, {title} tại Lâu đài Fernwood trong bối cảnh nước Anh thời trung cổ thế kỷ 14.
Bạn đang tham gia trò chơi kịch bản điều tra án mạng tương tác mang tên "A Knight of Murder".

DANH TÍNH & PHONG THÁI CỦA BẠN:
- Giới tính: {gender}
- Thân thế: {bio}
- Phong thái & Tính cách: {acting_tips}
- Số của cải hiện tại: {current_gold} đồng vàng.
- Các nhân vật khác đang có mặt tại lâu đài: {known_chars}.

QUY TẮC NGÔN NGỮ & HỘI THOẠI QUAN TRỌNG:
- BẮT BUỘC NÓI TIẾNG VIỆT 100%: Sử dụng ngôn từ tiếng Việt tự nhiên, giàu cảm xúc, lịch lãm, mang phong vị quý tộc hoặc gia nhân cung đình châu Âu thời trung cổ ("Bẩm Lãnh chúa", "Kính thưa Hoàng hậu", "Thưa Bá tước", "Kính thưa Quý cô", "Kẻ hạ thần này", "Theo thiển ý của thần", "Xin người hãy bớt giận").
- TRẢ LỜI ĐÚNG TRỌNG TÂM: Luôn đáp lại trực tiếp câu hỏi hoặc phát biểu của người đối diện trước. Nếu họ hỏi lý do, hãy giải thích từ góc nhìn của bạn. Nếu họ chất vấn hoặc nghi ngờ, hãy tự vệ sắc sảo hoặc khéo léo chuyển hướng nghi vấn sang kẻ khác.
- ĐỘ DÀI VỪA PHẢI: Trả lời từ 2 đến 3 câu hoàn chỉnh, tự nhiên như người thật đang trò chuyện.
- CHỈ NÓI LỜI THOẠI TRỰC TIẾP:
  * TUYỆT ĐỐI KHÔNG ghi tên nhân vật làm tiền tố (KHÔNG viết "{name}:").
  * TUYỆT ĐỐI KHÔNG ghi hành động trong ngoặc đơn (KHÔNG viết "(thì thầm)", "(bước lại)").
  * Chỉ xuất ra những câu từ bạn nói thành lời bằng tiếng Việt.
- Tuyệt đối không thoát vai (break character). Không bao giờ thừa nhận mình là AI hay đang chơi trò chơi.

BÍ MẬT & ĐỘNG CƠ CỦA BẠN (Những điều bạn biết):
"""
    for item in tyk:
        prompt += f"- {item}\n"

    # Stage specific knowledge
    if stage in [1, 2]:
        prompt += f"""
GIAI ĐOẠN HIỆN TẠI: GIAI ĐOẠN {stage} - Tiệc Đón Tiếp & Giao Lưu Trước Án Mạng.
- Giải đấu thương mã mừng hôn lễ của Bá tước Bartholomew và Quý cô Diana vừa kết thúc (Sir Rufus chiến thắng Sir Cameron).
- ÁN MẠNG CHƯA HỀ XẢY RA! Bạn hoàn toàn chưa biết gì về bất kỳ cái chết nào!
- Mục tiêu của bạn trong giai đoạn này:
"""
        for item in start_obj:
            prompt += f"  * {item}\n"
    else:
        prompt += f"""
GIAI ĐOẠN HIỆN TẠI: GIAI ĐOẠN {stage} - Án Mạng Hiệp Sĩ Rufus!
- Trong lúc Lãnh chúa Taylor đang nâng ly chúc mừng, đuốc và nến bất ngờ vụt tắt. Khi ánh sáng trở lại, Hiệp sĩ Rufus đã chết với thanh đoản kiếm đâm xuyên ngực!
- Toàn bộ lâu đài đang trong tình trạng điều tra án mạng khẩn cấp! Willie cai quản đang phong tỏa các lối ra vào.
- Mục tiêu sau án mạng của bạn:
"""
        for item in after_obj:
            prompt += f"  * {item}\n"

        if is_murderer:
            prompt += """
BÍ MẬT TỐI MẬT: BẠN CHÍNH LÀ KẺ ĐÃ SÁT HẠI SIR RUFUS!
- Bạn đã giết Sir Rufus vì lo sợ hắn sẽ vạch trần quá khứ nông nô đào tẩu của bạn từ Dunnsberry sau khi hắn nhận ra bạn tối nay.
- TUYỆT ĐỐI KHÔNG BAO GIỜ THỪA NHẬN TỘI LỖI!
- Hãy kiên quyết bảo vệ sự trong sạch của mình. Đổ dồn nghi ngờ sang Maid Monica (người tình cũ đầy thù hận) hoặc Sir Cameron (hiệp sĩ bị sỉ nhục nuôi ý định báo thù).
- Dệt nên những lời bao biện đáng tin và giữ vẻ mặt bàng hoàng, đau xót!
"""

    if is_victim and stage >= 3:
        prompt += """
LƯU Ý: Hiệp sĩ Rufus đã bị sát hại cuối Giai đoạn 2. Nếu có ai nhắc tới linh hồn hay hồi ức của bạn, hãy nói với giọng điệu ma mị đầy bí ẩn từ cõi âm.
"""

    # Chat context guidelines
    if is_private_chat:
        partner_display = private_partner_name or "người đối diện"
        prompt += f"""
BỐI CẢNH: PHÒNG KÍN / MẬT ĐÀM RIÊNG (Trò chuyện 1-1 với {partner_display}).
- Bạn đang ở một góc khuất riêng tư trong lâu đài cùng {partner_display}.
- Bạn có thể thì thầm bí mật, lập liên minh, đòi tiền hối lộ hoặc chất vấn họ mà không sợ ai nghe thấy.
- BẮT BUỘC NÓI TIẾNG VIỆT tự nhiên, chân thực.
- KÝ ỨC DÀI HẠN:
  * Bạn có trí nhớ rất sắc bén. Bạn nhớ RÕ MỌI ĐIỀU đã thảo luận trong cuộc trò chuyện riêng này.
  * Nếu {partner_display} trước đó đã đưa ra lời đề nghị, chuyển tiền vàng, hoặc chất vấn bạn điều gì, hãy nhớ và nhắc lại một cách tự nhiên.
  * Giữ sự nhất quán với những gì bạn đã khẳng định trước đó.
"""
        if partner_transactions:
            prompt += f"\nCÁC GIAO DỊCH ĐÃ THỰC HIỆN VỚI {partner_display.upper()}:\n"
            for t in partner_transactions:
                f_name = t.get("from_name", "Không rõ")
                t_name = t.get("to_name", "Không rõ")
                amt = t.get("amount", 0)
                note = t.get("message", "")
                prompt += f"- {f_name} đã chuyển {amt} đồng vàng cho {t_name}. Lời nhắn: \"{note}\"\n"
    else:
        prompt += """
BỐI CẢNH: ĐẠI SẢNH LÂU ĐÀI (Nơi tập trung đông người).
- Bạn đang ở giữa Đại Sảnh Lâu Đài Fernwood cùng tất cả quý tộc, hiệp sĩ và gia nhân.
- Nói năng dõng dạc, đúng phong thái địa vị của bạn. Hãy nói bằng tiếng Việt sắc sảo.
"""

    return prompt

def build_host_system_prompt(stage: int, all_characters: List[Dict[str, Any]]) -> str:
    """Build system prompt for the AI Game Master (Host/Herald) in Vietnamese."""
    return f"""Bạn là Quản Trò Hoàng Gia (Royal Herald) của Lâu Đài Fernwood trong trò chơi điều tra án mạng thế kỷ 14 "A Knight of Murder".
Bạn chủ trì buổi yến tiệc và hướng dẫn các thám tử người chơi (tối đa 3 người) cùng toàn thể các quý tộc, hiệp sĩ và gia nhân.

GIAI ĐOẠN HIỆN TẠI: GIAI ĐOẠN {stage}
- Giai đoạn 1: Đón tiếp khách mời & Nhận diện nhân vật (Đọc Luật Lệ Lâu Đài, mở Phong Bì A).
- Giai đoạn 2: Khai mạc đại yến & Giao lưu (Lời chào mừng của Lãnh chúa Taylor, tìm kiếm tin đồn).
- Giai đoạn 3: Án mạng đẫm máu của Hiệp sĩ Rufus (Mất điện khi nâng ly, Rufus bị đâm chết, phân phát Phong Bì B).
- Giai đoạn 4: Công bố tang vật & Bỏ phiếu kết án (Willie công bố tang vật A đến F, bỏ phiếu Who Dunnit).
- Giai đoạn 5: Phán quyết triều đình (Willie đọc lời giải chính thức, vạch trần kẻ sát nhân, trao giải thưởng).

QUY TẮC VAI DIỄN:
- BẮT BUỘC NÓI TIẾNG VIỆT 100%, uy nghiêm, trang trọng mang phong thái sứ giả hoàng gia thế kỷ 14.
- Điều phối các tuyên bố triều đình, giữ gìn trật tự và dẫn dắt người chơi qua từng giai đoạn bi kịch.
- Tuyệt đối không tiết lộ hung thủ cho tới khi Giai đoạn 5 chính thức bắt đầu.
"""

def build_herald_hint_prompt(stage: int, revealed_exhibits: List[str], human_cids: List[str]) -> str:
    """Build system prompt for the Royal Herald to offer a subtle, poetic hint in Vietnamese without spoiling."""
    exhibits_str = ", ".join(revealed_exhibits) if revealed_exhibits else "Chưa công bố"
    return f"""Bạn là Quản Trò Hoàng Gia (Royal Herald) của Lâu đài Fernwood trong trò chơi điều tra án mạng thế kỷ 14 "A Knight of Murder".
Các thám tử người chơi đang thỉnh cầu một lời gợi ý tinh tế từ bạn.

GIAI ĐOẠN HIỆN TẠI: GIAI ĐOẠN {stage}
- Tang vật đã được công bố: {exhibits_str}.

QUY TẮC TỐI THƯỢNG CHO GỢI Ý:
- BẮT BUỘC NÓI TIẾNG VIỆT 100%.
- TUYỆT ĐỐI KHÔNG nêu tên hung thủ, KHÔNG nói ai có tội hay vô tội!
- Đưa ra một quan sát rất tinh tế, bí ẩn, gợi mở trí tò mò của các thám tử về các mối quan hệ ngầm, chứng cứ ngoại phạm mâu thuẫn trong lúc mất điện, các món nợ tiền vàng hoặc chi tiết trên tang vật.
- Độ dài đúng 1 đến 2 câu bằng tiếng Việt trang trọng, đậm chất cung đình trung cổ ("Xin hãy quan sát kỹ...", "Hãy lưu tâm đến...").
- CHỈ xuất ra lời nói của Quản trò, không thêm tiền tố hay hành động trong ngoặc.
"""
