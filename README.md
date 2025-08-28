# PI Reactor Bot

High-speed Selenium bot for the PI2 Reactor game that:

- Detects the current **target orb** on the page
- Clicks with **human-like randomized intervals**
- Counts **confirmed clicks** by reading the page’s **console logs**
- **Pauses at a cap** (default **220** , 220 is current pi recorded score limit) without stopping the bot
- **Auto-resumes** when a new game session is detected

The bot opens Chrome via **undetected-chromedriver** and persists **cookies/localStorage** so you don’t have to log in every time.

---

## Table Of Contents
- [PI Reactor Bot](#pi-reactor-bot)
  - [Table Of Contents](#table-of-contents)
  - [PI Reactor Is Live](#pi-reactor-is-live)
  - [How it works (quick)](#how-it-works-quick)
  - [Requirements](#requirements)
  - [Setup](#setup)
    - [Windows (PowerShell)](#windows-powershell)
    - [macOS (zsh)](#macos-zsh)
    - [Ubuntu / Debian](#ubuntu--debian)
  - [Run the bot](#run-the-bot)
  - [Configuration](#configuration)
  - [Expected terminal logs](#expected-terminal-logs)
  - [Troubleshooting](#troubleshooting)
  - [Resetting state](#resetting-state)
  - [Notes](#notes)

---

## PI Reactor Is Live
Pi2 Reactor is Live
a fast-paced mini-game designed to stress-test the Pi Squared network.
➡https://portal.pi2.network/reactor
➖ Click on Play Reactor Mini-Game
➖ Start Tapping the ball with specific color which is shown.

---

## How it works (quick)

- Target detection uses these CSS selectors in `main.py`:
  - `TARGET_SELECTOR = 'img[alt^="Target Color:"]'`
  - `OPTION_SELECTOR = 'button img[alt$="orb"]'`
- It hooks `console.log/info/warn/error/debug` on the page and parses lines to:
  - Count correct clicks (default token: **`Correct click!`**)
  - Detect new sessions (token: **`Backend session ID set`**)
- When `CLICK_LIMIT` is reached, the bot **idles** (no clicks) but keeps watching logs and **resumes automatically** on the next session.

> If the site’s wording differs, update the tokens in `pop_logs()` inside `main.py`.

---

## Requirements

- **Python 3.10+**
- **Google Chrome** (stable channel)
- Python packages (installed via `requirements.txt`):
  - `undetected-chromedriver`
  - `selenium`

---

## Setup

Clone the project repo first
```
git clone https://github.com/Widiskel/pi-reactor-bot
```

### Windows (PowerShell)

```powershell
cd pi-reactor-bot
py -3.10 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### macOS (zsh)

```bash
cd pi-reactor-bot
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y python3-venv
cd pi-reactor-bot
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> If you have multiple Python versions, replace `python3` with the specific one (e.g., `python3.10`).

---

## Run the bot

```bash
# from inside the project folder, with the venv activated
python -u main.py
```

What happens:

1. Chrome opens the game at `https://portal.pi2.network/reactor`.
2. **First run:** the terminal shows `Bot: please login...`.
   - Log in inside Chrome.
   - Return to the terminal and press **ENTER**.
   - The bot saves `cookies.json` and `localstorage.json`.
3. **Next runs:** your session is reloaded automatically.

---

## Configuration

Open `main.py` and adjust:

```python
URL = "https://portal.pi2.network/reactor"
MIN_INTERVAL_MS = 20      # per-click min delay (ms)
MAX_INTERVAL_MS = 40      # per-click max delay (ms)
CLICK_LIMIT = 220         # pause clicking at this count per session
TARGET_SELECTOR = 'img[alt^="Target Color:"]'
OPTION_SELECTOR = 'button img[alt$="orb"]'
```

If the site changes console messages:

```python
# inside pop_logs(driver):
# change to phrases your game actually prints (case-insensitive)
if "correct click!" in s:            # count confirmed clicks
    cnt += 1
if "backend session id set" in s:    # detect new session
    session_reset = True
```

You can switch to other phrases like `"Click recorded in backend"`; the parser is case-insensitive.

---

## Expected terminal logs

- `Session: active (targets detected)` — game elements found
- `Clicker: active` — bot is clicking
- `Count: X/220` — running click count from console logs
- `Clicker: paused at cap (220/220)` — reached cap; bot idles
- `Session: reset detected; counter cleared; resuming clicks` — new session; bot clicks again
- `Clicker: wrong color detected; halting current target` — wrong click reported; bot waits for target change

---

## Troubleshooting

**Count stays 0**
- Confirm the game prints **`Correct click!`** (or your chosen token) to the **browser console**.
- Update `pop_logs()` tokens to match real console messages.
- Run unbuffered for real-time prints: `python -u main.py`.

**Login every run**
- Ensure `cookies.json` and `localstorage.json` are created after the first login.
- If auth changes, delete both files and re-login once.

**Windows: activation policy error**
- If PowerShell blocks venv activation, run:  
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`

**Cloudflare or bot checks**
- The script uses **undetected-chromedriver**. If you hit a challenge, complete it in the opened Chrome window; your session will persist.

**Selectors broken**
- If the page DOM changes, update `TARGET_SELECTOR` and `OPTION_SELECTOR` accordingly.

---

## Resetting state

Delete these files to clear session and counters:

```
cookies.json
localstorage.json
```

---

## Notes
- Due to cloudflare used on Pi Squared Portal This bot only work on desktop with chrome browser
- The bot keeps running after the cap; it simply idles until a new session is detected.
- All logs are printed to your **Python terminal**, not the browser console.
- Clicks are injected via Chrome DevTools Protocol with fallbacks for reliability.
