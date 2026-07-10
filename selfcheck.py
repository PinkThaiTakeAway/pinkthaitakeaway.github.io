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

    # 3. Alle aanwezige JSON-bestanden zijn geldig
    data = {}
    for f in sorted(x for x in os.listdir(".") if x.endswith(".json")):
        try:
            data[f] = json.load(open(f, encoding="utf-8"))
            ok(f"{f}: geldige JSON")
        except Exception as e:
            err(f"{f}: ONgeldige JSON ({e})")

    # 4. Alle gerecht-id's uit het menu (index.html) + eventuele extra gerechten
    menu_ids = set(re.findall(r'\bid:"([^"]+)"', html))
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
def main():
    live = "--live" in sys.argv
    if live:
        html = open("index.html", encoding="utf-8").read() if os.path.exists("index.html") else ""
        check_live(html)
        title = "LIVE-CONTROLE"
    else:
        check_repo()
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

    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
