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
def cloture_selenium(email, password, url, plages, date_str=""):
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
        # ── 1b. Ouvrir domaine pour poser le cookie RGPD ─────────────────────
        base_url = '/'.join(url.split('/')[:3])  # ex: https://drive.ecollaboratrice.com
        driver.get(base_url)
        time.sleep(1)
        driver.add_cookie({'name': 'alert-rgpd', 'value': 'true', 'domain': base_url.replace('https://', '').replace('http://', '')})

        driver.get(url)
        time.sleep(3)

        # ── 2. Connexion si nécessaire ───────────────────────────────────────
        current = driver.current_url.lower()
        if 'login' in current or 'account' in current or 'connect' in current or 'auth' in current:
            try:
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC
                from selenium.webdriver.common.by import By
                email_el = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], input[placeholder='Email'], input[name='Email']"))
                )
                email_el.clear()
                email_el.send_keys(email)
                pwd_el = driver.find_element('css selector', "input[type='password']")
                pwd_el.clear()
                pwd_el.send_keys(password)
                driver.find_element('css selector', "button[type='submit']" ).click()
                time.sleep(4)
                # Poser le cookie RGPD après login
                driver.add_cookie({'name': 'alert-rgpd', 'value': 'true', 'domain': base_url.replace('https://', '').replace('http://', '')})
                driver.get(url)
                time.sleep(3)
            except Exception as e:
                driver.quit()
                return False, f'Erreur de connexion : {e}'



        # ── 3. Cliquer .cellule-horaires du jour pour ouvrir la modale ────────
        # Utiliser la date du pointage si disponible, sinon aujourd'hui
        if date_str:
            try:
                dt = datetime.date.fromisoformat(date_str)
                today_str = dt.strftime('%d/%m')
            except Exception:
                today_str = datetime.date.today().strftime('%d/%m')
        else:
            today_str = datetime.date.today().strftime('%d/%m')

        found = driver.execute_script(f"""
            const ts = '{today_str}';
            const rows = document.querySelectorAll('tr');
            for (const tr of rows) {{
                if (tr.textContent.includes(ts)) {{
                    // Cliquer sur .cellule-horaires pour ouvrir la modale
                    const cell = tr.querySelector('.cellule-horaires');
                    if (cell) {{ cell.click(); return 'ok-cell:' + ts; }}
                    // Fallback : cliquer le td
                    const td = tr.querySelector('td');
                    if (td) {{ td.click(); return 'ok-td:' + ts; }}
                }}
            }}
            return 'not-found';
        """)

        if isinstance(found, str) and found == 'not-found':
            driver.quit()
            return False, f'Ligne du {today_str} introuvable dans le tableau.'

        time.sleep(3)  # Attendre que la modale s'ouvre

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

        # ── 5. Valider la journée ────────────────────────────────────────────
        time.sleep(0.5)
        driver.execute_script("""
            const btn = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
                .find(b => b.getAttribute('data-tippy-content') === 'Valider et bloquer la journée');
            if (btn) btn.click();
        """)
        time.sleep(1)

        # ── 6. Fermer la modale ───────────────────────────────────────────────
        driver.execute_script("""
            const btn = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
                .find(b => b.textContent.trim() === 'Fermer');
            if (btn) btn.click();
        """)
        time.sleep(2)

        # ── 7. Sauvegarder (page principale, jamais Sauvegarder et Terminer) ──
        saved = driver.execute_script("""
            const btn = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
                .find(b => b.textContent.trim() === 'Sauvegarder');
            if (btn) { btn.click(); return true; }
            return false;
        """)
        time.sleep(2)

        if not saved:
            driver.quit()
            return False, "Bouton Sauvegarder introuvable."


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
    date_str = (data.get("date") or "").strip()  # date réelle du pointage YYYY-MM-DD

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
    success, message = cloture_selenium(email, password, url, plages, date_str)

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
