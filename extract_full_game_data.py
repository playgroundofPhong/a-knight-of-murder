import docx
import pypdf
import json
import re
import os

def clean_text(text):
    text = text.replace('’', "'").replace('‘', "'").replace('`', "'")
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('–', '-').replace('—', '-')
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def clean_bullet(b):
    b = re.sub(r'©\d+.*', '', b)
    b = re.sub(r'Night of Mystery.*', '', b, flags=re.I)
    b = re.sub(r'www\.aknightofmurder\.com|www\.nightofmystery\.com', '', b, flags=re.I)
    b = re.sub(r'\s+', ' ', b).strip()
    
    # Fix drop cap OCR artifacts
    b = re.sub(r'^W hen\b|^hen\b', 'When', b)
    b = re.sub(r'^W hom\b|^hom\b', 'Whom', b)
    b = re.sub(r'^W ith\b|^ith\b', 'With', b)
    b = re.sub(r'illie the Watchperson when he asks\.give it only to W', 'give it only to Willie the Watchperson when he asks.', b)
    b = re.sub(r'ONE\.\s*•\s*TONIGHT!!! Tell NO YOU ARE THE VICTIM\s*•', 'YOU ARE THE VICTIM TONIGHT! Tell NO ONE.', b)
    b = re.sub(r'•', '', b).strip()
    return b

def extract_all():
    doc = docx.Document('a-knight-of-murder-8-12-guests.docx')
    lines = [clean_text(p.text) for p in doc.paragraphs if p.text.strip()]

    # 1. Speeches & Documents
    speeches = {}
    
    # Introduction (read by Lord Taylor)
    intro_lines = []
    start_intro = False
    for i, l in enumerate(lines):
        if 'introduction to the guests' in l.lower() or 'read by lord taylor after all of the guests arrive' in l.lower():
            start_intro = True
            continue
        if start_intro:
            if '©' in l or 'queen genevieve' in l.lower() or 'party objectives' in l.lower():
                break
            intro_lines.append(l)
    speeches['introduction'] = "\n\n".join(intro_lines)

    # Investigation (Stage 3, read by Willie)
    investigation_lines = []
    start_inv = False
    for i, l in enumerate(lines):
        if 'as you can all see, there was a murder' in l.lower():
            start_inv = True
        if start_inv:
            if '©' in l or 'queen genevieve' in l.lower() or 'party objectives' in l.lower() or 'exhibit' in l.lower():
                if len(investigation_lines) >= 3:
                    break
            investigation_lines.append(l)
    speeches['investigation'] = "\n\n".join(investigation_lines)

    # Toast (Stage 3, Lord Taylor)
    speeches['toast'] = (
        "Friends, nobles, and honored guests of Fernwood! "
        "I call upon all of you to raise your goblets high in honor of my son Baron Bartholomew "
        "and his lovely bride, Lady Diana of Dunnsberry! May their union bring everlasting prosperity, "
        "peace, and glory to the manor of Fernwood! To the bride and groom!"
    )

    # Evidence Presentation (Stage 4, Willie)
    ev_pres_lines = []
    start_ev = False
    for i, l in enumerate(lines):
        if 'after review of all the facts of this murder and conducting my own investigation' in l.lower():
            start_ev = True
        if start_ev:
            if 'exhibit a' in l.lower() and 'description:' in lines[min(len(lines)-1, i+1)].lower():
                break
            ev_pres_lines.append(l)
    speeches['evidence_presentation'] = "\n\n".join(ev_pres_lines)

    # Solution (Stage 5, Willie)
    sol_lines = []
    start_sol = False
    for i, l in enumerate(lines):
        if 'solution' in l.lower() and 'read by willie the watchperson' in lines[min(len(lines)-1, i+1)].lower():
            start_sol = True
            continue
        if start_sol:
            if 'congratulations!' in l.lower() or 'smoking gun' in l.lower():
                break
            sol_lines.append(l)
    speeches['solution'] = "\n\n".join(sol_lines)

    # Laws of the Land
    laws_lines = []
    start_laws = False
    for i, l in enumerate(lines):
        if 'laws of the land as set forth by queen genevieve' in l.lower():
            start_laws = True
        if start_laws:
            if '©' in l or 'exhibit d' in l.lower() or 'tournament celebration' in l.lower():
                if len(laws_lines) >= 5:
                    break
            laws_lines.append(l)
    speeches['laws_of_the_land'] = "\n\n".join(laws_lines)

    # 2. Exhibits
    exhibits = [
        {
            "id": "A",
            "title": "EXHIBIT A: The Murder Weapon",
            "description": "The murder weapon -- a jeweled dagger in the heart.",
            "notes": "Found lodged in the victim's chest, cause of death. Upon close examination, it bears the distinct coat of arms of the manor of Dunnsberry.",
            "held_by": "Willie the Watchman"
        },
        {
            "id": "B",
            "title": "EXHIBIT B: Personal Items on the Victim",
            "description": "Personal items found on the victim at the time of the crime.",
            "notes": "Among these items is a silk handkerchief belonging to Maid Marilyn, and coins from both Fernwood and Dunnsberry.",
            "held_by": "Willie the Watchman"
        },
        {
            "id": "C",
            "title": "EXHIBIT C: The Laws of the Land",
            "description": "The royal decree and laws of Queen Genevieve.",
            "notes": "Clause 5b states the victorious knight may request the favor of any woman at the celebration. Also states a serf can gain independence only by escaping to another manor and living there for one year plus one day.",
            "held_by": "Queen Genevieve"
        },
        {
            "id": "D",
            "title": "EXHIBIT D: Bottle of Poison",
            "description": "A glass vial found in the stall of Sir Cameron's warhorse, Stallion.",
            "notes": "Poison is harmless to humans, but fatal or debilitating if ingested by horses. Used to sabotage Sir Cameron during the joust.",
            "held_by": "Sir Cameron"
        },
        {
            "id": "E",
            "title": "EXHIBIT E: Threatening Note to Maid Monica",
            "description": "A threatening letter sent to Maid Monica.",
            "notes": "Shows that Sir Rufus had been terrorizing and threatening local families in the manor to extort 15% taxes.",
            "held_by": "Maid Monica"
        },
        {
            "id": "F",
            "title": "EXHIBIT F: Blackmail Note to Baron Bartholomew",
            "description": "A blackmail note confiscated from Baron Bartholomew.",
            "notes": "Reveals that Sir Rufus was blackmailing the Baron, hinting that he knew the truth about Lady Diana not being a true noblewoman.",
            "held_by": "Baron Bartholomew"
        }
    ]

    # 3. Characters metadata & block mappings
    char_configs = [
        {
            "id": "queen_genevieve",
            "name": "Queen Genevieve",
            "title": "Queen of the Kingdom",
            "gender": "Female",
            "avatar_icon": "👑",
            "pdf_name": "Queen Genevieve - Queen.pdf",
            "search_term": "Queen Genevieve",
            "coins": 12,
            "brief": "Ruler of the prosperous kingdom. Benevolent, fair-minded, but fiercely protective of her royal treasury. Betrayal is punishable by death.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "lord_taylor",
            "name": "Lord Taylor",
            "title": "Lord of Fernwood Manor",
            "gender": "Male",
            "avatar_icon": "🏰",
            "pdf_name": "Lord Taylor - Lord of the Manor.pdf",
            "search_term": "Lord Taylor",
            "coins": 12,
            "brief": "Ruler of Fernwood Manor. Authoritative, protective of his lands, and host of his son's lavish wedding tournament.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "lady_gwendolyn",
            "name": "Lady Gwendolyn",
            "title": "Lady of Fernwood Manor",
            "gender": "Female",
            "avatar_icon": "🌹",
            "pdf_name": "Lady Gwendolyn - Lady of the Manor.pdf",
            "search_term": "Lady Gwendolyn",
            "coins": 12,
            "brief": "Lady of the Manor, mother to the groom. Sophisticated, aristocratic, watchful of castle affairs and marriages.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "baron_bartholomew",
            "name": "Baron Bartholomew",
            "title": "Son to Lord Taylor & Lady Gwendolyn",
            "gender": "Male",
            "avatar_icon": "🍷",
            "pdf_name": "Baron Bartholomew - Son to Lord Taylor and Lady Gwendolyn.pdf",
            "search_term": "Baron Bartholomew",
            "coins": 12,
            "brief": "The groom-to-be. Handsome and wealthy noble, torn between his duty to marry Lady Diana and his secret passion for Maid Monica.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "lady_diana",
            "name": "Lady Diana of Dunnsberry",
            "title": "Bride-to-be / Noblewoman",
            "gender": "Female",
            "avatar_icon": "👰",
            "pdf_name": "Lady Diana of Dunnsberry - Noblewoman.pdf",
            "search_term": "Lady Diana",
            "coins": 12,
            "brief": "The bride-to-be from Dunnsberry. Beautiful and charming, but harboring an explosive secret about her true identity.",
            "is_victim": False,
            "is_murderer": True
        },
        {
            "id": "maid_marilyn",
            "name": "Maid Marilyn",
            "title": "Lady Gwendolyn's Lady-in-Waiting",
            "gender": "Female",
            "avatar_icon": "🎀",
            "pdf_name": "Maid Marilyn - Lady Gwendolyn’s Lady-In-Waiting.pdf",
            "search_term": "arilyn",
            "coins": 12,
            "brief": "Lady Gwendolyn's trusted confidante. Sweet, well-liked, infatuated with Sir Cameron, and cornered by Sir Rufus.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "sir_cameron",
            "name": "Sir Cameron",
            "title": "Knight of the Realm",
            "gender": "Male",
            "avatar_icon": "⚔️",
            "pdf_name": "Sir Cameron - Knight.pdf",
            "search_term": "Sir Cameron",
            "coins": 12,
            "brief": "The manor's champion knight. Chivalrous and undefeated until today's shocking joust defeat. Convinced of foul play.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "sir_rufus",
            "name": "Sir Rufus",
            "title": "Knight & Tax Collector (Victim)",
            "gender": "Male",
            "avatar_icon": "🛡️",
            "pdf_name": "Sir Rufus - Knight.pdf",
            "search_term": "Sir Rufus",
            "coins": 12,
            "brief": "Corrupt knight and tax collector. Arrogant, greedy, blackmailing nobles, and winner of today's joust.",
            "is_victim": True,
            "is_murderer": False
        },
        {
            "id": "joking_jerry",
            "name": "Joking Jerry",
            "title": "Court Jester",
            "gender": "Male",
            "avatar_icon": "🎭",
            "pdf_name": "Joking Jerry - Court Jester.pdf",
            "search_term": "Joking Jerry",
            "coins": 12,
            "brief": "The castle jester. Wit, humor, and mockery disguise an astute observer who hears everything whispered in the halls.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "willie_watchman",
            "name": "Willie the Watchman",
            "title": "Manor Watchperson & Investigator",
            "gender": "Male",
            "avatar_icon": "🗝️",
            "pdf_name": "Willie the Watchman - Manor Watchperson..pdf",
            "search_term": "atchperson",
            "coins": 12,
            "brief": "Guardian of the manor and chief investigator. Serious, vigilant, and determined to bring the killer to medieval justice.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "charlamagne",
            "name": "Charlamagne",
            "title": "Chambermaid",
            "gender": "Female",
            "avatar_icon": "🧹",
            "pdf_name": "Charlamagne - Chambermaid.pdf",
            "search_term": "Charlamagne",
            "coins": 12,
            "brief": "Castle chambermaid with access to all private quarters. Smitten with Sir Rufus and entangled in secret castle plots.",
            "is_victim": False,
            "is_murderer": False
        },
        {
            "id": "maid_monica",
            "name": "Maid Monica",
            "title": "Castle Maid",
            "gender": "Female",
            "avatar_icon": "💔",
            "pdf_name": "Maid Monica - Maid.pdf",
            "search_term": "onica",
            "coins": 12,
            "brief": "A peasant maid and Baron Bartholomew's former love. Heartbroken over his noble engagement and threatened with marriage to Sir Rufus.",
            "is_victim": False,
            "is_murderer": False
        }
    ]

    characters = []

    pdf_dir = 'a-knight-of-murder-8-12-guests/characters'

    for cfg in char_configs:
        # Extract Bio from PDF
        pdf_path = os.path.join(pdf_dir, cfg['pdf_name'])
        bio = ""
        acting = ""
        if os.path.exists(pdf_path):
            try:
                reader = pypdf.PdfReader(pdf_path)
                if len(reader.pages) >= 5:
                    page5 = reader.pages[4].extract_text()
                    page5_clean = clean_text(page5)
                    # Split bio and acting tips
                    m = re.search(r'Acting and Dressing Your Part:?(.*)', page5_clean, re.I | re.S)
                    if m:
                        acting = m.group(1).strip()
                        bio_part = page5_clean[:m.start()]
                    else:
                        bio_part = page5_clean
                    
                    bio_part = re.sub(r'©\d+.*|Your Character.*?' + re.escape(cfg['name']), '', bio_part, flags=re.I).strip()
                    bio = bio_part
            except Exception as e:
                print(f"Error reading PDF {pdf_path}: {e}")

        # Locate Docx blocks for objectives
        block_indices = []
        for i, l in enumerate(lines):
            if 'party objectives' in l.lower():
                chunk = ' '.join(lines[i:i+4])
                if cfg['search_term'].lower() in chunk.lower():
                    block_indices.append(i)

        things_you_know = []
        after_murder_objectives = []
        start_objectives = []

        if len(block_indices) >= 2:
            idx1 = block_indices[0]
            idx2 = block_indices[1]
            end_idx = min(len(lines), idx2 + 45)

            chunk1 = ' '.join(lines[idx1:idx2])
            chunk2 = ' '.join(lines[idx2:end_idx])

            # Split chunk 1 into Things You Know & After Murder
            m_split = re.search(r'urder:\s*Objectives After The M|Objectives After The Murder', chunk1, re.I)
            if m_split:
                tyk_text = chunk1[:m_split.start()]
                after_text = chunk1[m_split.end():]
            else:
                tyk_text = chunk1
                after_text = ""

            # Things you know bullets
            raw_tyk = [clean_bullet(b) for b in tyk_text.split('•') if clean_bullet(b)]
            for b in raw_tyk:
                if len(b) > 10 and not any(kw in b.lower() for kw in ['party objectives', 'things you know', 'maintain your innocence']):
                    things_you_know.append(b)

            # After murder bullets
            raw_after = [clean_bullet(b) for b in after_text.split('•') if clean_bullet(b)]
            for b in raw_after:
                if len(b) > 10 and not any(kw in b.lower() for kw in ['party objectives', 'objectives after', 'doing as well']):
                    after_murder_objectives.append(b)

            # Start objectives bullets from chunk 2
            raw_start = [clean_bullet(b) for b in chunk2.split('•') if clean_bullet(b)]
            for b in raw_start:
                if len(b) > 10 and not any(kw in b.lower() for kw in ['party objectives', 'objectives at the start', 'doing as well']):
                    start_objectives.append(b)

        # For Lady Diana, ensure murderer instructions are preserved in Envelope B
        murderer_note = None
        if cfg['is_murderer']:
            murderer_note = (
                "YOU ARE THE MURDERER! Let no one know. You need to keep your identity as a serf a secret. "
                "You killed Sir Rufus because you were afraid he would expose your true identity after he recognized "
                "you earlier tonight from Dunnsberry. Defend yourself at all costs and cast suspicion on Maid Monica or Sir Cameron!"
            )
            after_murder_objectives.insert(0, murderer_note)

        # For Sir Rufus (victim), special notes
        if cfg['is_victim']:
            start_objectives.append("When the lights go out tonight at the toast, you will fall down dead!")

        characters.append({
            "id": cfg['id'],
            "name": cfg['name'],
            "title": cfg['title'],
            "gender": cfg['gender'],
            "avatar_icon": cfg['avatar_icon'],
            "brief": cfg['brief'],
            "bio": bio if bio else cfg['brief'],
            "acting_tips": acting,
            "coins": cfg['coins'],
            "is_victim": cfg['is_victim'],
            "is_murderer": cfg['is_murderer'],
            "envelope_a": {
                "things_you_know": things_you_know,
                "start_objectives": start_objectives
            },
            "envelope_b": {
                "after_murder_objectives": after_murder_objectives
            }
        })

    game_data = {
        "title": "A Knight of Murder",
        "setting": "Fernwood Manor, 14th Century England",
        "speeches": speeches,
        "exhibits": exhibits,
        "characters": characters
    }

    with open('data/game_data.json', 'w', encoding='utf-8') as f:
        json.dump(game_data, f, indent=2, ensure_ascii=False)

    print(f"Successfully generated data/game_data.json with {len(characters)} characters, {len(exhibits)} exhibits, and {len(speeches)} speeches.")

if __name__ == '__main__':
    extract_all()
