import os
import re
import hashlib
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger("tts_engine")

# ElevenLabs voice mappings tailored to character gender, personality, and social station
CHARACTER_VOICES: Dict[str, Dict[str, Any]] = {
    "queen_genevieve": {
        "voice_id": "Xb7hH8MSUJpSbSDYk0k2",  # Alice - Confident, noble British female
        "name": "Alice (Nữ hoàng quyền uy)",
        "gender": "female",
        "description": "Giọng nữ hoàng uy nghi, đĩnh đạc và tôn nghiêm của bậc mẫu nghi thiên hạ.",
        "sample_quote": "Hỡi thần dân và quý tộc, Vương quyền của vương triều này không cho phép bất kỳ sự phản nghịch hay dối trá nào tồn tại!",
        "settings": {"stability": 0.65, "similarity_boost": 0.85, "style": 0.15, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.9, "rate": 0.95, "gender": "female"}
    },
    "lord_taylor": {
        "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam - Deep, commanding host
        "name": "Adam (Lãnh chúa Fernwood)",
        "gender": "male",
        "description": "Giọng nam trung niên trầm hùng, hiếu khách nhưng uy nghiêm của chủ nhân Lâu đài Fernwood.",
        "sample_quote": "Chào mừng chư vị hiệp sĩ và phu nhân đã quang lâm Lâu đài Fernwood. Rượu ngon đã rót, xin hãy nâng ly mừng ngày đại hỷ!",
        "settings": {"stability": 0.60, "similarity_boost": 0.80, "style": 0.10, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.85, "rate": 0.95, "gender": "male"}
    },
    "lady_gwendolyn": {
        "voice_id": "z9fAnlkpzviPz146aGWa",  # Glinda - Warm, vintage aristocratic female
        "name": "Glinda (Phu nhân quý phái)",
        "gender": "female",
        "description": "Giọng nữ quý phái, thanh nhã và đằm thắm của nữ chủ nhân lâu đài.",
        "sample_quote": "Lâu đài hôm nay lộng lẫy xiết bao. Ta hy vọng tất cả quý khách đều cảm thấy hài lòng với sự hiếu khách này.",
        "settings": {"stability": 0.65, "similarity_boost": 0.80, "style": 0.10, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.95, "rate": 0.95, "gender": "female"}
    },
    "baron_bartholomew": {
        "voice_id": "N2lVS1w4EtoT3dr4eOWO",  # Callum - Intense, ambitious young noble
        "name": "Callum (Nam tước tham vọng)",
        "gender": "male",
        "description": "Giọng nam trẻ tuổi, sắc bén, tham vọng và đầy mưu tính của chú rể quý tộc.",
        "sample_quote": "Hôn lễ này sẽ định đoạt tương lai của Fernwood. Kẻ nào dám cản đường ta... sẽ phải trả giá đắt.",
        "settings": {"stability": 0.50, "similarity_boost": 0.75, "style": 0.20, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.05, "rate": 1.0, "gender": "male"}
    },
    "lady_diana": {
        "voice_id": "EXAVITQu4vr4xnSDxMaL",  # Bella - Delicate, aristocratic bride
        "name": "Bella (Tiểu thư kiêu sa)",
        "gender": "female",
        "description": "Giọng nữ trẻ tuổi, thanh tú, kiêu kỳ nhưng ẩn chứa sự nhạy bén của cô dâu quý tộc.",
        "sample_quote": "Một cuộc hôn nhân môn đăng hộ đối. Nhưng trong lâu đài cổ kính này, ta cảm giác có điều gì đó không bình thường...",
        "settings": {"stability": 0.55, "similarity_boost": 0.75, "style": 0.15, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.1, "rate": 1.0, "gender": "female"}
    },
    "maid_marilyn": {
        "voice_id": "pFZP5JQG7iQjIQuC4Bku",  # Lily - Lively, animated servant
        "name": "Lily (Hầu nữ nhanh nhẹn)",
        "gender": "female",
        "description": "Giọng nữ lanh lợi, hoạt bát, hay tò mò và nhiều chuyện của hầu nữ thân cận.",
        "sample_quote": "Suỵt! Ngài có nghe thấy tiếng động lạ ở hành lang tối không? Tôi vừa thấy bóng ai đó lén lút qua phòng ngủ!",
        "settings": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.25, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.15, "rate": 1.05, "gender": "female"}
    },
    "sir_cameron": {
        "voice_id": "VR6AewLTigWG4xSOukaG",  # Arnold - Crisp, stern warrior
        "name": "Arnold (Hiệp sĩ dũng mãnh)",
        "gender": "male",
        "description": "Giọng nam rắn rỏi, hào sảng, kiên định của hiệp sĩ bất khả chiến bại.",
        "sample_quote": "Thanh kiếm của ta chỉ phục vụ cho danh dự và công lý. Kẻ nào làm vấy bẩn danh dự triều đình, ta sẽ trừng phạt!",
        "settings": {"stability": 0.60, "similarity_boost": 0.80, "style": 0.15, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.8, "rate": 0.95, "gender": "male"}
    },
    "sir_rufus": {
        "voice_id": "ErXwobaYiN019PkySvjV",  # Antoni - Expressive, swaggering knight
        "name": "Antoni (Hiệp sĩ ngạo mạn)",
        "gender": "male",
        "description": "Giọng nam cao ngạo, hợm hĩnh và tham lam của hiệp sĩ kiêm cai thuế lâu đài.",
        "sample_quote": "Ha ha! Lâu đài này nếu không có ta thu gom từng đồng thuế thì lấy đâu ra yến tiệc xa hoa thế này? Hãy kính trọng ta đi!",
        "settings": {"stability": 0.40, "similarity_boost": 0.70, "style": 0.30, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.0, "rate": 1.0, "gender": "male"}
    },
    "joking_jerry": {
        "voice_id": "IKne3meq5aSn9XLyUdCD",  # Charlie - Playful, teasing jester
        "name": "Charlie (Hề hoàng gia tinh quái)",
        "gender": "male",
        "description": "Giọng nam tinh quái, mỉa mai, dí dỏm của chàng hề triều đình.",
        "sample_quote": "Haha! Kẻ ngu ngốc nhất đôi khi lại là người sáng suốt nhất. Lâu đài này đầy rẫy những kẻ đạo đức giả mang mặt nạ!",
        "settings": {"stability": 0.35, "similarity_boost": 0.70, "style": 0.35, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.25, "rate": 1.1, "gender": "male"}
    },
    "willie_watchman": {
        "voice_id": "yoZ06aMxZJJ28mfd3POQ",  # Sam - Raspy, gruff watchman
        "name": "Sam (Quan tuần tra nghiêm nghị)",
        "gender": "male",
        "description": "Giọng nam già dặn, khàn đục, cảnh giác cao độ của người gác thành lâu đài.",
        "sample_quote": "Đêm nay gió rít dữ dội. Mọi ngóc ngách của Fernwood đều phải được canh gác cẩn mật, không kẻ mờ ám nào lọt qua được mắt ta.",
        "settings": {"stability": 0.70, "similarity_boost": 0.80, "style": 0.10, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.75, "rate": 0.9, "gender": "male"}
    },
    "charlamagne": {
        "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Rachel - Calm, prim housekeeper
        "name": "Rachel (Hầu phòng mẫu mực)",
        "gender": "female",
        "description": "Giọng nữ điềm đạm, nghiêm cẩn, chỉn chu đến từng chi tiết của trưởng quản việc nội trị.",
        "sample_quote": "Mọi sự trong yến tiệc phải đúng theo quy củ hoàng gia. Từng chiếc ly, từng ngọn nến đều không được phép có sai sót.",
        "settings": {"stability": 0.70, "similarity_boost": 0.80, "style": 0.05, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.0, "rate": 0.95, "gender": "female"}
    },
    "maid_monica": {
        "voice_id": "MF3mGyEYCl7XYWbV9V6O",  # Elli - Emotional, gentle maid
        "name": "Elli (Hầu nữ nhút nhát)",
        "gender": "female",
        "description": "Giọng nữ e ấp, rụt rè, dễ hoảng sợ của cô hầu phòng non nớt.",
        "sample_quote": "Em... em rất sợ... có ai đó đang thì thầm những điều kinh khủng trong bóng tối sau bức màn...",
        "settings": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.25, "use_speaker_boost": True},
        "web_speech": {"pitch": 1.2, "rate": 1.0, "gender": "female"}
    },
    "system": {
        "voice_id": "nPczCjzI2devNBz1zQrb",  # Brian - Deep, dramatic narrator / herald
        "name": "Brian (Sứ giả triều đình)",
        "gender": "male",
        "description": "Giọng nam trầm ấm, hùng hồn và truyền cảm của người dẫn truyện kiêm sứ giả hoàng gia.",
        "sample_quote": "Truyền lệnh Lâu đài! Hãy lắng nghe lời phán quyết và lời tuyên cáo từ Tòa án Hoàng gia!",
        "settings": {"stability": 0.70, "similarity_boost": 0.85, "style": 0.15, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.8, "rate": 0.95, "gender": "male"}
    },
    "herald": {
        "voice_id": "nPczCjzI2devNBz1zQrb",  # Same as system / herald
        "name": "Brian (Sứ giả triều đình)",
        "gender": "male",
        "description": "Giọng nam trầm ấm, hùng hồn và truyền cảm của người dẫn truyện kiêm sứ giả hoàng gia.",
        "sample_quote": "Truyền lệnh Lâu đài! Hãy lắng nghe lời phán quyết và lời tuyên cáo từ Tòa án Hoàng gia!",
        "settings": {"stability": 0.70, "similarity_boost": 0.85, "style": 0.15, "use_speaker_boost": True},
        "web_speech": {"pitch": 0.8, "rate": 0.95, "gender": "male"}
    }
}

def clean_text_for_tts(text: str) -> str:
    """Clean text by removing stage actions, markdown markers, and symbols for natural voice acting."""
    if not text:
        return ""
    
    # 1. Remove text inside asterisks (stage actions like *[cười khẩy]*, *uống rượu*)
    cleaned = re.sub(r"\*[^*]*\*", "", text)
    
    # 2. Remove text inside brackets (e.g. [TUYÊN BỐ], [HỐI LỘ])
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)
    
    # 3. Remove common parenthetical roleplay stage cues (e.g. (thì thầm), (nói nhỏ))
    cleaned = re.sub(r"\((?:thì thầm|nói nhỏ|cười|nghĩ thầm|lẩm bẩm|gật đầu|nhìn quanh)[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    
    # 4. Remove Markdown bold/italic/headers
    cleaned = re.sub(r"[#*_~`>]", "", cleaned)
    
    # 5. Remove multiple quotes or extra symbols
    cleaned = cleaned.replace('"', '').replace('“', '').replace('”', '').replace("'", "")
    
    # 6. Normalize whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    # If cleaning stripped everything (e.g. text was purely an action), fallback to original without brackets
    if len(cleaned) < 3:
        cleaned = re.sub(r"[\[\]\(\)\*]", "", text).strip()
        
    return cleaned

class TTSEngine:
    def __init__(self, cache_dir: str = "static/audio_cache"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        # Create a .gitkeep if not present
        gitkeep_path = os.path.join(self.cache_dir, ".gitkeep")
        if not os.path.exists(gitkeep_path):
            with open(gitkeep_path, "w", encoding="utf-8") as f:
                f.write("")

    def get_voice_info(self, character_id: str) -> Dict[str, Any]:
        """Get voice configuration for a character, defaulting to 'system' voice if unknown."""
        return CHARACTER_VOICES.get(character_id, CHARACTER_VOICES["system"])

    def get_all_voices(self) -> Dict[str, Any]:
        """Return public metadata of all available voices."""
        return {
            cid: {
                "name": v["name"],
                "gender": v["gender"],
                "description": v["description"],
                "sample_quote": v["sample_quote"],
                "web_speech": v["web_speech"]
            }
            for cid, v in CHARACTER_VOICES.items()
        }

    def compute_cache_hash(self, character_id: str, text: str) -> str:
        """Generate deterministic MD5 cache key based on character and cleaned text."""
        cleaned = clean_text_for_tts(text)
        key = f"{character_id}:{cleaned}"
        return hashlib.md5(key.encode("utf-8")).hexdigest()

    def get_cached_audio_path(self, character_id: str, text: str) -> Optional[str]:
        """Check if an audio file is already cached on disk."""
        h = self.compute_cache_hash(character_id, text)
        filename = f"{h}.mp3"
        filepath = os.path.join(self.cache_dir, filename)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return f"/static/audio_cache/{filename}"
        return None

    async def generate_speech(self, character_id: str, text: str, api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate or retrieve voice audio for a character.
        If cached, returns cached URL immediately.
        If not cached and api_key is valid, generates via ElevenLabs API and writes to disk.
        If no api_key or error, returns fallback metadata for Web Speech API.
        """
        voice_meta = self.get_voice_info(character_id)
        cleaned_text = clean_text_for_tts(text)

        if not cleaned_text:
            return {
                "success": False,
                "audio_url": None,
                "fallback": "web_speech",
                "voice_settings": voice_meta["web_speech"],
                "message": "Empty cleaned text"
            }

        # Check disk cache first
        cached_url = self.get_cached_audio_path(character_id, text)
        if cached_url:
            return {
                "success": True,
                "audio_url": cached_url,
                "cached": True,
                "text": cleaned_text,
                "voice_name": voice_meta["name"]
            }

        # If no ElevenLabs API key, signal browser fallback
        effective_key = (api_key or os.getenv("ELEVENLABS_API_KEY") or "").strip()
        if not effective_key:
            return {
                "success": False,
                "audio_url": None,
                "cached": False,
                "fallback": "web_speech",
                "cleaned_text": cleaned_text,
                "voice_settings": voice_meta["web_speech"],
                "message": "No ElevenLabs API key configured"
            }

        # Generate audio using ElevenLabs multilingual model
        voice_id = voice_meta["voice_id"]
        h = self.compute_cache_hash(character_id, text)
        filename = f"{h}.mp3"
        filepath = os.path.join(self.cache_dir, filename)

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": effective_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg"
        }
        payload = {
            "text": cleaned_text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": voice_meta.get("settings", {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True
            })
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200 and len(resp.content) > 0:
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                    logger.info(f"Generated and cached voice for {character_id} ({len(resp.content)} bytes) -> {filename}")
                    return {
                        "success": True,
                        "audio_url": f"/static/audio_cache/{filename}",
                        "cached": False,
                        "text": cleaned_text,
                        "voice_name": voice_meta["name"]
                    }
                else:
                    logger.warning(f"ElevenLabs API returned {resp.status_code}: {resp.text[:200]}")
                    return {
                        "success": False,
                        "audio_url": None,
                        "cached": False,
                        "fallback": "web_speech",
                        "cleaned_text": cleaned_text,
                        "voice_settings": voice_meta["web_speech"],
                        "error": f"ElevenLabs API error: {resp.status_code}"
                    }
        except Exception as e:
            logger.error(f"Error calling ElevenLabs API for {character_id}: {e}")
            return {
                "success": False,
                "audio_url": None,
                "cached": False,
                "fallback": "web_speech",
                "cleaned_text": cleaned_text,
                "voice_settings": voice_meta["web_speech"],
                "error": str(e)
            }
