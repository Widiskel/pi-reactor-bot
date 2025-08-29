import sys
import json
import time
import random
import threading
import re
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

COOKIES_FILE = "cookies.json"
LOCALSTORAGE_FILE = "localstorage.json"
URL = "https://portal.pi2.network/reactor"
TARGET_SELECTOR = 'img[alt^="Target Color:"]'
OPTION_SELECTOR = 'button img[alt$="orb"]'
MIN_INTERVAL_MS = 20
MAX_INTERVAL_MS = 40
CLICK_LIMIT = 220

state_lock = threading.Lock()
current_target_src = None
click_point = None
target_version = 0
blacklisted_src = None
click_recorded = 0
clicking_disabled = False
stop_event = threading.Event()
session_active = False
last_mode = None
game_ended_flag = False

def safe_save_session(driver):
    try:
        cookies = driver.get_cookies()
        with open(COOKIES_FILE, "w") as f: json.dump(cookies, f)
    except Exception: pass
    try:
        localstorage = driver.execute_script("return {...localStorage};")
        with open(LOCALSTORAGE_FILE, "w") as f: json.dump(localstorage, f)
    except Exception: pass

def load_session(driver):
    ok = False
    try:
        with open(COOKIES_FILE, "r") as f: cookies = json.load(f)
        for cookie in cookies: driver.add_cookie(cookie)
        ok = True
    except Exception: pass
    try:
        with open(LOCALSTORAGE_FILE, "r") as f: localstorage = json.load(f)
        for k, v in localstorage.items(): driver.execute_script("localStorage.setItem(arguments[0], arguments[1])", k, v)
        ok = True or ok
    except Exception: pass
    return ok

def ensure_console_hook(driver):
    driver.execute_script("""
        try{if(!window._logBuffer) window._logBuffer=[];const labels=["log","info","warn","error","debug"];const pack=(...args)=>{try{return args.map(a=>{try{return typeof a==='object'?JSON.stringify(a):String(a)}catch(e){return String(a)}}).join(' ')}catch(e){return ''}};labels.forEach(lbl=>{const cur=console[lbl];if(!cur) return;if(!cur.__pi2Wrapped){const wrapped=function(){try{window._logBuffer.push(pack(...arguments))}catch(e){} try{return cur.apply(console,arguments)}catch(e){}};wrapped.__pi2Wrapped=true;console[lbl]=wrapped;}});}catch(e){}
    """)

def pop_logs(driver):
    try:
        logs = driver.execute_script("const a=window._logBuffer||[]; window._logBuffer=[]; return a;")
    except Exception:
        return [], 0, False, False
    
    click_count = 0
    game_ended = False
    game_started = False
    for line in logs:
        s_line = str(line).lower()
        if "correct click!" in s_line:
            click_count += 1
        if "game ended due" in s_line:
            game_ended = True
        if "backend session started" in s_line or "game reset" in s_line:
            game_started = True
            
    return logs, click_count, game_ended, game_started

def get_button_center(driver, img_elem):
    try: button = img_elem.find_element(By.XPATH, "./ancestor::button")
    except Exception: return None, None, None
    if not button.is_enabled(): return None, None, None
    rect = driver.execute_script("const r=arguments[0].getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};", button)
    if not rect: return None, None, None
    x, y = rect["x"] + rect["w"] / 2.0, rect["y"] + rect["h"] / 2.0
    return x, y, button

def cdp_click(driver, x, y):
    try:
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "buttons": 1})
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        return True
    except Exception: return False

def fallback_click(driver, x, y, button_elem):
    try:
        ActionChains(driver).move_to_element_with_offset(button_elem, 1, 1).click().perform()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click()", button_elem)
            return True
        except Exception: return cdp_click(driver, x, y)

def worker_hook_ensure(driver):
    while not stop_event.is_set():
        try: ensure_console_hook(driver)
        except Exception: pass
        time.sleep(0.2)

def worker_identify(driver):
    global current_target_src, click_point, target_version, blacklisted_src, session_active
    prev_session = False
    while not stop_event.is_set():
        try:
            targets = driver.find_elements(By.CSS_SELECTOR, TARGET_SELECTOR)
            options = driver.find_elements(By.CSS_SELECTOR, OPTION_SELECTOR)
            active = bool(targets and options)
            if active != prev_session:
                print(f"Session: {'active' if active else 'inactive'}", flush=True)
            prev_session = active
            session_active = active
            if not active:
                with state_lock: click_point = None
                time.sleep(0.01)
                continue
            tgt_src = targets[0].get_attribute("src")
            if not tgt_src:
                with state_lock: click_point = None
                time.sleep(0.008)
                continue
            with state_lock:
                if blacklisted_src and tgt_src == blacklisted_src:
                    click_point = None
            matched = None
            for opt in options:
                if opt.get_attribute("src") == tgt_src:
                    matched = opt
                    break
            if not matched:
                with state_lock: click_point = None
                time.sleep(0.008)
                continue
            x, y, btn = get_button_center(driver, matched)
            if x is None:
                with state_lock: click_point = None
                time.sleep(0.008)
                continue
            with state_lock:
                if tgt_src != current_target_src:
                    current_target_src = tgt_src
                    target_version += 1
                click_point = (x, y, btn)
        except Exception:
            with state_lock: click_point = None
            time.sleep(0.01)

def worker_click(driver):
    global click_point, blacklisted_src, target_version, click_recorded, clicking_disabled, last_mode, game_ended_flag

    while not stop_event.is_set():
        # PERBAIKAN: Selalu baca log di awal setiap loop
        logs, click_count, game_ended, game_started = pop_logs(driver)

        # Proses semua informasi dari log untuk memperbarui status global
        if click_count > 0:
            with state_lock:
                if not clicking_disabled:
                    click_recorded += click_count
                    if click_recorded >= CLICK_LIMIT:
                        clicking_disabled = True
            print(f"Count: {click_recorded}/{CLICK_LIMIT}", flush=True)

        if game_started:
            with state_lock:
                click_recorded = click_count if click_count > 0 else 0
                clicking_disabled = False
                game_ended_flag = False
            print("Session: new game detected; counter reset.", flush=True)

        if game_ended:
            with state_lock: game_ended_flag = True
            print("Session: game over detected. Handing over to endgame logic.", flush=True)

        wrong_click = False
        with state_lock: cur_src = current_target_src
        for line in logs:
            if '"iscorrect":false' in str(line).lower().replace(" ", ""):
                wrong_click = True
                break
        
        if wrong_click:
            with state_lock:
                blacklisted_src = cur_src
                click_point = None
                target_version += 1
            print("Clicker: wrong color detected; halting current target", flush=True)
            time.sleep(0.06)
            with state_lock: blacklisted_src = None

        # Baca status terbaru setelah diproses
        with state_lock:
            cp, ver, sess, ended, cur_disabled, cur_count = \
                click_point, target_version, session_active, game_ended_flag, clicking_disabled, click_recorded
        
        # Tentukan mode berdasarkan status terbaru
        mode = "idle"
        if ended: mode = "idle:ended"
        elif not sess: mode = "idle:no-session"
        elif cur_disabled or cur_count >= CLICK_LIMIT: mode = "idle:cap"
        elif cp is None: mode = "idle:waiting-target"
        else: mode = "clicking"

        if mode != last_mode:
            print(f"Clicker: {mode.split(':')[0]}", flush=True)
            last_mode = mode

        # Lakukan aksi berdasarkan mode
        if mode == "idle:ended":
            handle_game_end(driver)
        elif mode == "clicking":
            x, y, btn = cp
            cdp_click(driver, x, y) or fallback_click(driver, x, y, btn)
            interval = random.randint(MIN_INTERVAL_MS, MAX_INTERVAL_MS) / 1000.0
            time.sleep(interval)
        else: # Semua mode idle lainnya
            time.sleep(0.05)


def handle_game_end(driver):
    global game_ended_flag

    if driver.current_url != URL:
        print("Endgame: Not on game URL, skipping.", flush=True)
        time.sleep(5)
        return

    print("Endgame: Waiting 10 seconds...", flush=True)
    time.sleep(10)

    try:
        play_again_button = driver.find_element(By.XPATH, "//button[text()='Play Again']")
        play_again_button.click()
        print("Endgame: 'Play Again' button clicked.", flush=True)
    except Exception:
        print("Endgame: 'Play Again' button not found.", flush=True)

    print("Endgame: Waiting up to 20 seconds for game to restart...", flush=True)
    start_wait = time.time()
    restarted = False
    while time.time() - start_wait < 20:
        _, _, _, game_started = pop_logs(driver)
        if game_started:
            print("Endgame: New game detected!", flush=True)
            restarted = True
            break
        time.sleep(1)

    if not restarted:
        print("Endgame: Game did not restart. Refreshing page.", flush=True)
        driver.refresh()
        time.sleep(5)
        try:
            wait = WebDriverWait(driver, 10)
            play_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Play Reactor Mini-Game')]")))
            play_button.click()
            print("Endgame: 'Play Reactor Mini-Game' button clicked.", flush=True)
        except Exception:
            print("Endgame: Could not find main play button after refresh.", flush=True)
            
    with state_lock:
        game_ended_flag = False

if __name__ == '__main__':
    print(r"""
                                                          
                      ...                                 
                     .;:.                                 
                    .;ol,.                                
                   .;ooc:'                                
            ..    .;ooccc:'.    ..                        
          .',....'cdxlccccc;.....,'.                      
         .;;..'';clolccccccc:,''..;;.                     
        ':c'..':cccccccccccccc;...'c:.                    
       ':cc,.'ccccccccccccccccc:..;cc:'                   
    ...:cc;.':cccccccccccccccccc:..:cc:...                
   .;';cc;.':;;:cccccccccccccc:;;;'.;cc,,;.               
  .cc':c:.',.....;cccccccccc;.....,..:c:'c:               
  ,x:'cc;.,'     .':cccccc:'.     ',.;cc':x'              
  lO,'cc;.;,       .;cccc:.       ,;.;cc';0l              
 .o0;.;c;.,:'......',''''''......':,.;c;.:0l.             
 .lxl,.;,..;c::::;:,.    .,:;::::c;..,;.,oxl.             
 .lkxOl..  ..'..;::'..''..'::;..'..  ..c0xkl.             
  .cKMx.        .;c:;:cc:;:c:.        .xMKc.              
    ;KX:         ;o::l:;cc;o:.        ;KK;                
     :KK:.       ,d,cd,'ol'o:       .:0K:                 
      ;0NOl:;:loo;. ... .. .;ldlc::lkN0:                  
       .lONNNKOx0Xd,;;'.,:,lKKkk0XNN0o.                   
         .','.. .lX0doooodOXd.  .','.                     
                 .,okkddxkd;.                             
                    'oxxd;.                               
   ........................................                              
   .OWo  xNd lox  xxl Ald   xoc dakkkkkxsx.              
   .OWo  o0W cXW  dM0 MMN   lNK laddKMNkso.               
   .kMKoxsNN oWX  dW0 MMMWO lWK    axM0   .                
   .OMWXNaMX dM0  kM0 MMKxNXKW0    axMk   .                 
   .OMk  dWK oWX XWdx Mxx  XMMO    akMx   .                 
   'OWo  dM0 'kNNXNNd DMD   OWk    aoWd   .                 
   ........................................
   . By : Widiskel                        .
   . Join : t.me/skeldrophunt             .
   ........................................                 
                                                                      
    """,end="")
    print("Bot: starting", flush=True)
    driver = uc.Chrome()
    driver.maximize_window()
    driver.get(URL)
    if not load_session(driver):
        print("Bot: please login, then press ENTER here", flush=True)
        input()
        safe_save_session(driver)
        driver.get(URL)
    ensure_console_hook(driver)
    t_hook = threading.Thread(target=worker_hook_ensure, args=(driver,), daemon=True)
    t_id = threading.Thread(target=worker_identify, args=(driver,), daemon=True)
    t_ck = threading.Thread(target=worker_click, args=(driver,), daemon=True)
    t_hook.start()
    t_id.start()
    t_ck.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        try: safe_save_session(driver)
        except Exception: pass
        try: driver.quit()
        except Exception: pass