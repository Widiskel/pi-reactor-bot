import sys
import json
import time
import random
import threading
import re
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
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

def safe_save_session(driver):
    try:
        cookies = driver.get_cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)
    except Exception:
        pass
    try:
        localstorage = driver.execute_script("return {...localStorage};")
        with open(LOCALSTORAGE_FILE, "w") as f:
            json.dump(localstorage, f)
    except Exception:
        pass

def load_session(driver):
    ok = False
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        for cookie in cookies:
            driver.add_cookie(cookie)
        ok = True
    except Exception:
        pass
    try:
        with open(LOCALSTORAGE_FILE, "r") as f:
            localstorage = json.load(f)
        for k, v in localstorage.items():
            driver.execute_script("localStorage.setItem(arguments[0], arguments[1])", k, v)
        ok = True or ok
    except Exception:
        pass
    return ok

def ensure_console_hook(driver):
    driver.execute_script("""
        try{
            if(!window._logBuffer) window._logBuffer=[];
            const labels=["log","info","warn","error","debug"];
            const pack=(...args)=>{try{return args.map(a=>{try{return typeof a==='object'?JSON.stringify(a):String(a)}catch(e){return String(a)}}).join(' ')}catch(e){return ''}};
            labels.forEach(lbl=>{
                const cur=console[lbl];
                if(!cur) return;
                if(!cur.__pi2Wrapped){
                    const wrapped=function(){try{window._logBuffer.push(pack(...arguments))}catch(e){} try{return cur.apply(console,arguments)}catch(e){}};
                    wrapped.__pi2Wrapped=true;
                    console[lbl]=wrapped;
                }
            });
        }catch(e){}
    """)

def pop_logs(driver):
    try:
        logs = driver.execute_script("const a=window._logBuffer||[]; window._logBuffer=[]; return a;")
    except Exception:
        return [], 0, False
    objs = []
    cnt = 0
    session_reset = False
    for line in logs:
        s = str(line).lower()
        if "correct click!" in s:
            cnt += 1
        if "backend session id set" in s:
            session_reset = True
        if ("sending click data to backend" in s) or ('"iscorrect":' in s):
            m = re.search(r'(\{.*\})', line)
            if m:
                try:
                    obj = json.loads(m.group(1))
                    objs.append(obj)
                except Exception:
                    pass
    return objs, cnt, session_reset

def get_button_center(driver, img_elem):
    try:
        button = img_elem.find_element(By.XPATH, "./ancestor::button")
    except Exception:
        return None, None, None
    if not button.is_enabled():
        return None, None, None
    rect = driver.execute_script("const r=arguments[0].getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};", button)
    if not rect:
        return None, None, None
    x = rect["x"] + rect["w"] / 2.0
    y = rect["y"] + rect["h"] / 2.0
    return x, y, button

def cdp_click(driver, x, y):
    try:
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "buttons": 1})
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        return True
    except Exception:
        return False

def fallback_click(driver, x, y, button_elem):
    try:
        actions = ActionChains(driver)
        actions.move_to_element_with_offset(button_elem, 1, 1).click().perform()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click()", button_elem)
            return True
        except Exception:
            return cdp_click(driver, x, y)

def worker_hook_ensure(driver):
    while not stop_event.is_set():
        try:
            ensure_console_hook(driver)
        except Exception:
            pass
        time.sleep(0.2)

def worker_identify(driver):
    global current_target_src, click_point, target_version, blacklisted_src, session_active
    prev_session = False
    while not stop_event.is_set():
        try:
            targets = driver.find_elements(By.CSS_SELECTOR, TARGET_SELECTOR)
            options = driver.find_elements(By.CSS_SELECTOR, OPTION_SELECTOR)
            active = bool(targets and options)
            if active and not prev_session:
                print("Session: active (targets detected)", flush=True)
            if not active and prev_session:
                print("Session: inactive (targets missing)", flush=True)
            prev_session = active
            session_active = active
            if not active:
                with state_lock:
                    click_point = None
                time.sleep(0.01)
                continue
            tgt = targets[0]
            tgt_src = tgt.get_attribute("src")
            if not tgt_src:
                with state_lock:
                    click_point = None
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
                with state_lock:
                    click_point = None
                time.sleep(0.008)
                continue
            x, y, btn = get_button_center(driver, matched)
            if x is None:
                with state_lock:
                    click_point = None
                time.sleep(0.008)
                continue
            alt_text = matched.get_attribute("alt") or ""
            # color_name = alt_text.split(" ")[0] if alt_text else "?"
            with state_lock:
                if tgt_src != current_target_src:
                    current_target_src = tgt_src
                    target_version += 1
                    # print(f"Target: {color_name} (src changed)", flush=True)
                click_point = (x, y, btn)
        except Exception:
            with state_lock:
                click_point = None
            time.sleep(0.01)

def worker_click(driver):
    global click_point, blacklisted_src, target_version, click_recorded, clicking_disabled, last_mode
    while not stop_event.is_set():
        with state_lock:
            cp = click_point
            ver = target_version
            cur_src = current_target_src
            cur_disabled = clicking_disabled
            cur_count = click_recorded
            sess = session_active
        mode = "idle"
        if not sess:
            mode = "idle:no-session"
        elif cur_disabled or cur_count >= CLICK_LIMIT:
            mode = "idle:cap"
        elif cp is None:
            mode = "idle:waiting-target"
        else:
            mode = "clicking"
        if mode != last_mode:
            if mode == "clicking":
                print("Clicker: active", flush=True)
            elif mode == "idle:cap":
                print(f"Clicker: paused at cap ({min(cur_count, CLICK_LIMIT)}/{CLICK_LIMIT})", flush=True)
            elif mode == "idle:waiting-target":
                print("Clicker: idle (waiting target)", flush=True)
            elif mode == "idle:no-session":
                print("Clicker: idle (no session)", flush=True)
            else:
                print("Clicker: idle", flush=True)
            last_mode = mode
        objs, inc, session_reset = pop_logs(driver)
        if not cur_disabled and cur_count < CLICK_LIMIT and inc:
            with state_lock:
                click_recorded += inc
                if click_recorded > CLICK_LIMIT:
                    click_recorded = CLICK_LIMIT
            print(f"Count: {click_recorded}/{CLICK_LIMIT}", flush=True)
        else:
            inc = 0
        if session_reset:
            with state_lock:
                clicking_disabled = False
                click_recorded = 0
                blacklisted_src = None
                target_version += 1
                click_point = None
            print("Session: reset detected; counter cleared; resuming clicks", flush=True)
            time.sleep(0.05)
            continue
        if mode != "clicking":
            time.sleep(0.03)
            continue
        with state_lock:
            if clicking_disabled or click_recorded >= CLICK_LIMIT:
                click_point = None
                continue
        x, y, btn = cp
        ok = cdp_click(driver, x, y)
        if not ok:
            fallback_click(driver, x, y, btn)
        interval = random.randint(MIN_INTERVAL_MS, MAX_INTERVAL_MS) / 1000.0
        time.sleep(interval)
        wrong = False
        objs, inc2, session_reset2 = pop_logs(driver)
        if not clicking_disabled and click_recorded < CLICK_LIMIT and inc2:
            with state_lock:
                click_recorded += inc2
                if click_recorded > CLICK_LIMIT:
                    click_recorded = CLICK_LIMIT
            print(f"Count: {click_recorded}/{CLICK_LIMIT}", flush=True)
            if click_recorded >= CLICK_LIMIT:
                with state_lock:
                    clicking_disabled = True
                    click_point = None
                print("Clicker: paused at cap", flush=True)
        if session_reset2:
            with state_lock:
                clicking_disabled = False
                click_recorded = 0
                blacklisted_src = None
                target_version += 1
                click_point = None
            print("Session: reset detected; counter cleared; resuming clicks", flush=True)
        for obj in reversed(objs):
            if isinstance(obj, dict) and "isCorrect" in obj:
                if obj.get("isCorrect") is False:
                    wrong = True
                break
        if wrong:
            with state_lock:
                blacklisted_src = cur_src
                click_point = None
                target_version += 1
            print("Clicker: wrong color detected; halting current target", flush=True)
            time.sleep(0.06)
            with state_lock:
                blacklisted_src = None
            continue
        with state_lock:
            if ver != target_version or click_point is None:
                continue

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
    print("Bot: please login in the opened browser, then press ENTER here", flush=True)
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
    try:
        safe_save_session(driver)
    except Exception:
        pass
    try:
        driver.quit()
    except Exception:
        pass
