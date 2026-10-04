# 🏰 A Knight of Murder - 2-Player Murder Mystery with AI Agents

Welcome to **Fernwood Manor**! This system is tailor-made for **2 human players** to experience the complete 14th-century murder mystery *"A Knight of Murder"* as living suspects, without needing a human host/game master, and without spoiling the murderer in advance!

All other lords, ladies, knights, servants, and the Royal Herald are powered by interactive AI agents who converse, harbor secrets, demand bribes, and react in real-time authentic Medieval English.

---

## 🚀 Quick Start Guide

### 1. Launch the Game Server
Simply double-click the **`run.bat`** file in this folder (or open PowerShell in this directory and run):
```powershell
python server.py
```
The browser will automatically open to `http://localhost:8000`.

### 2. Connect Both Players
- **Player 1 (Host PC)**: Plays directly in the browser at `http://localhost:8000`.
- **Player 2 (Friend's Phone or Laptop)**: 
  - Ensure the second device is connected to the **same Wi-Fi network**.
  - Scan the **QR Code** displayed on the lobby screen, or type the local IP URL (e.g. `http://192.168.55.113:8000`).

### 3. API Key Setup
- Click the **⚙️ Settings icon** on the top right.
- Paste your **Google Gemini API Key** (or OpenAI API Key) and save.
- *(Alternatively, you can copy `.env.example` to `.env` and set `GEMINI_API_KEY=...`).*

### 4. Choose Your Roles & Begin
- **Player 1**: Type your name and select any available character card.
- **Player 2**: Open the link on their device, enter their name, and pick their preferred character card.
- *(Note: **Sir Rufus** is automatically locked to AI as the designated victim, ensuring both of you stay alive throughout the entire investigation!)*
- Click **🍷 Start Banquet**!

---

## 🎮 How to Play

### 5 Game Stages
1. **Stage 1: Arrival & Reception**: Open your **Envelope A** in your *Secret Dossier*, inspect your *Things You Know*, and check off your *Start Objectives*.
2. **Stage 2: The Banquet & Festivities**: Lord Taylor delivers the grand welcoming speech. Mingle freely, trade rumors, and test loyalties.
3. **Stage 3: The Murder of Sir Rufus**: When both players click *"Advance to Stage 3"*, Lord Taylor raises his goblet for the toast... **The torches blow out! Thunder crashes! A scream echoes!** Sir Rufus is slain! Willie the Watchman outlines the body, and **Envelope B** is unsealed for everyone.
4. **Stage 4: Presentation of Evidence & Accusations**: Willie lays **Exhibits A through F** upon the table. Review the murder weapon, the love tokens, poison, and blackmail notes. Open the **Who Dunnit** tab to cast your secret accusation!
5. **Stage 5: The Grand Climax & Solution**: Willie reads the official solution, unmasks the true murderer, and presents the **Smoking Gun Award**, **Wealthiest Guest Award**, and **Best Performance Award**!

---

## 🛡️ Interactive Features
- **🏰 The Great Hall**: Public banquet room. Speak aloud to the gathering, or type `@CharacterName` (e.g., `@Genevieve`, `@Diana`) to address an AI directly.
- **🗝️ Private Whispers**: Pull any suspect into a secluded corner for 1-on-1 private interrogation, scheming, or deals.
- **💰 Gold Purse & Bribes**: Each player starts with 12 gold coins. Click *"Send Bribe / Gold"* to slip coins to an AI character or the other player to extract guarded secrets.
- **📜 Secret Dossier**: Read your character's full medieval biography, secrets, and tick off completed tasks on your interactive checklist.
- **🔊 Atmospheric Medieval Audio Engine**: Built-in lute chords, coin clinks, royal trumpet fanfares, and thunder crashes—works 100% offline with a mute toggle.
- **💾 Session Persistence**: If you accidentally refresh the page (F5) or switch apps on your phone, you will immediately reconnect without losing your character, gold, or chat history!
