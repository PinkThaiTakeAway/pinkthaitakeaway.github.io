#!/usr/bin/env python3
"""
Zelfcontrole voor Pink Thai TakeAway.

Twee standen:
  python3 selfcheck.py          -> controleert de repo-inhoud (vóór publiceren)
  python3 selfcheck.py --live   -> controleert de gepubliceerde site (na publiceren)

Fouten (✗) laten de controle mislukken -> GitHub stuurt automatisch een e-mail.
Waarschuwingen (⚠) worden gemeld maar laten de controle slagen (om valse
alarmen bij tijdelijke, externe hikjes te voorkomen).
"""
import json, os, re, subprocess, sys, hashlib, urllib.request, ssl, socket
from datetime import datetime, timezone

SITE = "https://pinkthaitakeaway.nl"
oks, warnings, errors = [], [], []
def ok(m):   oks.append(m)
def warn(m): warnings.append(m)
def err(m):  errors.append(m)

def http(url, timeout=25):
    """Haalt een URL op. Geeft (status, bytes) of (None, foutmelding)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "selfcheck"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return None, str(e).encode()

def redirect_target(url, timeout=20):
    """Geeft (status, Location) van de eerste reactie ZONDER redirects te volgen."""
    class _NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    try:
        opener = urllib.request.build_opener(_NoRedir)
        req = urllib.request.Request(url, headers={"User-Agent": "selfcheck"})
        try:
            r = opener.open(req, timeout=timeout)
            return r.getcode(), r.headers.get("Location")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location")
    except Exception as e:
        return None, str(e)

# ----------------------------------------------------------------------------
def _menu_ids(html):
    s = html.find("const MENU = [")
    if s < 0: return None
    oi = html.find("[", s); d = 0; i = oi
    while i < len(html):
        c = html[i]
        if c == "[": d += 1
        elif c == "]":
            d -= 1
            if d == 0: break
        i += 1
    if d != 0: return None
    return set(re.findall(r'\{id:"([^"]+)"', html[oi:i]))

def _menu_region(html):
    s = html.find("const MENU = [")
    if s < 0: return None
    oi = html.find("[", s); d = 0; i = oi
    while i < len(html):
        c = html[i]
        if c == "[": d += 1
        elif c == "]":
            d -= 1
            if d == 0: break
        i += 1
    return html[oi:i] if d == 0 else None

def check_repo():
    # 1. index.html bestaat + bevat de merknaam
    if not os.path.exists("index.html"):
        err("index.html ontbreekt"); html = ""
    else:
        html = open("index.html", encoding="utf-8").read()
        if "Pink Thai TakeAway" in html:
            ok("index.html aanwezig met verwachte inhoud")
        else:
            err("index.html mist de merknaam 'Pink Thai TakeAway'")

    # 2. JavaScript-syntax van het hoofdscript
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    if scripts:
        open("/tmp/_chk.js", "w", encoding="utf-8").write(scripts[-1])
        r = subprocess.run(["node", "--check", "/tmp/_chk.js"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            ok("JavaScript-syntax geldig")
        else:
            last = (r.stderr.strip().splitlines() or ["onbekende fout"])[-1]
            err("JavaScript-syntaxfout: " + last)
    else:
        warn("geen <script>-blok gevonden om te controleren")

    # 2b. Eigen domein: CNAME-bestand aanwezig en correct
    if os.path.exists("CNAME"):
        cn = open("CNAME", encoding="utf-8").read().strip()
        if cn == "pinkthaitakeaway.nl":
            ok("CNAME aanwezig en correct (pinkthaitakeaway.nl)")
        else:
            err(f"CNAME bevat onverwacht domein '{cn}' (verwacht pinkthaitakeaway.nl)")
    else:
        err("CNAME ontbreekt \u2014 eigen domein kan bij een deploy worden losgelaten")

    # 2c. Geen onveilige (http://) bronnen op de pagina (mixed content)
    mixed = re.findall(r'\ssrc\s*=\s*"(http://[^"]+)"', html)
    mixed += re.findall(r'<link[^>]+href\s*=\s*"(http://[^"]+)"', html)
    mixed += re.findall(r'url\(\s*(http://[^)]+)\)', html)
    if mixed:
        warn(f"onveilige http://-bron(nen) op de pagina: {len(mixed)}\u00d7 \u2014 bijv. {mixed[0][:60]}")
    else:
        ok("geen onveilige http://-bronnen op de pagina")

    # 2d. MENU-broncode consistent met (definitief) verwijderde gerechten
    mids = _menu_ids(html)
    if mids is None:
        err("MENU-array niet gevonden of niet gebalanceerd in index.html")
    else:
        ok(f"MENU-broncode telt {len(mids)} gerechten")
        try:
            purged = set(json.load(open("verwijderdweg.json", encoding="utf-8")))
        except Exception:
            purged = set()
        stuck = sorted(mids & purged)
        if stuck:
            warn(f"{len(stuck)} definitief verwijderd gerecht(en) staan nog in de broncode: {', '.join(stuck[:6])}")
        else:
            ok("geen definitief verwijderde gerechten meer in de broncode")
        # Eigen gerechten uit extra.json tellen ook als geldig (die staan niet in de MENU-broncode)
        try:
            _ej = json.load(open("extra.json", encoding="utf-8"))
            _items = _ej.get("items", _ej) if isinstance(_ej, dict) else _ej
            extra_ids = {it["id"] for it in _items if isinstance(it, dict) and it.get("id")}
        except Exception:
            extra_ids = set()
        valid_ids = set(mids) | extra_ids
        orphan = 0
        for fn in ("fotos.json", "prijzen.json", "recepten.json", "namen.json", "omschrijvingen.json", "pittigheid.json", "serveertips.json", "nummers.json"):
            if os.path.exists(fn):
                try:
                    dd = json.load(open(fn, encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(dd, dict):
                    orphan += sum(1 for k in dd if k not in valid_ids)
        if orphan:
            warn(f"{orphan} verweesde gegeven(s) voor niet-bestaande gerechten in de databestanden")
        else:
            ok("geen verweesde gegevens in de databestanden")

    # 2e. Broncode-integriteit: geen dubbele id's / bestelnummers
    region = _menu_region(html)
    if region is not None:
        idlist = re.findall(r'\{id:"([^"]+)"', region)
        dups = sorted(set(x for x in idlist if idlist.count(x) > 1))
        if dups: err("dubbele gerecht-id(s) in de broncode: " + ", ".join(dups))
        else: ok("geen dubbele gerecht-id's in de broncode")
    if os.path.exists("nummers.json"):
        try:
            nums = json.load(open("nummers.json", encoding="utf-8"))
            vals = [v for v in nums.values() if isinstance(v, (int, float))]
            dn = sorted(set(int(v) for v in vals if vals.count(v) > 1))
            if dn: warn("dubbele bestelnummers: " + ", ".join(str(x) for x in dn))
            else: ok("bestelnummers zijn uniek")
        except Exception:
            pass

    # 2f. Verwijzingen naar niet-bestaande gerechten (prullenbak / verborgen)
    if mids is not None:
        dangling = 0
        for fn in ("verwijderd.json", "verborgen.json"):
            if os.path.exists(fn):
                try: arr = json.load(open(fn, encoding="utf-8"))
                except Exception: arr = []
                if isinstance(arr, list):
                    dangling += sum(1 for x in arr if x not in mids)
        if dangling: warn(str(dangling) + " verwijzing(en) naar niet-bestaande gerechten (prullenbak/verborgen)")
        else: ok("geen verwijzingen naar niet-bestaande gerechten")

    # 2g. Foto's: kapotte verwijzingen + verweesde uploads
    referenced = set()
    if os.path.exists("fotos.json"):
        try:
            for v in json.load(open("fotos.json", encoding="utf-8")).values():
                if isinstance(v, str) and v and not v.startswith("http") and not v.startswith("data:"):
                    referenced.add(v.lstrip("/"))
        except Exception:
            pass
    try:
        cf = json.load(open("bedrijf.json", encoding="utf-8")).get("chefFoto")
        if isinstance(cf, str) and cf and not cf.startswith("http") and not cf.startswith("data:"):
            referenced.add(cf.lstrip("/"))
    except Exception:
        pass
    missing = sorted(p for p in referenced if not os.path.exists(p))
    if missing: warn(str(len(missing)) + " fotoverwijzing(en) naar ontbrekend bestand: " + ", ".join(missing[:4]))
    else: ok("alle lokale fotoverwijzingen bestaan")
    if os.path.isdir("foto"):
        upl = []
        for root, _dirs, files in os.walk("foto"):
            for f in files:
                if f.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp", "gif"):
                    upl.append(os.path.join(root, f).lstrip("/"))
        orphan_imgs = sorted(p for p in upl if p not in referenced)
        if orphan_imgs: warn(str(len(orphan_imgs)) + " verweesd fotobestand(en) in foto/ (nergens gebruikt)")
        else: ok("geen verweesde fotobestanden")

    # 3. Alle aanwezige JSON-bestanden zijn geldig
    data = {}
    for f in sorted(x for x in os.listdir(".") if x.endswith(".json")):
        try:
            data[f] = json.load(open(f, encoding="utf-8"))
            ok(f"{f}: geldige JSON")
        except Exception as e:
            err(f"{f}: ONgeldige JSON ({e})")

    # 4. Alle gerecht-id's uit het menu (index.html) + eventuele extra gerechten
    menu_ids = set(_menu_ids(html) or [])
    extra = data.get("extra.json", [])
    if isinstance(extra, list):
        for it in extra:
            if isinstance(it, dict) and it.get("id"):
                menu_ids.add(it["id"])
    if menu_ids:
        ok(f"{len(menu_ids)} gerechten in het menu herkend")
    else:
        warn("kon geen gerecht-id's uit index.html lezen")

    # 5. Inhoudelijke checks per databestand
    def as_map(fn):
        d = data.get(fn)
        if d is None: return None
        if not isinstance(d, dict):
            err(f"{fn}: verwacht een object"); return None
        return d

    def check_ids(fn, validate=None, typ=""):
        d = as_map(fn)
        if d is None: return
        for k, v in d.items():
            if menu_ids and k not in menu_ids:
                warn(f"{fn}: verwijst naar onbekend gerecht '{k}'")
            if validate and validate(v) is False:
                err(f"{fn}: gerecht '{k}' heeft een ongeldige {typ}")
        ok(f"{fn}: {len(d)} vermelding(en) gecontroleerd")

    check_ids("prijzen.json",  lambda v: isinstance(v,(int,float)) and v>=0, "prijs")
    check_ids("nummers.json",  lambda v: isinstance(v,int) and v>0,          "bestelnummer")
    check_ids("pittigheid.json", lambda v: isinstance(v,int) and 0<=v<=3,    "pittigheid")
    check_ids("fotos.json",    lambda v: isinstance(v,str) and (v.startswith("http") or v.startswith("data:image")), "foto")
    for fn in ("namen.json","omschrijvingen.json","recepten.json","serveertips.json"):
        check_ids(fn)

    # verwijderd/verborgen: lijsten van id's
    for fn in ("verwijderd.json","verborgen.json"):
        if fn in data:
            d = data[fn]
            if not isinstance(d, list):
                err(f"{fn}: verwacht een lijst")
            else:
                for k in d:
                    if menu_ids and k not in menu_ids:
                        warn(f"{fn}: verwijst naar onbekend gerecht '{k}'")
                ok(f"{fn}: {len(d)} id('s) gecontroleerd")

    # tikkie.json
    if "tikkie.json" in data:
        t = data["tikkie.json"]
        if isinstance(t, dict) and isinstance(t.get("enabled"), bool):
            ok("tikkie.json: geldig (enabled=%s)" % t["enabled"])
        else:
            err("tikkie.json: verwacht {\"enabled\": true/false}")

    # bestelpauze.json
    if "bestelpauze.json" in data:
        b = data["bestelpauze.json"]
        if (isinstance(b, dict) and isinstance(b.get("openedAt"), (int,float))
                and isinstance(b.get("manualPause"), bool)):
            ok("bestelpauze.json: geldig (manualPause=%s)" % b["manualPause"])
        else:
            err("bestelpauze.json: verwacht {openedAt: getal, manualPause: true/false}")

    # 6. Belangrijke bestanden aanwezig
    for asset in ("pink-thai-og.png", ".nojekyll"):
        if os.path.exists(asset): ok(f"{asset} aanwezig")
        else: warn(f"{asset} ontbreekt")

    return html

# ----------------------------------------------------------------------------
def check_live(html):
    # 7. Hoofdpagina bereikbaar + juiste inhoud
    st, body = http(SITE + "/")
    if st == 200 and b"Pink Thai TakeAway" in body:
        ok("live site bereikbaar (200) met juiste inhoud")
    elif st == 200:
        err("live site is bereikbaar maar mist de verwachte inhoud")
    else:
        err(f"live site niet goed bereikbaar (status {st})")

    # 8. Live databestanden bereikbaar + geldig (steekproef op de bekende bestanden)
    for f in ("prijzen.json", "bestelpauze.json", "tikkie.json"):
        st, body = http(f"{SITE}/{f}?t=selfcheck")
        if st == 200:
            try:
                json.loads(body.decode()); ok(f"live {f}: bereikbaar en geldig")
            except Exception:
                err(f"live {f}: bereikbaar maar geen geldige JSON")
        else:
            warn(f"live {f}: status {st}")

    # 9. Deelbanner (voor de linkvoorbeelden) bereikbaar
    st, _ = http(SITE + "/pink-thai-og.png")
    ok("deelbanner bereikbaar") if st == 200 else warn(f"deelbanner status {st}")

    # 10. De bestelkoppeling (Apps Script) — verwerkt bestellingen, agenda en klanten
    m = re.search(r'agendaUrl:\s*"([^"]+)"', html or "")
    if m:
        st, _ = http(m.group(1))
        # Apps Script antwoordt op een GET vaak met 200/302/405 - elk is 'leeft'
        if st is not None:
            ok(f"bestelkoppeling reageert (status {st})")
        else:
            warn("bestelkoppeling reageerde niet")

    # 11. Bedrijfsgegevens (bedrijf.json): bereikbaar, geldig en logisch
    st, body = http(f"{SITE}/bedrijf.json?t=selfcheck")
    if st == 200:
        try:
            b = json.loads(body.decode())
            ok("live bedrijf.json: bereikbaar en geldig")
            af = b.get("afhaal") or {}
            be = b.get("bestel") or {}
            problemen = []
            if af.get("van") and af.get("tot") and str(af["van"]) >= str(af["tot"]):
                problemen.append("afhaal 'vanaf' ligt niet vóór 'tot'")
            if af.get("slotMin") is not None and not (isinstance(af["slotMin"], int) and af["slotMin"] > 0):
                problemen.append("tijdvak-lengte ongeldig")
            if af.get("dag") is not None and not (isinstance(af["dag"], int) and 0 <= af["dag"] <= 6):
                problemen.append("afhaaldag buiten 0-6")
            if be.get("cutoffUur") is not None and not (isinstance(be["cutoffUur"], int) and 0 <= be["cutoffUur"] <= 23):
                problemen.append("deadline-uur buiten 0-23")
            if be.get("cutoffDag") is not None and not (isinstance(be["cutoffDag"], int) and 0 <= be["cutoffDag"] <= 6):
                problemen.append("deadline-dag buiten 0-6")
            warn("bedrijf.json: " + "; ".join(problemen)) if problemen else ok("bedrijf.json: afhaaltijden en deadline logisch")
            # foto van Pink bereikbaar
            cf = b.get("chefFoto")
            if cf:
                fu = cf if str(cf).startswith("http") else f"{SITE}/{str(cf).lstrip('/')}"
                fst, _ = http(fu)
                ok("foto van Pink bereikbaar") if fst == 200 else warn(f"foto van Pink niet bereikbaar (status {fst})")
        except Exception:
            err("live bedrijf.json: bereikbaar maar geen geldige JSON")
    else:
        warn(f"live bedrijf.json: nog niet aanwezig of niet bereikbaar (status {st})")

    # 12. Standaard chef-foto aanwezig (fallback als er geen eigen foto is ingesteld)
    st, _ = http(f"{SITE}/chef.jpg")
    ok("standaard chef-foto (chef.jpg) aanwezig") if st == 200 else warn(f"chef.jpg status {st}")

    # 13. CallMeBot-melding + capaciteit (via veilige publieke statuscheck; vereist script v12+)
    if m:
        sep = "&" if "?" in m.group(1) else "?"
        st2, body2 = http(m.group(1) + sep + "actie=statuscheck&t=selfcheck")
        conf = None
        if st2 and body2:
            try:
                d = json.loads(body2.decode())
                if d.get("ok"):
                    conf = d
            except Exception:
                pass
        if conf is not None:
            if conf.get("cbConfigured"):
                ok("CallMeBot-melding: telefoon en key ingesteld")
            else:
                warn("CallMeBot-melding: geen telefoon/key ingesteld — bestellingen sturen geen WhatsApp")
            ok("capaciteitslimieten: eigen waarden ingesteld" if conf.get("maxConfigured") else "capaciteitslimieten: standaardwaarden actief")
        else:
            warn("CallMeBot/capaciteit-status kon niet worden opgevraagd (script mogelijk nog niet op v12)")

    # 14. TLS-certificaat: vervaldatum bewaken
    try:
        host = SITE.split("://", 1)[-1].split("/", 1)[0]
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=20) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
        na = cert.get("notAfter")
        exp = datetime.fromtimestamp(ssl.cert_time_to_seconds(na), timezone.utc)
        dagen = (exp - datetime.now(timezone.utc)).days
        datum = exp.strftime("%d-%m-%Y")
        if dagen < 0:
            err(f"TLS-certificaat is VERLOPEN (sinds {datum})")
        elif dagen <= 21:
            warn(f"TLS-certificaat verloopt binnenkort: nog {dagen} dagen (tot {datum})")
        else:
            ok(f"TLS-certificaat geldig, nog {dagen} dagen (tot {datum})")
    except Exception as e:
        warn(f"TLS-certificaat kon niet worden gecontroleerd ({e})")

    # 15. Doorverwijzingen: http\u2192https en oud github.io\u2192eigen domein
    st, loc = redirect_target("http://pinkthaitakeaway.nl/")
    if st in (301, 302, 307, 308) and (loc or "").startswith("https://"):
        ok("http stuurt door naar https")
    elif st == 200:
        warn("http wordt niet doorgestuurd naar https (staat 'Enforce HTTPS' aan?)")
    else:
        warn(f"http-doorverwijzing onduidelijk (status {st})")
    st, loc = redirect_target("https://pinkthaitakeaway.github.io/")
    if st in (301, 302, 307, 308) and "pinkthaitakeaway.nl" in (loc or ""):
        ok("oud github.io-adres stuurt door naar eigen domein")
    else:
        warn(f"github.io stuurt (nog) niet door naar eigen domein (status {st})")

# ----------------------------------------------------------------------------
def write_health(groep, items, reset=False):
    """Voegt de bevindingen toe aan health.json (dashboard in beheer)."""
    path = "health.json"
    h = {"updated": None, "groepen": []}
    if not reset:
        try: h = json.load(open(path, encoding="utf-8"))
        except Exception: pass
    h["groepen"] = [g for g in h.get("groepen", []) if g.get("naam") != groep]
    h["groepen"].append({"naam": groep, "items": items})
    h["updated"] = datetime.now(timezone.utc).isoformat()
    try:
        json.dump(h, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        print("kon health.json niet schrijven:", e)

# ----------------------------------------------------------------------------
# Restant-/opruimingscontrole: spoort achtergebleven onderdelen op in index.html
# (dode mailfuncties/teksten, taalblok-asymmetrie, verweesde vertaalsleutels en
#  ongebruikte data-/beeldbestanden). Alles als waarschuwing: blokkeert deploy niet.
# ----------------------------------------------------------------------------
def _skip_string(s, i, q):
    i += 1; n = len(s)
    while i < n:
        c = s[i]
        if c == "\\": i += 2; continue
        if q == "`" and c == "$" and i + 1 < n and s[i + 1] == "{":
            i = _match_brace(s, i + 1) + 1; continue
        if c == q: return i
        i += 1
    return n - 1

def _match_brace(s, oi):
    open_c = s[oi]; depth = 0; i = oi; n = len(s)
    while i < n:
        c = s[i]
        if c in "'\"`":
            i = _skip_string(s, i, c); i += 1; continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i); i = n if j < 0 else j; continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i); i = n if j < 0 else j + 2; continue
        if c in "{[": depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0: return i
        i += 1
    return n - 1

def _collect_keys(body):
    """Alle sleutels op elke diepte binnen objectliteralen, recursief."""
    keys = []; i = 0; n = len(body); stack = [("{", True)]
    while i < n:
        c = body[i]; typ, expect = stack[-1]
        if c in "'\"`":
            end = _skip_string(body, i, c)
            if typ == "{" and expect:
                j = end + 1
                while j < n and body[j] in " \t\r\n": j += 1
                if j < n and body[j] == ":":
                    keys.append(body[i + 1:end]); stack[-1] = ("{", False); i = j + 1; continue
            i = end + 1; continue
        if c == "/" and i + 1 < n and body[i + 1] == "/":
            j = body.find("\n", i); i = n if j < 0 else j; continue
        if c == "/" and i + 1 < n and body[i + 1] == "*":
            j = body.find("*/", i); i = n if j < 0 else j + 2; continue
        if c in "{[(":
            stack.append((c if c != "(" else "(", c == "{")); i += 1; continue
        if c in "}])":
            if len(stack) > 1: stack.pop()
            i += 1; continue
        if typ == "{":
            if c == ",": stack[-1] = ("{", True); i += 1; continue
            if expect and (c.isalpha() or c in "_$"):
                m = re.match(r"[A-Za-z_$][\w$]*", body[i:]); ident = m.group(0)
                j = i + len(ident); k = j
                while k < n and body[k] in " \t\r\n": k += 1
                if k < n and body[k] == ":":
                    keys.append(ident); stack[-1] = ("{", False); i = k + 1; continue
                i = j; continue
        i += 1
    return keys

def _top_level_entries(body):
    entries = []; i = 0; n = len(body); depth = 0; expect = True; cur = None
    while i < n:
        c = body[i]
        if c in "'\"`":
            end = _skip_string(body, i, c)
            if depth == 0 and expect:
                j = end + 1
                while j < n and body[j] in " \t\r\n": j += 1
                if j < n and body[j] == ":":
                    cur = [body[i + 1:end], j + 1, None]; expect = False; i = j + 1; continue
            i = end + 1; continue
        if c == "/" and i + 1 < n and body[i + 1] == "/":
            j = body.find("\n", i); i = n if j < 0 else j; continue
        if c == "/" and i + 1 < n and body[i + 1] == "*":
            j = body.find("*/", i); i = n if j < 0 else j + 2; continue
        if c in "{[(": depth += 1; i += 1; continue
        if c in "}])": depth -= 1; i += 1; continue
        if depth == 0:
            if c == ",":
                if cur: cur[2] = i; entries.append(cur); cur = None
                expect = True; i += 1; continue
            if expect and (c.isalpha() or c in "_$"):
                m = re.match(r"[A-Za-z_$][\w$]*", body[i:]); ident = m.group(0)
                j = i + len(ident); k = j
                while k < n and body[k] in " \t\r\n": k += 1
                if k < n and body[k] == ":":
                    cur = [ident, k + 1, None]; expect = False; i = k + 1; continue
                i = j; continue
        i += 1
    if cur: cur[2] = n; entries.append(cur)
    return entries

def _dict_lang_keys(html, name):
    try:
        m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\{", html)
        if not m: return None
        obj_open = html.index("{", m.end() - 1)
        body = html[obj_open + 1:_match_brace(html, obj_open)]
        out = {}
        for key, vs, ve in _top_level_entries(body):
            if key in ("nl", "en", "th"):
                val = body[vs:ve].strip()
                if val.startswith("{"):
                    out[key] = _collect_keys(val[1:_match_brace(val, 0)])
        return out
    except Exception:
        return None

_DICTS = ("I18N", "VOLG_T", "BEHEER_T", "LOGIN_T")
_MAIL_MARKERS = [
    (r"MailApp", "MailApp"), (r"GmailApp", "GmailApp"), (r"\.sendEmail\b", "sendEmail"),
    (r"createDraft", "createDraft"), (r"getInboxThreads", "getInboxThreads"),
    (r'actie\s*:\s*"bedankmail"', 'actie:bedankmail'), (r'actie\s*:\s*"bulkmail"', 'actie:bulkmail'),
    (r'actie\s*:\s*"inloglink"', 'actie:inloglink'), (r'actie\s*:\s*"mailcount"', 'actie:mailcount'),
    (r"fetchMailCount", "fetchMailCount"), (r"stuurInloglink", "stuurInloglink"),
    (r"klantBulk", "klantBulk"), (r"klantBedank", "klantBedank"),
    (r"\bbedankBtn\b", "bedankBtn"), (r"\bbedankAl\b", "bedankAl"), (r"confBedank", "confBedank"),
    (r"badgeBedankt", "badgeBedankt"), (r"bulkOnderwerp", "bulkOnderwerp"), (r"bulkBericht", "bulkBericht"),
    (r"bulkConf", "bulkConf"), (r"bulkBezig", "bulkBezig"), (r"\bbulk\s*:", "bulk"),
    (r"tBedankVerz", "tBedankVerz"), (r"tMailVerz", "tMailVerz"), (r"tMailFout", "tMailFout"),
    (r"tGeenMail", "tGeenMail"), (r"tBulkKlaar", "tBulkKlaar"), (r"tBulkFout", "tBulkFout"),
    (r"promptOnderwerp", "promptOnderwerp"), (r"promptBericht", "promptBericht"),
    (r"losseMail", "losseMail"), (r"emailFout", "emailFout"), (r"emailOfTel", "emailOfTel"),
    (r"impGeenEmail", "impGeenEmail"),
    (r"labelEmail", "labelEmail"), (r"phEmail", "phEmail"), (r'"fEmail"', "fEmail-veld"), (r"\bemailOk\b", "emailOk"),
]
_MAIL_KEY_NAMES = {
    "bedankBtn", "bedankAl", "confBedank", "badgeBedankt", "bulkOnderwerp", "bulkOnderwerpDef",
    "bulkBericht", "bulkConf", "bulkBezig", "bulk", "tBedankVerz", "tMailVerz", "tMailFout",
    "tGeenMail", "tBulkKlaar", "tBulkFout", "promptOnderwerp", "promptOnderwerpDef", "promptBericht",
    "losseMail", "emailFout", "emailOfTel", "impGeenEmail", "labelEmail", "phEmail",
}

def restant_checks(html):
    """Geeft een lijst met health-items (naam/status) voor de groep 'Opruiming (restanten)'."""
    items = []
    if not html:
        return [{"naam": "restant-controle overgeslagen (geen index.html)", "status": "warn"}]

    # 1. Dode mailfuncties, mail-teksten en het klant-e-mailveld
    present, voork = [], 0
    for pat, label in _MAIL_MARKERS:
        c = len(re.findall(pat, html))
        if c: present.append(label); voork += c
    if present:
        vb = ", ".join(present[:5]) + (f" (+{len(present) - 5})" if len(present) > 5 else "")
        items.append({"naam": f"mail-restanten: {len(present)} soort(en), {voork} voorkomen(s) in index.html — {vb}", "status": "warn"})
    else:
        items.append({"naam": "geen mail-restanten in index.html", "status": "ok"})

    # 2. Taalpariteit nl/en/th per woordenboek
    defined = set(); gdefs = {}; parity_problem = False
    for name in _DICTS:
        lk = _dict_lang_keys(html, name)
        if not lk: continue
        for v in lk.values():
            defined.update(v)
            for k in v: gdefs[k] = gdefs.get(k, 0) + 1
        if "nl" not in lk: continue
        base = set(lk["nl"]); msgs = []
        for lang in ("en", "th"):
            s = set(lk.get(lang, []))
            miss = sorted(base - s); extra = sorted(s - base)
            if miss:  msgs.append(f"{lang} mist {len(miss)} ({', '.join(miss[:4])})")
            if extra: msgs.append(f"{lang} heeft {len(extra)} extra ({', '.join(extra[:4])})")
        if msgs:
            parity_problem = True
            items.append({"naam": f"taalblok {name}: " + "; ".join(msgs), "status": "warn"})
    if not parity_problem:
        items.append({"naam": "taalblokken nl/en/th in balans", "status": "ok"})

    # 3. Verweesde vertaalsleutels (conservatief: alleen als de naam nergens
    #    anders dan in de definitie voorkomt; mail-sleutels al gemeld bij 1)
    orphans = []
    for key in sorted(defined):
        if len(key) < 4 or key in _MAIL_KEY_NAMES: continue
        total = len(re.findall(r"\b" + re.escape(key) + r"\b", html))
        if total - gdefs.get(key, 0) <= 0: orphans.append(key)
    if orphans:
        vb = ", ".join(orphans[:8]) + (f" (+{len(orphans) - 8})" if len(orphans) > 8 else "")
        items.append({"naam": f"{len(orphans)} vertaalsleutel(s) lijken ongebruikt (mogelijke restanten): {vb}", "status": "warn"})
    else:
        items.append({"naam": "geen verweesde vertaalsleutels", "status": "ok"})

    # 4. Ongebruikte data-/beeldbestanden in de repo
    corpus = html
    _html_files = [f for f in os.listdir(".") if f.lower().endswith(".html")]
    for extra in tuple(_html_files) + ("selfcheck.py", "health-status.py", ".github/workflows/static.yml",
                  "sitemap.xml", "robots.txt", "mutatietest.js"):
        try: corpus += "\n" + open(extra, encoding="utf-8", errors="replace").read()
        except Exception: pass
    allow = {"health.json", "health-status.json"}
    try:
        files = [f for f in os.listdir(".")
                 if "." in f and f.rsplit(".", 1)[-1].lower() in ("json", "png", "jpg", "jpeg", "webp", "gif")]
    except Exception:
        files = []
    orphan_files = sorted(f for f in files if f not in allow and f not in corpus)
    if orphan_files:
        items.append({"naam": f"{len(orphan_files)} ongebruikt bestand(en) in de repo: {', '.join(orphan_files[:6])}", "status": "warn"})
    else:
        items.append({"naam": "geen ongebruikte data-/beeldbestanden", "status": "ok"})

    return items

# ----------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Aanvullende controlegroepen (alleen repo-modus). Elke functie krijgt de
# index.html-inhoud en geeft een lijst {naam,status} terug. 'err'-items
# blokkeren de publicatie (tellen mee in de exitcode); 'warn' niet.
# ---------------------------------------------------------------------------
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".github"}
_BIN_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
            ".woff", ".woff2", ".ttf", ".otf", ".pdf", ".zip")
_SECRET_PATTERNS = [
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "GitHub PAT (classic)"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{60,}"), "GitHub PAT (fine-grained)"),
    (re.compile(r"gh[osru]_[A-Za-z0-9]{36}"), "GitHub-token"),
    (re.compile(r"AIza[A-Za-z0-9_\-]{35}"), "Google API-sleutel"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AWS access key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack-token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
]

def security_checks(html):
    """Scant alle tekstbestanden in de repo op per ongeluk gecommitte geheimen.
    Meldt NOOIT de aangetroffen waarde (health.json staat in een publieke repo)."""
    hits = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in files:
            if fn.lower().endswith(_BIN_EXT): continue
            p = os.path.join(root, fn)
            try: txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception: continue
            for rx, label in _SECRET_PATTERNS:
                if rx.search(txt):
                    hits.append((os.path.relpath(p, "."), label))
    if hits:
        seen = sorted({f"{f} ({lab})" for f, lab in hits})
        return [{"naam": "mogelijke geheime sleutel(s) gevonden: " + ", ".join(seen[:6]) +
                 (f" (+{len(seen)-6})" if len(seen) > 6 else "") +
                 " — verwijderen uit git-historie en roteren", "status": "err"}]
    return [{"naam": "geen tokens/sleutels in repobestanden aangetroffen", "status": "ok"}]

def _json_load(fn, default):
    try: return json.load(open(fn, encoding="utf-8"))
    except Exception: return default

def _menu_dishes(html):
    """Parseert de gerechten uit de MENU-array (id, cat, price, spice, foto, name)."""
    region = _menu_region(html) or ""
    out = []; i = 0; n = len(region)
    while i < n:
        if region[i] == "{":
            e = _match_brace(region, i); blok = region[i:e + 1]
            mid = re.search(r'id:"([^"]+)"', blok)
            if mid:
                mp = re.search(r'\bprice:\s*([0-9]+(?:\.[0-9]+)?)', blok)
                ms = re.search(r'\bspice:\s*(\d+)', blok)
                mc = re.search(r'\bcat:"([^"]*)"', blok)
                mf = re.search(r'\bfoto:"([^"]*)"', blok)
                nm = re.search(r'\bname:\{([^}]*)\}', blok); nb = nm.group(1) if nm else ""
                dm = re.search(r'\bdesc:\{([^}]*)\}', blok); db = dm.group(1) if dm else ""
                def _nl(l, _b=nb):
                    m = re.search(l + r':"([^"]*)"', _b); return m.group(1) if m else ""
                def _dl(l, _b=db):
                    m = re.search(l + r':"([^"]*)"', _b); return m.group(1) if m else ""
                out.append({"id": mid.group(1), "cat": mc.group(1) if mc else None,
                            "price": float(mp.group(1)) if mp else None,
                            "spice": int(ms.group(1)) if ms else None,
                            "foto": mf.group(1) if mf else "",
                            "name": {l: _nl(l) for l in ("nl", "en", "th")},
                            "desc": {l: _dl(l) for l in ("nl", "en", "th")}})
            i = e + 1
        else:
            i += 1
    return out

def dish_checks(html):
    """Controleert per bestelbaar gerecht: prijs, categorie, pittigheid, foto,
    en of de naam in nl/en/th aanwezig is. Overrides in de .json-bestanden
    gaan vóór de inline waarde in de MENU-array."""
    dishes = _menu_dishes(html)
    if not dishes:
        return [{"naam": "kon geen gerechten uit de MENU-array lezen", "status": "warn"}]
    items = []
    cats = set(re.findall(r'\{id:"([^"]+)",\s*nm:\{', html))
    try:
        _cj = json.load(open("categorieen.json", encoding="utf-8"))
        _clist = _cj if isinstance(_cj, list) else (_cj.get("custom", []) if isinstance(_cj, dict) else [])
        for c in _clist:
            if isinstance(c, dict) and c.get("id"):
                cats.add(c["id"])
    except Exception:
        pass
    prijzen = _json_load("prijzen.json", {}); pit = _json_load("pittigheid.json", {})
    fotos = _json_load("fotos.json", {}); namen = _json_load("namen.json", {})
    hidden = set(_json_load("verborgen.json", [])) | set(_json_load("verwijderd.json", []))
    order = [d for d in dishes if d["id"] not in hidden]

    def eff_price(d):
        try: return float(prijzen.get(d["id"], d["price"]))
        except Exception: return None
    geen = [d["id"] for d in order if not eff_price(d) or eff_price(d) <= 0]
    raar = [d["id"] for d in order if eff_price(d) and not (2 <= eff_price(d) <= 40)]
    if geen:
        items.append({"naam": "gerecht(en) zonder geldige prijs: " + ", ".join(geen[:8]), "status": "err"})
    elif raar:
        items.append({"naam": "prijs buiten verwacht bereik (\u20ac2\u2013\u20ac40): " + ", ".join(raar[:8]), "status": "warn"})
    else:
        items.append({"naam": f"alle {len(order)} bestelbare gerechten hebben een geldige prijs", "status": "ok"})

    badcat = [d["id"] for d in order if d["cat"] not in cats]
    items.append({"naam": ("gerecht(en) met onbekende categorie: " + ", ".join(badcat[:8])) if badcat
                  else "alle gerechten verwijzen naar een bestaande categorie",
                  "status": "err" if badcat else "ok"})

    def eff_spice(d):
        try: return int(pit.get(d["id"], d["spice"]))
        except Exception: return None
    badspice = [d["id"] for d in order if eff_spice(d) not in (0, 1, 2, 3)]
    items.append({"naam": ("ongeldige pittigheid (verwacht 0\u20133): " + ", ".join(badspice[:8])) if badspice
                  else "pittigheid van alle gerechten is geldig (0\u20133)",
                  "status": "warn" if badspice else "ok"})

    def has_photo(d):
        return bool((fotos.get(d["id"]) or "").strip() or (d["foto"] or "").strip())
    geenfoto = [d["id"] for d in order if not has_photo(d)]
    items.append({"naam": ("bestelbare gerechten zonder foto: " + ", ".join(geenfoto[:8])) if geenfoto
                  else "alle bestelbare gerechten hebben een foto",
                  "status": "warn" if geenfoto else "ok"})

    def val(d, l):
        return (namen.get(d["id"], {}).get(l) or d["name"].get(l) or "").strip()
    geennl = [d["id"] for d in order if not val(d, "nl")]
    missent = [d["id"] for d in order if not val(d, "en") or not val(d, "th")]
    if geennl:
        items.append({"naam": "gerecht(en) zonder Nederlandse naam: " + ", ".join(geennl[:8]), "status": "err"})
    elif missent:
        items.append({"naam": "gerecht(en) zonder EN- of TH-naam: " + ", ".join(missent[:8]), "status": "warn"})
    else:
        items.append({"naam": "alle gerechten hebben een naam in nl/en/th", "status": "ok"})

    # Omschrijvingen compleet én vertaald in nl/en/th (incl. eigen gerechten uit extra.json, met overrides)
    oms = _json_load("omschrijvingen.json", {})
    extra = _json_load("extra.json", [])
    if isinstance(extra, dict): extra = extra.get("items", [])
    weg = set(_json_load("verwijderd.json", [])) | set(_json_load("verwijderdweg.json", []))
    desc_src = {}
    for d in dishes:
        desc_src[d["id"]] = d.get("desc", {}) or {}
    for it in (extra or []):
        if isinstance(it, dict) and it.get("id"):
            desc_src[it["id"]] = it.get("desc", {}) or {}
    def eff_desc(did, l):
        o = oms.get(did, {}); o = o if isinstance(o, dict) else {}
        return (o.get(l) or desc_src.get(did, {}).get(l) or "").strip()
    def _has_thai(s):
        return bool(re.search(r'[\u0E00-\u0E7F]', s or ""))
    dids = [x for x in desc_src if x not in weg]
    geen_nl_d = [x for x in dids if not eff_desc(x, "nl")]
    mis_d = [x for x in dids if eff_desc(x, "nl") and (
                not eff_desc(x, "en") or eff_desc(x, "en") == eff_desc(x, "nl")
                or not eff_desc(x, "th") or eff_desc(x, "th") == eff_desc(x, "nl")
                or not _has_thai(eff_desc(x, "th")))]
    if geen_nl_d:
        items.append({"naam": "gerecht(en) zonder Nederlandse omschrijving: " + ", ".join(geen_nl_d[:8]), "status": "warn"})
    elif mis_d:
        items.append({"naam": "gerecht(en) zonder (vertaalde) EN/TH-omschrijving: " + ", ".join(mis_d[:8]), "status": "warn"})
    else:
        items.append({"naam": "alle gerechten hebben een omschrijving in nl/en/th", "status": "ok"})
    return items

def seo_checks(html):
    """robots.txt (blokkeert de site niet?), sitemap.xml (geldig + eigen domein),
    JSON-LD structured data en de belangrijkste meta-/og-tags."""
    import xml.etree.ElementTree as ET
    items = []
    dom = "pinkthaitakeaway.nl"
    try:
        cn = open("CNAME", encoding="utf-8").read().strip()
        if cn: dom = cn
    except Exception: pass

    # robots.txt
    try: rob = open("robots.txt", encoding="utf-8").read()
    except Exception: rob = None
    if rob is None:
        items.append({"naam": "robots.txt ontbreekt", "status": "warn"})
    else:
        dis = any(re.match(r'\s*Disallow:\s*/\s*$', ln, re.I) for ln in rob.splitlines())
        alw = any(re.match(r'\s*Allow:\s*/\s*$', ln, re.I) for ln in rob.splitlines())
        items.append({"naam": "robots.txt blokkeert de hele site (Disallow: /)" if (dis and not alw)
                      else "robots.txt in orde (site niet geblokkeerd)",
                      "status": "err" if (dis and not alw) else "ok"})
        sm = re.search(r'(?im)^\s*Sitemap:\s*(\S+)', rob)
        if sm and dom not in sm.group(1):
            items.append({"naam": f"robots.txt-sitemap staat op ander domein dan {dom}: {sm.group(1)}", "status": "warn"})

    # sitemap.xml
    try: sx = open("sitemap.xml", encoding="utf-8").read()
    except Exception: sx = None
    if sx is None:
        items.append({"naam": "sitemap.xml ontbreekt", "status": "warn"})
    else:
        try: ET.fromstring(sx); valid = True
        except Exception: valid = False
        locs = re.findall(r'<loc>\s*([^<]+?)\s*</loc>', sx)
        if not valid:
            items.append({"naam": "sitemap.xml is geen geldige XML", "status": "err"})
        elif not locs:
            items.append({"naam": "sitemap.xml bevat geen <loc>-URL's", "status": "warn"})
        elif any(dom not in u for u in locs):
            items.append({"naam": f"sitemap.xml gebruikt niet (overal) het eigen domein {dom}", "status": "warn"})
        else:
            items.append({"naam": f"sitemap.xml geldig ({len(locs)} URL(s), eigen domein)", "status": "ok"})

    # JSON-LD
    m = re.search(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S)
    if not m:
        items.append({"naam": "geen JSON-LD (structured data) op de pagina", "status": "warn"})
    else:
        try: ld = json.loads(m.group(1))
        except Exception: ld = None
        if not isinstance(ld, dict):
            items.append({"naam": "JSON-LD aanwezig maar geen geldige JSON", "status": "warn"})
        else:
            mis = [k for k in ("name", "address", "telephone") if not ld.get(k)]
            items.append({"naam": ("JSON-LD mist veld(en): " + ", ".join(mis)) if mis
                          else f"JSON-LD compleet (@type {ld.get('@type', '?')})",
                          "status": "warn" if mis else "ok"})

    # Meta-/og-tags
    meta = {"title": re.search(r'<title>([^<]*)</title>', html),
            "meta-description": re.search(r'<meta name="description" content="([^"]*)"', html),
            "og:title": re.search(r'<meta property="og:title" content="([^"]*)"', html),
            "og:image": re.search(r'<meta property="og:image" content="([^"]*)"', html),
            "canonical": re.search(r'<link rel="canonical" href="([^"]*)"', html)}
    miss = [k for k, v in meta.items() if not (v and v.group(1).strip())]
    items.append({"naam": ("meta-tags ontbreken/leeg: " + ", ".join(miss)) if miss
                  else "title, meta-description, og-tags en canonical aanwezig",
                  "status": "warn" if miss else "ok"})
    return items

_EXPECTED_ACTIES = {
    "account", "adminget", "adminlog", "afgehaald", "bestelling", "betaald", "bezoek",
    "bezoekreset", "bezoekstats", "cbget", "cbset", "klanten", "klantimport", "klantnieuw",
    "maxget", "maxset", "notitie", "slots", "taal", "uitgenodigd", "versie", "verwijder",
    "verwijderklant", "volg", "zatnu", "audit", "auditget", "auditwis", "adminwis",
}

def _dict_body(html, name):
    m = re.search(r'const\s+' + name + r'\s*=\s*\{', html)
    if not m: return None
    o = html.index("{", m.end() - 1)
    return html[o + 1:_match_brace(html, o)]

def _obj_dup_keys(body, path=""):
    dups = []; seen = {}
    for k, vs, ve in _top_level_entries(body):
        seen[k] = seen.get(k, 0) + 1
        val = body[vs:ve].strip().rstrip(",").strip()
        if val.startswith("{"):
            dups += _obj_dup_keys(val[1:_match_brace(val, 0)], path + k + ".")
    dups += [path + k for k, c in seen.items() if c > 1]
    return dups

def _kv_pairs(body, prefix=""):
    out = {}
    for k, vs, ve in _top_level_entries(body):
        val = body[vs:ve].strip().rstrip(",").strip()
        if val.startswith("{"):
            out.update(_kv_pairs(val[1:_match_brace(val, 0)], prefix + k + "."))
        else:
            out[prefix + k] = val
    return out

def maintenance_checks(html):
    """Operationele hygiëne: versheid van de laatste run, bestandsgrootte,
    dubbele vertaalsleutels, placeholder-consistentie en client/GAS-contract."""
    items = []

    # 1. Versheid van de laatste Action-run
    try:
        hs = json.load(open("health-status.json", encoding="utf-8"))
        ts = hs.get("opgeslagen") or hs.get("selfcheck", {}).get("updated")
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).days
        items.append({"naam": (f"laatste Health-run {age} dag(en) geleden \u2014 controle lijkt gestopt"
                               if age > 3 else f"laatste Health-run is recent ({age} dag(en) geleden)"),
                      "status": "warn" if age > 3 else "ok"})
    except Exception:
        items.append({"naam": "kon versheid van health-status.json niet bepalen", "status": "warn"})

    # 2. Bestandsgrootte + zware inline-afbeeldingen
    kb = len(html.encode("utf-8")) / 1024
    big = [m for m in re.findall(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', html) if len(m) > 60000]
    msgs = []
    if kb > 450: msgs.append(f"index.html is {kb:.0f} KB (boven 450)")
    if big: msgs.append(f"{len(big)} grote inline-afbeelding(en) ingebed")
    items.append({"naam": ("; ".join(msgs)) if msgs
                  else f"index.html compact ({kb:.0f} KB, geen zware inline-afbeeldingen)",
                  "status": "warn" if msgs else "ok"})

    # 3. Dubbele sleutels binnen één taalobject
    alldups = []
    for name in ("I18N", "VOLG_T", "BEHEER_T", "LOGIN_T"):
        b = _dict_body(html, name)
        if b: alldups += [name + "." + d for d in _obj_dup_keys(b)]
    items.append({"naam": (f"{len(alldups)} dubbel gedefinieerde vertaalsleutel(s): " +
                           ", ".join(alldups[:6]) + (f" (+{len(alldups)-6})" if len(alldups) > 6 else ""))
                  if alldups else "geen dubbel gedefinieerde vertaalsleutels",
                  "status": "warn" if alldups else "ok"})

    # 4. Placeholder-consistentie ${...} tussen nl/en/th
    mism = []
    for name in ("I18N", "VOLG_T", "BEHEER_T", "LOGIN_T"):
        b = _dict_body(html, name)
        if not b: continue
        lb = {}
        for k, vs, ve in _top_level_entries(b):
            if k in ("nl", "en", "th"):
                v = b[vs:ve].strip().rstrip(",").strip()
                if v.startswith("{"): lb[k] = _kv_pairs(v[1:_match_brace(v, 0)])
        for path, val in lb.get("nl", {}).items():
            if "${" not in val: continue
            want = sorted(re.findall(r'\$\{([^}]+)\}', val))
            for l in ("en", "th"):
                ov = lb.get(l, {}).get(path)
                if ov is not None and sorted(re.findall(r'\$\{([^}]+)\}', ov)) != want:
                    mism.append(f"{name}.{path} ({l})")
    items.append({"naam": ("placeholder-verschil tussen talen: " + ", ".join(mism[:6])) if mism
                  else "template-placeholders (${...}) consistent tussen nl/en/th",
                  "status": "warn" if mism else "ok"})

    # 5. Client-acties vs verwacht GAS-contract
    acties = set(re.findall(r'actie\s*:\s*"([^"]+)"', html))
    onbekend = sorted(acties - _EXPECTED_ACTIES)
    items.append({"naam": ("onbekende client-actie(s) t.o.v. verwacht GAS-contract: " + ", ".join(onbekend))
                  if onbekend else f"alle {len(acties)} client-acties staan in het verwachte GAS-contract",
                  "status": "warn" if onbekend else "ok"})
    return items

def openingsbericht_checks(html):
    """Openingsbericht: taal-aankondigingspagina's linken naar bestaande pagina's,
    elk met een bestaand og:image, een og:title (nodig om het plaatje te tonen)
    en een doorverwijzing naar de bestelsite. Alles als waarschuwing (niet blokkerend)."""
    items = []
    m = re.search(r"function berLinkFor\(taal\)\{.*?\}", html, re.S)
    linked = sorted(set(re.findall(r"open[-a-z0-9]*\.html", m.group(0)))) if m else []
    for pg in linked:
        if not os.path.exists(pg):
            items.append({"naam": f"Openingsbericht linkt naar ontbrekende pagina: {pg}", "status": "warn"})
    try:
        pages = sorted(f for f in os.listdir(".") if re.match(r"open[-a-z0-9]*\.html$", f))
    except Exception:
        pages = []
    checked = 0
    for pg in pages:
        try:
            c = open(pg, encoding="utf-8").read()
        except Exception:
            continue
        checked += 1
        img = re.search(r'og:image"\s+content="([^"]+)"', c)
        if not img:
            items.append({"naam": f"{pg}: geen og:image \u2014 WhatsApp toont geen plaatje", "status": "warn"})
        else:
            fname = img.group(1).rstrip("/").split("/")[-1]
            if fname and not os.path.exists(fname):
                items.append({"naam": f"{pg}: og:image verwijst naar ontbrekend bestand {fname}", "status": "warn"})
        if "og:title" not in c:
            items.append({"naam": f"{pg}: geen og:title \u2014 WhatsApp toont het plaatje dan niet", "status": "warn"})
        if "location.replace" not in c and 'http-equiv="refresh"' not in c:
            items.append({"naam": f"{pg}: geen doorverwijzing naar de bestelsite", "status": "warn"})
    if not items:
        items.append({"naam": f"Openingsbericht: {checked} taal-pagina('s) in orde (plaatje, og:title en doorverwijzing aanwezig)", "status": "ok"})
    return items

# Register van extra groepen (repo-modus). Nieuwe groepen worden hier toegevoegd.
EXTRA_GROUPS = [
    ("Openingsbericht", openingsbericht_checks),
    ("Beveiliging", security_checks),
    ("Gerechten & data", dish_checks),
    ("SEO & vindbaarheid", seo_checks),
    ("Onderhoud", maintenance_checks),
]

def main():
    live = "--live" in sys.argv
    extra_block = False
    if live:
        html = open("index.html", encoding="utf-8").read() if os.path.exists("index.html") else ""
        check_live(html)
        title = "LIVE-CONTROLE"
    else:
        html = check_repo()
        title = "REPO-CONTROLE"

    print(f"\n=== {title}: RESULTAAT ===")
    for m in oks:      print("  \u2713", m)
    for m in warnings: print("  \u26a0", m)
    for m in errors:   print("  \u2717", m)
    print(f"\n{len(oks)} ok \u00b7 {len(warnings)} waarschuwing(en) \u00b7 {len(errors)} fout(en)")
    for m in warnings: print(f"::warning::{m}")
    for m in errors:   print(f"::error::{m}")

    # Net rapport in de GitHub-samenvatting (zichtbaar bovenaan de run)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        vlag = "\u2705 alles in orde" if not errors else "\u274c fout gevonden"
        if not errors and warnings: vlag = "\u26a0\ufe0f let op"
        with open(summ, "a", encoding="utf-8") as f:
            f.write(f"## {title} \u2014 {vlag}\n\n")
            f.write(f"**{len(oks)} ok \u00b7 {len(warnings)} waarschuwing(en) \u00b7 {len(errors)} fout(en)**\n\n")
            for m in errors:   f.write(f"- \u274c {m}\n")
            for m in warnings: f.write(f"- \u26a0\ufe0f {m}\n")
            for m in oks:      f.write(f"- \u2705 {m}\n")
            f.write("\n")

    # Health-gegevens wegschrijven voor het dashboard in beheer
    groep = "Live site & diensten" if live else "Bronbestanden"
    items = ([{"naam": m, "status": "err"}  for m in errors] +
             [{"naam": m, "status": "warn"} for m in warnings] +
             [{"naam": m, "status": "ok"}   for m in oks])
    write_health(groep, items, reset=(not live))

    # Opruiming (restanten): aparte groep, alleen in repo-modus. Waarschuwingen
    # hier blokkeren de publicatie niet (tellen niet mee in de exitcode).
    if not live:
        ritems = restant_checks(html)
        write_health("Opruiming (restanten)", ritems, reset=False)
        sym_c = {"ok": "\u2713", "warn": "\u26a0", "err": "\u2717"}
        print("\n=== OPRUIMING (RESTANTEN) ===")
        for it in ritems:
            print("  " + sym_c[it["status"]] + " " + it["naam"])
        if summ:
            sym_s = {"ok": "\u2705", "warn": "\u26a0\ufe0f", "err": "\u274c"}
            rwarn = sum(1 for it in ritems if it["status"] != "ok")
            with open(summ, "a", encoding="utf-8") as f:
                f.write("## OPRUIMING (RESTANTEN) \u2014 " +
                        ("\u26a0\ufe0f " + str(rwarn) + " aandachtspunt(en)" if rwarn else "\u2705 schoon") + "\n\n")
                for it in ritems:
                    f.write(f"- {sym_s[it['status']]} {it['naam']}\n")
                f.write("\n")

    # Aanvullende controlegroepen (alleen repo-modus)
    if not live:
        sym_c = {"ok": "\u2713", "warn": "\u26a0", "err": "\u2717"}
        sym_s = {"ok": "\u2705", "warn": "\u26a0\ufe0f", "err": "\u274c"}
        for gnaam, gfn in EXTRA_GROUPS:
            try:
                gitems = gfn(html)
            except Exception as e:
                gitems = [{"naam": f"{gnaam}: controle mislukt ({e})", "status": "warn"}]
            write_health(gnaam, gitems, reset=False)
            if any(it["status"] == "err" for it in gitems): extra_block = True
            print(f"\n=== {gnaam.upper()} ===")
            for it in gitems:
                print("  " + sym_c[it["status"]] + " " + it["naam"])
            if summ:
                nprob = sum(1 for it in gitems if it["status"] != "ok")
                with open(summ, "a", encoding="utf-8") as f:
                    f.write(f"## {gnaam.upper()} \u2014 " +
                            ("\u274c/\u26a0\ufe0f " + str(nprob) + " aandachtspunt(en)" if nprob else "\u2705 in orde") + "\n\n")
                    for it in gitems:
                        f.write(f"- {sym_s[it['status']]} {it['naam']}\n")
                    f.write("\n")

    sys.exit(1 if (errors or extra_block) else 0)

if __name__ == "__main__":
    main()
