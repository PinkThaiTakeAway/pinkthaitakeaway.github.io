#!/usr/bin/env python3
"""Schrijft health-status.json met de LIVE scriptversie.
Draait in GitHub Actions (kan script.google.com bereiken)."""
import json, re, urllib.request, datetime

def http(url, timeout=45):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "health-status"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read()
    except Exception as e:
        return None, str(e).encode()

html = open("index.html", encoding="utf-8").read()
m  = re.search(r'agendaUrl:\s*"([^"]+)"', html)
agurl = m.group(1) if m else ""
m2 = re.search(r'SCRIPT_VERSIE_VERWACHT\s*=\s*(\d+)', html)
exp = int(m2.group(1)) if m2 else None

ver = None
if agurl:
    sep = "&" if "?" in agurl else "?"
    ts = str(int(datetime.datetime.now().timestamp()))
    st, body = http(agurl + sep + "actie=versie&t=" + ts)
    if st and body:
        try:
            d = json.loads(body.decode("utf-8", "replace"))
            if isinstance(d.get("versie"), int):
                ver = d["versie"]
        except Exception:
            pass

obj = {
    "opgeslagen": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "scriptVersie": ver,
    "verwachteVersie": exp,
    "versieOk": (ver is not None and exp is not None and ver >= exp),
    "bron": "automatisch via GitHub Actions (live script bevraagd)",
}
try:
    obj["selfcheck"] = json.load(open("health.json", encoding="utf-8"))
except Exception:
    pass

open("health-status.json", "w", encoding="utf-8").write(json.dumps(obj, indent=2, ensure_ascii=False))
print("health-status geschreven: scriptVersie=%s versieOk=%s" % (ver, obj["versieOk"]))
