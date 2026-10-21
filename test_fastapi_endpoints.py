import sys
import asyncio
import httpx
from server import app

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

async def test_endpoints():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Game data
        r_gd = await client.get("/api/game-data")
        assert r_gd.status_code == 200, f"game-data status {r_gd.status_code}"
        gd_json = r_gd.json()
        assert "has_elevenlabs_key" in gd_json
        assert "tts_enabled" in gd_json
        print("✓ /api/game-data verified successfully!")

        # 2. TTS voices
        r_v = await client.get("/api/tts/voices")
        assert r_v.status_code == 200
        v_json = r_v.json()
        assert "voices" in v_json
        assert len(v_json["voices"]) >= 12
        print(f"✓ /api/tts/voices returned {len(v_json['voices'])} voices!")

        # 3. TTS speech generation (fallback mode)
        r_tts = await client.post("/api/tts", json={
            "character_id": "lord_taylor",
            "text": "Chào mừng chư vị đến Lâu đài Fernwood!"
        })
        assert r_tts.status_code == 200
        tts_json = r_tts.json()
        assert tts_json["fallback"] == "web_speech"
        assert tts_json["voice_settings"]["pitch"] == 0.85
        print("✓ /api/tts fallback verified successfully!")

        # 4. Settings update
        r_set = await client.post("/api/settings", json={
            "provider": "gemini",
            "elevenlabs_api_key": "test_elevenlabs_key_123",
            "tts_enabled": True
        })
        assert r_set.status_code == 200
        print("✓ /api/settings update verified successfully!")

        # 5. Verify game-data reflects the new settings
        r_gd2 = await client.get("/api/game-data")
        assert r_gd2.status_code == 200
        assert r_gd2.json()["has_elevenlabs_key"] is True
        print("✓ Updated ElevenLabs key state verified in /api/game-data!")

    print("\nALL FASTAPI TTS INTEGRATION TESTS PASSED! 🏰🎙️")

if __name__ == "__main__":
    asyncio.run(test_endpoints())
