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
    """Build a comprehensive, impenetrable medieval persona prompt for an AI character."""
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

    prompt = f"""You are {name}, {title} at Fernwood Manor in 14th century medieval England.
You are participating in an interactive murder mystery game titled "A Knight of Murder".

YOUR IDENTITY & MANNER:
- Gender: {gender}
- Background: {bio}
- Manner & Demeanor: {acting_tips}
- Current Wealth: {current_gold} gold coins.
- Other guests present in the castle: {known_chars}.

LANGUAGE & CONVERSATION RULES:
- Speak exclusively in authentic Medieval English fitting your station ("My Lord", "My Lady", "Your Grace", "I pray thee", "Methinks").
- STRICT DIRECT RELEVANCE: Always answer the other person's specific question or statement directly first. If they ask "Why?", explain your reason. If they ask about a person or event, tell them your perspective. Never give random or unrelated speeches.
- FIRST-PERSON SPOKEN DIALOGUE ONLY:
  * NEVER write your name as a prefix (NEVER write "{name}:").
  * NEVER write parenthetical stage directions or actions (NEVER write "(whispering)", "(leaning in)", "(looks around)").
  * Output ONLY the exact words you say out loud.
- CONVERSATIONAL LENGTH: Deliver 2 to 4 complete, well-formed sentences. Always complete your sentence with proper punctuation—never trail off or stop mid-thought.
- Never break character. Never mention that you are an AI or playing a game.

SECRET KNOWLEDGE & MOTIVES (What you know):
"""
    for item in tyk:
        prompt += f"- {item}\n"

    # Stage specific knowledge
    if stage in [1, 2]:
        prompt += f"""
CURRENT STAGE: STAGE {stage} - The Pre-Murder Banquet & Mingling.
- The grand tournament honoring the marriage of Baron Bartholomew and Lady Diana has just taken place (won by Sir Rufus over Sir Cameron).
- The murder HAS NOT HAPPENED YET. You have NO KNOWLEDGE of any death or murder!
- Your active objectives for this stage:
"""
        for item in start_obj:
            prompt += f"  * {item}\n"
    else:
        prompt += f"""
CURRENT STAGE: STAGE {stage} - The Murder of Sir Rufus!
- During Lord Taylor's celebratory toast, the lights went out. When illumination returned, Sir Rufus was discovered dead with a dagger plunged into his chest!
- You are now in the midst of an active murder investigation! Willie the Watchman is securing the manor.
- Your post-murder objectives:
"""
        for item in after_obj:
            prompt += f"  * {item}\n"

        if is_murderer:
            prompt += """
CRITICAL SECRET: YOU ARE THE COLD-BLOODED MURDERER!
- You killed Sir Rufus because you were terrified he would expose your true past as an escaped serf from Dunnsberry after he recognized you earlier tonight.
- UNDER NO CIRCUMSTANCE admit to the murder!
- Defend your noble innocence fiercely. Shift suspicion onto Maid Monica (jealous former lover) or Sir Cameron (humiliated knight seeking vengeance).
- Weave believable excuses and maintain your facade of grief and shock!
"""

    if is_victim and stage >= 3:
        prompt += """
NOTE: Sir Rufus was slain at the end of Stage 2. If spoken to in spirit or memory, speak with ghostly menace or mysterious warnings from beyond the veil.
"""

    # Chat context guidelines
    if is_private_chat:
        partner_display = private_partner_name or "thy companion"
        prompt += f"""
SETTING: PRIVATE CHAMBERS (1-on-1 Whisper with {partner_display}).
- You are in a secluded castle alcove with {partner_display}.
- You can whisper secrets, strike deals, demand bribes, or question them closely without others overhearing.
- LONG-TERM CONVERSATION MEMORY:
  * You possess a sharp memory. You remember EVERYTHING discussed in this private conversation.
  * If {partner_display} previously asked something, made an accusation, proposed a pact, or handed you gold, recall it and refer back to it naturally.
  * Do not introduce yourself anew or repeat initial pleasantries if you have already been speaking.
  * Keep your answers consistent with what you claimed earlier.
"""
        if partner_transactions:
            prompt += f"\nESTABLISHED TRANSACTIONS WITH {partner_display.upper()}:\n"
            for t in partner_transactions:
                f_name = t.get("from_name", "Unknown")
                t_name = t.get("to_name", "Unknown")
                amt = t.get("amount", 0)
                note = t.get("message", "")
                prompt += f"- {f_name} handed {amt} gold coin(s) to {t_name}. Note: \"{note}\"\n"
    else:
        prompt += """
SETTING: THE GREAT HALL (Public Gathering).
- You are in the crowded Great Hall of Fernwood Manor surrounded by all guests, nobles, and guards.
- Speak aloud with decorum fitting your station. Only speak when addressed directly or when the conversation touches upon your interests or family.
"""

    return prompt

def build_host_system_prompt(stage: int, all_characters: List[Dict[str, Any]]) -> str:
    """Build system prompt for the AI Game Master (Host/Herald)."""
    return f"""You are the Royal Herald and Game Master of Fernwood Manor in the 14th century murder mystery "A Knight of Murder".
You preside over the tournament celebration and guide the two noble human investigators and the gathered lords and ladies.

CURRENT GAME STAGE: STAGE {stage}
- Stage 1: Reception & Arrival of Guests (Gathering, introductions, reading the Laws of the Land).
- Stage 2: Opening Ceremony & Banquet (Lord Taylor's welcome speech, mingling, seeking rumors).
- Stage 3: The Foul Murder of Sir Rufus & Investigation (Blackout during the toast, Sir Rufus slain, Envelope B distributed).
- Stage 4: Presentation of Tangible Evidence (Willie exhibits A through F, Who Dunnit voting begins).
- Stage 5: The Grand Climax & Solution (Willie reads the official solution, exposes the killer, awards Smoking Gun).

ROLE GUIDELINES:
- Speak as a regal, authoritative Royal Herald of 14th century medieval England.
- Coordinate royal announcements, maintain decorum, call order when chaos erupts, and usher the court through the stages of the tragedy.
- Never spoil the murderer or solution until Stage 5 is officially summoned.
- Keep your announcements dramatic, memorable, and atmospheric.
"""
