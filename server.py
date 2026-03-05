#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur de clôture automatique — Charles Murgat
Lance: python server.py
"""

import json
import math
import time
import datetime
import threading
import os
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Autorise les requêtes depuis la PWA mobile

# ─── UTILS ───────────────────────────────────────────────────────────────────
def to_minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m

def min_to_hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"

# ─── CLÔTURE SELENIUM ────────────────────────────────────────────────────────
def cloture_selenium(email, password, url, plages):
    """
    Ouvre Chrome, se connecte à Ecollaboratrice, injecte les horaires, sauvegarde.
    Retourne (True, "message") ou (False, "erreur")
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    # Trouver Chromium et Chromedriver via env ou système
    import shutil, glob

    chromium_path     = os.environ.get("CHROME_BIN") or \
                        shutil.which("chromium") or \
                        shutil.which("chromium-browser") or \
                        shutil.which("google-chrome")

    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH") or \
                        shutil.which("chromedriver")

    try:
        if chromium_path:
            opts.binary_location = chromium_path
        if chromedriver_path:
            service = Service(chromedriver_path)
            driver  = webdriver.Chrome(service=service, options=opts)
        else:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            driver  = webdriver.Chrome(service=service, options=opts)
    except Exception as e:
        return False, f"Impossible de lancer Chrome : {e}"

    try:
        # ── 1. Ouvrir la page ────────────────────────────────────────────────
        # Injecter mois et année courants dans l'URL
        import re as _re
        today_d = datetime.date.today()
        url = _re.sub(r'mois=\d+', f'mois={today_d.month:02d}', url)
        url = _re.sub(r'annee=\d+', f'annee={today_d.year}', url)
        driver.get(url)
        time.sleep(3)

        # ── 2. Connexion si nécessaire ───────────────────────────────────────
        current = driver.current_url.lower()
        if "login" in current or "account" in current or "connect" in current:
            try:
                email_el = driver.find_element("css selector",
                    "input[type='email'], input[name='Email'], input[name='login']")
                email_el.clear()
                email_el.send_keys(email)

                pwd_el = driver.find_element("css selector", "input[type='password']")
                pwd_el.clear()
                pwd_el.send_keys(password)

                btn = driver.find_element("css selector",
                    "button[type='submit'], input[type='submit']")
                btn.click()
                time.sleep(4)
            except Exception as e:
                driver.quit()
                return False, f"Erreur de connexion : {e}"

        # ── 2b. Fermer popup RGPD ────────────────────────────────────────
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        time.sleep(2)
        try:
            btn = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'AI COMPRIS')]"))
            )
            driver.execute_script('arguments[0].scrollIntoView(true);', btn)
            driver.execute_script('arguments[0].click();', btn)
            time.sleep(2)
        except Exception:
            # Fallback : supprimer overlay DOM
            driver.execute_script(
                "document.querySelectorAll('.modal,.modal-backdrop,[class*=modal],[class*=overlay],[class*=popup],[class*=rgpd]')"
                ".forEach(el=>el.remove());"
                "document.body.style.overflow='auto';"
                "document.body.classList.remove('modal-open','overflow-hidden','no-scroll');"
            )
            time.sleep(1.5)



        # ── 3. Cliquer le jour courant (format Ecollaboratrice: "Mercredi 04/03") ──
        today_str = datetime.date.today().strftime("%d/%m")   # ex: "04/03"

        found = driver.execute_script(f"""
            const ts = '{today_str}';
            // Chercher td.text-nowrap contenant la date ex: "Mercredi 04/03"
            const tds = document.querySelectorAll('td.text-nowrap, td');
            for (const td of tds) {{
                const txt = td.textContent.trim();
                if (txt.includes(ts)) {{
                    td.click();
                    return 'ok:' + txt.substring(0, 30);
                }}
            }}
            return 'not-found';
        """)

        if isinstance(found, str) and found == 'not-found':
            driver.quit()
            return False, f"Ligne du {today_str} introuvable. La popup RGPD bloque peut-être encore."

        time.sleep(2)

        # ── 4. Injection horaires via inputs range-picker ────────────────────
        time.sleep(2)
        plages_min = [{"debut": to_minutes(p["debut"]), "fin": to_minutes(p["fin"])} for p in plages]

        result = driver.execute_script("""
            const inputs = [...document.querySelectorAll('input.range-picker.horaire-heure')];
            if (inputs.length === 0) return 'ERR_NO_INPUTS';
            return 'OK:' + inputs.length;
        """)

        if not result or not str(result).startswith('OK'):
            driver.quit()
            return False, f"Inputs horaires introuvables : {result}"

        for i, p in enumerate(plages_min):
            debut = min_to_hhmm(p['debut'])
            fin   = min_to_hhmm(p['fin'])
            driver.execute_script(f"""
                const inputs = [...document.querySelectorAll('input.range-picker.horaire-heure')];
                const idx = {i} * 2;
                if (inputs[idx]) {{
                    inputs[idx].value = '{debut}';
                    inputs[idx].dispatchEvent(new Event('input', {{bubbles:true}}));
                    inputs[idx].dispatchEvent(new Event('change', {{bubbles:true}}));
                    inputs[idx].dispatchEvent(new Event('blur', {{bubbles:true}}));
                }}
                if (inputs[idx+1]) {{
                    inputs[idx+1].value = '{fin}';
                    inputs[idx+1].dispatchEvent(new Event('input', {{bubbles:true}}));
                    inputs[idx+1].dispatchEvent(new Event('change', {{bubbles:true}}));
                    inputs[idx+1].dispatchEvent(new Event('blur', {{bubbles:true}}));
                }}
            """)
            time.sleep(0.5)

        # ── 5. Sauvegarder ───────────────────────────────────────────────────
        time.sleep(0.5)
        saved = driver.execute_script("""
            const btn = [...document.querySelectorAll('button')]
                .find(b => b.textContent.trim() === 'Sauvegarder');
            if (btn) { btn.click(); return true; }
            return false;
        """)

        if not saved:
            driver.quit()
            return False, "Bouton 'Sauvegarder' introuvable."

        time.sleep(1.5)

        # Confirmer dialog éventuel
        driver.execute_script("""
            const btn = [...document.querySelectorAll('button')]
                .find(b => ['Oui','Confirmer','OK'].includes(b.textContent.trim()));
            if (btn) btn.click();
        """)
        time.sleep(0.8)

        driver.quit()

        resume = " | ".join(f"{p['debut']} → {p['fin']}" for p in plages)
        return True, f"Clôture réussie : {resume}"

    except Exception as e:
        try:
            driver.quit()
        except Exception:
            pass
        return False, f"Erreur inattendue : {e}"


# ─── ROUTES API ──────────────────────────────────────────────────────────────

@app.route("/debug", methods=["GET"])
def debug():
    """Diagnostique Chrome/Chromedriver sur le serveur."""
    import shutil, glob, os
    def find_bin(*names):
        for name in names:
            p = shutil.which(name)
            if p: return p
        for name in names:
            matches = glob.glob(f"/nix/store/*/{name}") + glob.glob(f"/nix/store/*/bin/{name}")
            if matches: return matches[0]
        return None

    return jsonify({
        "chromium":     find_bin("chromium", "chromium-browser", "google-chrome"),
        "chromedriver": find_bin("chromedriver"),
        "PATH":         os.environ.get("PATH", ""),
        "nix_chromium": glob.glob("/nix/store/*/bin/chromium")[:3],
        "nix_driver":   glob.glob("/nix/store/*/bin/chromedriver")[:3],
    })


@app.route("/screenshot", methods=["POST"])
def screenshot():
    import base64, re as _re
    data     = request.get_json(force=True)
    email    = data.get("email","").strip()
    password = data.get("password","").strip()
    url      = data.get("url","").strip()
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    opts = Options()
    opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    cp = os.environ.get("CHROME_BIN") or shutil.which("chromium") or shutil.which("chromium-browser")
    dp = os.environ.get("CHROMEDRIVER_PATH") or shutil.which("chromedriver")
    if cp: opts.binary_location = cp
    driver = webdriver.Chrome(service=Service(dp), options=opts)
    try:
        today_d = datetime.date.today()
        url = _re.sub(r'mois=\d+', f'mois={today_d.month:02d}', url)
        url = _re.sub(r'annee=\d+', f'annee={today_d.year}', url)
        driver.get(url); time.sleep(3)
        cur = driver.current_url.lower()
        if "login" in cur or "account" in cur or "connect" in cur or "auth" in cur:
            try:
                inputs = driver.find_elements("css selector","input")
                email_el = next((i for i in inputs if i.get_attribute("placeholder") in ("Email","email","Login","login") or i.get_attribute("type")=="email" or i.get_attribute("name") in ("Email","email")), None)
                pwd_el   = next((i for i in inputs if i.get_attribute("type")=="password"), None)
                if email_el: email_el.clear(); email_el.send_keys(email)
                if pwd_el:   pwd_el.clear();   pwd_el.send_keys(password)
                btns = driver.find_elements("css selector","button, input[type='submit']")
                btn  = next((b for b in btns if b.text.strip().upper() in ("SE CONNECTER","CONNEXION","CONNECT","LOGIN","VALIDER") or b.get_attribute("type")=="submit"), None)
                if btn: btn.click()
                elif pwd_el:
                    from selenium.webdriver.common.keys import Keys
                    pwd_el.send_keys(Keys.RETURN)
                time.sleep(4)
            except: pass
        # Capturer HTML popup RGPD AVANT toute tentative
        popup_html = driver.execute_script(
            "const modals=[...document.querySelectorAll('[class*=modal],[class*=popup],[class*=rgpd],[class*=overlay]')];"
            "if(modals.length) return modals[0].outerHTML.substring(0,2000);"
            "return 'NO_MODAL_FOUND';"
        )
        # Tous les boutons visibles sur la page
        all_buttons = driver.execute_script(
            "return [...document.querySelectorAll('button,a')].map(b=>({"
            "  tag:b.tagName, text:b.textContent.trim().substring(0,50),"
            "  cls:b.className.substring(0,50), visible:b.offsetParent!==null"
            "})).filter(b=>b.text.length>0).slice(0,30);"
        )
        time.sleep(1)
        # Inspecter le bouton RGPD en détail
        rgpd_info = driver.execute_script(
            "const all=[...document.querySelectorAll('button,a,span,div,p')];"
            "const matches=all.filter(x=>x.textContent.includes('COMPRIS'));"
            "return matches.map(x=>({"
            "  tag:x.tagName,"
            "  text:JSON.stringify(x.textContent.trim()),"
            "  html:x.outerHTML.substring(0,200),"
            "  codes:[...x.textContent].map(c=>c.charCodeAt(0))"
            "}));"
        )
        rgpd_still_open = bool(rgpd_info)
        png = driver.get_screenshot_as_base64()
        day_cells = driver.execute_script("""
            const cells = document.querySelectorAll('td, [class*="jour"], [class*="day"]');
            return Array.from(cells).slice(0,30).map(c => ({
                tag: c.tagName, text: c.textContent.trim().substring(0,30),
                hasOnclick: !!c.onclick, cls: c.className.substring(0,50)
            }));
        """)
        final_url = driver.current_url; title = driver.title
        driver.quit()
        return jsonify({"title":title,"url":final_url,"screenshot":png,"day_cells":day_cells,"rgpd_open":rgpd_still_open,"rgpd_info":rgpd_info,"popup_html":popup_html,"all_buttons":all_buttons})
    except Exception as e:
        try: driver.quit()
        except: pass
        return jsonify({"error":str(e)}), 500


@app.route("/ping", methods=["GET"])
def ping():
    """Test de connexion depuis la PWA."""
    return jsonify({"status": "ok", "message": "Serveur opérationnel"})


@app.route("/cloture", methods=["POST"])
def cloture():
    """
    Corps attendu :
    {
        "email":    "user@example.com",
        "password": "••••••••",
        "url":      "https://drive.ecollaboratrice.com/...",
        "plages":   [{"debut": "08:00", "fin": "12:00"}, ...]
    }
    """
    data = request.get_json(force=True)

    email    = (data.get("email")    or "").strip()
    password = (data.get("password") or "").strip()
    url      = (data.get("url")      or "").strip()
    plages   = data.get("plages", [])

    # Validation
    if not email or not password:
        return jsonify({"success": False, "error": "Email et mot de passe requis"}), 400
    if not url:
        return jsonify({"success": False, "error": "URL Ecollaboratrice requise"}), 400
    if not plages:
        return jsonify({"success": False, "error": "Aucune plage horaire fournie"}), 400

    # Vérifier format plages
    for p in plages:
        if not p.get("debut") or not p.get("fin"):
            return jsonify({"success": False, "error": f"Plage incomplète : {p}"}), 400

    # Lancer la clôture
    success, message = cloture_selenium(email, password, url, plages)

    if success:
        return jsonify({"success": True, "message": message})
    else:
        return jsonify({"success": False, "error": message}), 500


# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*50}")
    print(f"  Serveur Pointage CM — port {port}")
    print(f"  Test : http://localhost:{port}/ping")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
