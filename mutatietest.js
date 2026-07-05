/*
 * Mutatie-zelftest voor Pink Thai TakeAway.
 *
 * Laadt de site in een afgeschermde kopie (jsdom, beheer-modus) en voert
 * ELKE beheer-actie echt uit. De "opslaan naar GitHub"-stap wordt onderschept:
 * er gaat dus niets naar de echte repo. Per actie wordt gecontroleerd:
 *   (1) het directe effect in de site zelf, en
 *   (2) dat de juiste opslag met de juiste inhoud zou zijn gebeurd.
 *
 * Sluit af met code 1 als een actie faalt -> GitHub mailt.
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const results = [];
function pass(m) { results.push(["ok", m]); }
function fail(m) { results.push(["err", m]); }

// Databestanden uit de werkmap inlezen (die bestaan in de repo)
function readJSON(f, fallback) {
  try { return JSON.parse(fs.readFileSync(f, "utf8")); } catch (e) { return fallback; }
}
const files = {
  "prijzen.json":     readJSON("prijzen.json", {}),
  "fotos.json":       readJSON("fotos.json", {}),
  "recepten.json":    readJSON("recepten.json", {}),
  "verborgen.json":   readJSON("verborgen.json", []),
  "verwijderd.json":  readJSON("verwijderd.json", []),
  "tikkie.json":      readJSON("tikkie.json", {}),
  "bestelpauze.json": readJSON("bestelpauze.json", {}),
  "extra.json":       readJSON("extra.json", []),
};

const html = fs.readFileSync("index.html", "utf8");
const pushes = {};   // hier belanden de onderschepte opslag-acties

const dom = new JSDOM(html, {
  url: "https://pinkthaitakeaway.github.io/?beheer",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(w) {
    // localStorage-token zodat de opslag-functies "denken" dat ze mogen pushen
    w.localStorage.setItem("tta_ghtoken", "TESTTOKEN");
    w.scrollTo = () => {};
    w.open = () => {};
    w.alert = () => {};
    w.confirm = () => true;
    w.prompt = () => "TESTTOKEN";
    w.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} takeRecords(){return[]} };
    w.matchMedia = () => ({ matches:false, addEventListener(){}, removeEventListener(){} });
    // externe fotokeuze niet echt uitvoeren
    w.pickPhoto = () => Promise.resolve(null);
    w.resolvePhotos = () => {};

    w.fetch = (url, opts) => {
      url = String(url); opts = opts || {};
      // --- opslag onderscheppen (GitHub contents-API) ---
      if (url.includes("api.github.com")) {
        if ((opts.method || "GET").toUpperCase() === "PUT") {
          try {
            const body = JSON.parse(opts.body);
            const p = decodeURIComponent(url.split("/contents/")[1].split("?")[0]);
            const content = Buffer.from(body.content, "base64").toString("utf8");
            try { pushes[p] = JSON.parse(content); } catch (e) { pushes[p] = content; }
          } catch (e) {}
          return Promise.resolve({ ok:true, status:200, json:()=>Promise.resolve({ commit:{ sha:"test" }, content:{ sha:"test" } }) });
        }
        // GET sha
        return Promise.resolve({ ok:true, status:200, json:()=>Promise.resolve({ sha:"testsha" }) });
      }
      // --- databestanden serveren (zoals de site ze bij het laden ophaalt) ---
      const base = url.split("?")[0].split("/").pop();
      if (base in files) {
        return Promise.resolve({ ok:true, status:200, json:()=>Promise.resolve(files[base]) });
      }
      // al het andere: niet gevonden
      return Promise.resolve({ ok:false, status:404, json:()=>Promise.resolve(null) });
    };
  }
});

const w = dom.window, doc = w.document;

function wait(ms){ return new Promise(r=>setTimeout(r, ms)); }

(async () => {
  await wait(600);                 // init laten voltooien
  const ID = "popia";              // bestaand gerecht om op te testen
  const byId = (id) => w.eval(`byId(${JSON.stringify(id)})`);

  async function step(naam, fn, verify, waitMs) {
    try {
      await fn();
      await wait(waitMs || 150);   // async/vertraagde opslag laten registreren
      const msg = verify();
      if (msg === true) pass(naam);
      else fail(`${naam}: ${msg}`);
    } catch (e) {
      fail(`${naam}: uitzondering (${e.message})`);
    }
  }
  const SLOW = 1300;   // livePush wacht ~900ms (debounce) voordat het opslaat

  // 1. Prijs
  await step("Prijs aanpassen", () => w.savePrice(ID, 6.5, null),
    () => (byId(ID).price === 6.5 && pushes["prijzen.json"] && pushes["prijzen.json"][ID] === 6.5) || "effect of opslag klopt niet");

  // 2. Pittigheid
  await step("Pittigheid aanpassen", () => w.savePittigheid(ID, 2, null),
    () => (byId(ID).spice === 2 && pushes["pittigheid.json"] && pushes["pittigheid.json"][ID] === 2) || "effect of opslag klopt niet");

  // 3. Naam
  await step("Naam aanpassen", () => w.saveName(ID, "TestNaam", "TestName", "ทดสอบ", null),
    () => (byId(ID).name.nl === "TestNaam" && pushes["namen.json"] && pushes["namen.json"][ID].nl === "TestNaam") || "effect of opslag klopt niet");

  // 4. Omschrijving
  await step("Omschrijving aanpassen", () => w.saveDesc(ID, "omschr NL", "descr EN", "TH", null),
    () => (byId(ID).desc.nl === "omschr NL" && pushes["omschrijvingen.json"] && pushes["omschrijvingen.json"][ID].nl === "omschr NL") || "effect of opslag klopt niet");

  // 5. Recept
  await step("Recept aanpassen", () => w.saveRecipe(ID, "1 ei\n2 uien", "Kort roerbakken.", null),
    () => (pushes["recepten.json"] && pushes["recepten.json"][ID]) ? true : "geen opslag naar recepten.json");

  // 6. Serveertip (container met één regel opbouwen)
  await step("Serveertip aanpassen", () => {
    const c = doc.createElement("div");
    c.innerHTML = `<div class="serverow"><select class="sv-m"><option value="micro" selected>micro</option></select>`
      + `<input class="sv-t" value="3"><input class="sv-p" value="600 W"><input class="sv-n" value="Halverwege roeren."></div>`;
    w.saveServe(ID, c, null);
  }, () => (pushes["serveertips.json"] && pushes["serveertips.json"][ID] && pushes["serveertips.json"][ID][0].m === "micro") || "geen/verkeerde opslag naar serveertips.json");

  // 7. Foto instellen (gaat via choose -> livePush)
  await step("Foto instellen", () => w.choose(ID, "https://example.com/foto.jpg"),
    () => (pushes["fotos.json"] && pushes["fotos.json"][ID] === "https://example.com/foto.jpg") || "geen/verkeerde opslag naar fotos.json", SLOW);

  // 8. Verbergen
  await step("Gerecht verbergen", () => w.toggleHidden(ID),
    () => (Array.isArray(pushes["verborgen.json"]) && pushes["verborgen.json"].includes(ID)) || "gerecht niet in verborgen.json", SLOW);

  // 9. Weer tonen
  await step("Gerecht weer tonen", () => w.toggleHidden(ID),
    () => (Array.isArray(pushes["verborgen.json"]) && !pushes["verborgen.json"].includes(ID)) || "gerecht nog in verborgen.json", SLOW);

  // 10. Verwijderen
  await step("Gerecht verwijderen", () => w.deleteDish(ID),
    () => (Array.isArray(pushes["verwijderd.json"]) && pushes["verwijderd.json"].includes(ID)) || "gerecht niet in verwijderd.json", SLOW);

  // 11. Herstellen
  await step("Gerecht herstellen", () => w.restoreDish(ID),
    () => (Array.isArray(pushes["verwijderd.json"]) && !pushes["verwijderd.json"].includes(ID)) || "gerecht nog in verwijderd.json", SLOW);

  // 12. Tikkie aan/uit
  await step("Tikkie omschakelen", () => { w._t0 = w.eval("TIKKIE_ON"); w.toggleTikkie(); },
    () => (pushes["tikkie.json"] && pushes["tikkie.json"].enabled === !w._t0) || "tikkie.json niet correct omgeschakeld", SLOW);

  // 13. Bestellen pauzeren/openen
  await step("Bestellen pauzeren/openen", () => w.togglePauze(),
    () => (pushes["bestelpauze.json"] && typeof pushes["bestelpauze.json"].manualPause === "boolean") || "bestelpauze.json niet correct", SLOW);

  // 14. Nieuw gerecht toevoegen
  await step("Nieuw gerecht toevoegen", () => {
    const set = (id,v)=>{ const e=doc.getElementById(id); if(e) e.value=v; };
    set("fNaam","Testgerecht"); set("fPrijs","9.5"); set("fCat","voor");
    set("fSpice","1"); set("fEmoji","🥗"); set("fTh","ทดสอบ");
    set("fNaamEn","Test dish"); set("fNaamTh","ทดสอบ");
    set("fDesc","omschrijving"); set("fDescEn","description"); set("fDescTh","");
    const rh=doc.getElementById("fRh"); if(rh && rh.options.length) rh.value=rh.options[0].value;
    w.saveNewDish();
  }, () => (Array.isArray(pushes["extra.json"]) && pushes["extra.json"].length >= 1 && pushes["extra.json"][pushes["extra.json"].length-1].name.nl === "Testgerecht") || "nieuw gerecht niet correct opgeslagen", SLOW);

  // --- Rapport ---
  const errs = results.filter(r => r[0] === "err");
  console.log("\n=== MUTATIE-ZELFTEST: RESULTAAT ===");
  for (const [t, m] of results) console.log("  " + (t === "ok" ? "\u2713" : "\u2717") + " " + m);
  console.log(`\n${results.length - errs.length}/${results.length} beheer-acties correct`);
  for (const [, m] of errs) console.log(`::error::mutatie-zelftest: ${m}`);

  // Net rapport in de GitHub-samenvatting
  const summ = process.env.GITHUB_STEP_SUMMARY;
  if (summ) {
    const vlag = errs.length ? "\u274c fout gevonden" : "\u2705 alle beheer-acties werken";
    let md = `## MUTATIE-ZELFTEST \u2014 ${vlag}\n\n`;
    md += `**${results.length - errs.length}/${results.length} beheer-acties correct**\n\n`;
    for (const [t, m] of results) md += `- ${t === "ok" ? "\u2705" : "\u274c"} ${m}\n`;
    md += "\n";
    fs.appendFileSync(summ, md);
  }

  process.exit(errs.length ? 1 : 0);
})();
