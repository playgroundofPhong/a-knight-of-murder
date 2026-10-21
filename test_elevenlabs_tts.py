import asyncio
import os
import sys
import json

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from tts_engine import TTSEngine, CHARACTER_VOICES, clean_text_for_tts
from game_state import GameStateManager

def test_tts_engine_character_coverage():
    engine = TTSEngine(cache_dir="static/audio_cache")
    all_voices = engine.get_all_voices()
    
    # 12 characters + system + herald
    expected_characters = [
        "queen_genevieve", "lord_taylor", "lady_gwendolyn", "baron_bartholomew",
        "lady_diana", "maid_marilyn", "sir_cameron", "sir_rufus",
        "joking_jerry", "willie_watchman", "charlamagne", "maid_monica",
        "system", "herald"
    ]
    for cid in expected_characters:
        assert cid in all_voices, f"Missing voice for {cid}"
        v = all_voices[cid]
        assert "name" in v
        assert "gender" in v
        assert "sample_quote" in v
        assert "web_speech" in v
        assert v["web_speech"]["pitch"] > 0
        assert v["web_speech"]["rate"] > 0

def test_text_cleaning_for_tts():
    raw_1 = '*[cười khẩy rồi nâng chén rượu]* "Chào mừng ngài đến Fernwood!"'
    cleaned_1 = clean_text_for_tts(raw_1)
    assert cleaned_1 == "Chào mừng ngài đến Fernwood!"
    assert "*" not in cleaned_1
    assert "[" not in cleaned_1

    raw_2 = '(thì thầm với Taylor) Kẻ đó có mang theo ám khí không?'
    cleaned_2 = clean_text_for_tts(raw_2)
    assert "Kẻ đó có mang theo ám khí không?" in cleaned_2
    assert "thì thầm" not in cleaned_2

    raw_3 = '**LƯU Ý:** [TUYÊN BỐ] *Hết giờ*'
    cleaned_3 = clean_text_for_tts(raw_3)
    assert "**" not in cleaned_3
    assert "[" not in cleaned_3

    raw_empty = '*[thở dài]*'
    cleaned_empty = clean_text_for_tts(raw_empty)
    assert len(cleaned_empty) > 0  # Fallback to non-empty text

async def test_tts_fallback_when_no_api_key():
    engine = TTSEngine(cache_dir="static/audio_cache")
    res = await engine.generate_speech("queen_genevieve", "Trẫm muốn mọi sự minh bạch!", api_key="")
    assert res["success"] is False
    assert res["fallback"] == "web_speech"
    assert "voice_settings" in res
    assert res["voice_settings"]["gender"] == "female"
    assert res["voice_settings"]["pitch"] == 0.9

async def test_tts_caching():
    engine = TTSEngine(cache_dir="static/audio_cache")
    cid = "lord_taylor"
    text = "Rượu nho hảo hạng từ phương Nam đã sẵn sàng!"
    h = engine.compute_cache_hash(cid, text)
    cache_path = os.path.join("static/audio_cache", f"{h}.mp3")
    
    # Simulate an existing cached file
    with open(cache_path, "wb") as f:
        f.write(b"ID3FakeAudioBytesForTesting1234567890")
    
    try:
        res = await engine.generate_speech(cid, text, api_key="")
        assert res["success"] is True
        assert res["cached"] is True
        assert res["audio_url"] == f"/static/audio_cache/{h}.mp3"
    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)

async def test_game_state_tts_persistence():
    gs = GameStateManager(state_file="data/test_room_state.json")
    gs.elevenlabs_api_key = "test_elevenlabs_xi_key_999"
    gs.tts_enabled = True
    gs.save_state()

    gs_loaded = GameStateManager(state_file="data/test_room_state.json")
    assert gs_loaded.elevenlabs_api_key == "test_elevenlabs_xi_key_999"
    assert gs_loaded.tts_enabled is True
    
    if os.path.exists("data/test_room_state.json"):
        os.remove("data/test_room_state.json")

def main():
    print("Testing character voices coverage...")
    test_tts_engine_character_coverage()
    print("✓ Character coverage passed!")

    print("Testing text cleaning for TTS...")
    test_text_cleaning_for_tts()
    print("✓ Text cleaning passed!")

    print("Testing fallback mode...")
    asyncio.run(test_tts_fallback_when_no_api_key())
    print("✓ Fallback passed!")

    print("Testing caching...")
    asyncio.run(test_tts_caching())
    print("✓ Caching passed!")

    print("Testing persistence...")
    asyncio.run(test_game_state_tts_persistence())
    print("✓ Persistence passed!")

    print("\nALL TTS TESTS PASSED SUCCESSFULLY! 👑🎙️")

if __name__ == "__main__":
    main()
