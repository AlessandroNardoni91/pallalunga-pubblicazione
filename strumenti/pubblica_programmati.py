"""Gira sui computer di GitHub ogni 15 minuti circa. Pubblica su Instagram i post APPROVATI la cui ora
è arrivata, manda i promemoria e avvisa quando il permesso di Instagram sta per scadere.

Un post esce solo se TUTTE queste cose sono vere:
  - la sua scheda è aperta, aperta dall'automatismo, con le etichette "coda" e "approvato";
  - non ha le etichette "in-pubblicazione", "pubblicato", "errore" o "scaduto";
  - l'approvazione è stata data proprio a QUESTA versione del post (l'impronta scritta nella scheda
    coincide con quella del pacchetto in coda/);
  - l'ora di uscita è passata da meno di FINESTRA_ORE ore (oltre, il post è "scaduto": non esce in ritardo);
  - non esiste già pubblicati/<nome>.json e sulla pagina non c'è già un post con la stessa didascalia
    (quest'ultimo controllo lo fa pubblica.py).
Prima di dare l'ordine a Instagram mette l'etichetta "in-pubblicazione": se qualcosa si interrompe a
metà, il post NON viene ritentato da solo. In caso di errore: etichetta "errore" e avviso per email.

Con PROVA=1 fa tutto tranne l'ordine di pubblicare (Instagram scarica e prepara l'immagine e ci si ferma)
e non cambia etichette né file. In prova considera SOLO i pacchetti "zz-prova", così una prova non tocca
mai i post veri.

L'attesa (27/09/2026). GitHub non fa 96 giri al giorno ma 6-7, uno ogni 3-5 ore: i post uscivano fino a
4 ore dopo l'ora fissata (stipendi: fissato alle 18:30, uscito alle 20:38). Per questo, se un post
approvato deve uscire entro ATTESA_MAX dall'inizio del giro, il giro resta acceso, aspetta l'ora esatta e
lo pubblica. Prima di pubblicare riscarica l'archivio e rilegge la scheda: se nel frattempo il post è
cambiato o l'approvazione è stata tolta, non esce.

Le riserve (20/10/2026, decisione di Alena: «il giorno che non abbiamo post o mi dimentico di approvare, esci
con la riserva»). Una riserva è un post approvato SENZA data, con "riserva_fino" nel suo post.json. Ogni giorno
ci sono due orari fissi (ORARI, ora italiana; si possono cambiare con il file orari.json nella radice
dell'archivio). Se a uno di questi orari nessun post è approvato, in pubblicazione o pubblicato, esce da sola la
riserva valida più vecchia, così com'è (l'approvazione vale perché grafica e testo sono quelli approvati). Il
post di quell'orario che non era approvato prende l'etichetta "saltato": non esce più in ritardo, va rimandato
con una data nuova. La riserva entra solo entro FINESTRA_RISERVA dall'orario, mai a notte fonda.

Solo libreria standard.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pubblica  # noqa: E402  (lo stesso file che pubblica dal PC)

ARCHIVIO = os.environ["GITHUB_REPOSITORY"]
PROPRIETARIA = os.environ["GITHUB_REPOSITORY_OWNER"]
PROVA = os.environ.get("PROVA") == "1"
AUTOMATISMO = "github-actions[bot]"
FINESTRA_ORE = 6  # GitHub fa 7-8 giri al giorno, non 96: buco più lungo visto 5 ore e 2 minuti (misura 17-21/09/2026)
# quanto un giro può restare acceso ad aspettare: GitHub ferma un giro dopo 6 ore, il limite nel file
# del programma di GitHub è 5 ore e 55 minuti, qui si lascia margine per pubblicare dopo l'attesa
ATTESA_MAX = timedelta(hours=5, minutes=20)
PROMEMORIA_ORE = 3
FERMI = {"in-pubblicazione", "pubblicato", "errore", "scaduto", "saltato"}
ORARI = ["12:30", "18:30"]  # gli orari fissi di ogni giorno, ora italiana (orari.json li può cambiare)
FINESTRA_RISERVA = timedelta(hours=3)  # oltre, la riserva non entra più per quell'orario
COPRONO = {"approvato", "in-pubblicazione", "pubblicato"}  # un post con una di queste etichette occupa il suo orario


def fuso_italiano(giorno: datetime) -> timezone:
    """Ora legale dall'ultima domenica di marzo (02:00) all'ultima domenica di ottobre (03:00): +2, se no +1.
    La stessa regola di prepara.py sul PC."""
    def ultima_domenica(mese: int) -> datetime:
        d = datetime(giorno.year, mese, 31)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    inizio, fine = ultima_domenica(3).replace(hour=2), ultima_domenica(10).replace(hour=3)
    return timezone(timedelta(hours=2 if inizio <= giorno.replace(tzinfo=None) < fine else 1))


def orari_del_giorno(giorno: date) -> list[datetime]:
    """Gli orari fissi di un giorno, in ora universale. orari.json può avere "orari" (per esempio ["12:30", "18:30"])
    e "senza_riserva" (per esempio ["2026-10-16 12:30"]: quell'orario resta vuoto apposta)."""
    impostazioni = json.loads(Path("orari.json").read_text(encoding="utf-8")) if Path("orari.json").exists() else {}
    orari = [os.environ["ORARIO_PROVA"]] if PROVA and os.environ.get("ORARIO_PROVA") else impostazioni.get("orari", ORARI)
    esclusi = set(impostazioni.get("senza_riserva", []))
    risultato = []
    for hhmm in orari:
        if f"{giorno:%Y-%m-%d} {hhmm}" in esclusi:
            continue
        locale = datetime.strptime(f"{giorno:%Y-%m-%d} {hhmm}", "%Y-%m-%d %H:%M")
        risultato.append(locale.replace(tzinfo=fuso_italiano(locale)).astimezone(timezone.utc))
    return risultato


def github(metodo: str, percorso: str, dati: dict | None = None):
    richiesta = urllib.request.Request(f"https://api.github.com/repos/{ARCHIVIO}{percorso}", method=metodo,
                                       data=json.dumps(dati).encode("utf-8") if dati is not None else None,
                                       headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json",
                                                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pallalunga"})
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            corpo = risposta.read()
            return json.loads(corpo) if corpo else None
    except urllib.error.HTTPError as errore:
        if errore.code in (404, 422) and (metodo == "DELETE" or percorso == "/labels"):
            return None
        raise SystemExit(f"GitHub ha risposto {errore.code} a {metodo} {percorso}")


def etichette(scheda: dict) -> set:
    return {e["name"] for e in scheda.get("labels", [])}


def metti(numero: int, *nomi: str) -> None:
    if not PROVA:
        github("POST", f"/issues/{numero}/labels", {"labels": list(nomi)})


def togli(numero: int, *nomi: str) -> None:
    if not PROVA:
        for nome in nomi:
            github("DELETE", f"/issues/{numero}/labels/{nome}")


def scrivi(numero: int, testo: str) -> None:
    print(f"   scheda {numero}: {testo}")
    if not PROVA:
        github("POST", f"/issues/{numero}/comments", {"body": testo})


def git(*argomenti: str) -> None:
    subprocess.run(["git", *argomenti], check=True)


def versione() -> str:
    """La versione dell'archivio in uso adesso: dopo un'attesa è cambiata, perché l'archivio si riscarica."""
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def pubblica_uno(scheda: dict, nome: str, cartella: Path, dati: dict, extra: dict | None = None) -> None:
    numero = scheda["number"]
    base = f"https://raw.githubusercontent.com/{ARCHIVIO}/{versione()}/coda/{nome}"
    indirizzo = f"{base}/post.jpg"
    # un carosello (07/10/2026) ha anche post-2.jpg, post-3.jpg...: si controllano tutte le pagine
    for p in dati.get("pagine") or [{"file": "post.jpg", "sha256": dati["sha256_jpeg"]}]:
        with urllib.request.urlopen(urllib.request.Request(f"{base}/{p['file']}", headers={"User-Agent": "curl/8"}), timeout=30) as r:
            if hashlib.sha256(r.read()).hexdigest() != p["sha256"]:
                raise pubblica.ErroreInstagram(f"l'immagine {p['file']} all'indirizzo pubblico non è quella approvata")
    metti(numero, "in-pubblicazione")
    esito = pubblica.pubblica(cartella, indirizzo, davvero=not PROVA)
    if PROVA:
        print(f"   PROVA riuscita per {nome}: Instagram ha scaricato e preparato l'immagine, niente è stato pubblicato")
        return
    Path("pubblicati").mkdir(exist_ok=True)
    Path("pubblicati", f"{nome}.json").write_text(json.dumps({**esito, **(extra or {})}, ensure_ascii=False, indent=2), encoding="utf-8")
    git("rm", "-r", "-q", "--cached", f"coda/{nome}")
    subprocess.run(["rm", "-rf", f"coda/{nome}"], check=True)
    git("add", "pubblicati")
    git("-c", "user.name=pallalungaepedalare", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-q", "-m", f"Pubblicato: {nome}")
    git("pull", "-q", "--rebase", "origin", "main")
    git("push", "-q", "origin", "HEAD:main")
    avviso = "" if esito.get("didascalia_uguale") else "\n\nATTENZIONE: la didascalia riletta da Instagram non è identica a quella approvata, controllala."
    scrivi(numero, f"@{PROPRIETARIA} pubblicato: {esito['indirizzo']}{avviso}")
    metti(numero, "pubblicato")
    togli(numero, "in-pubblicazione", "approvato")
    github("PATCH", f"/issues/{numero}", {"state": "closed", "state_reason": "completed"})


def giro_dei_post(adesso: datetime) -> datetime | None:
    """Pubblica quello che è ora di pubblicare; restituisce l'ora del prossimo post approvato che deve ancora uscire."""
    prossime = []
    for scheda in github("GET", "/issues?state=open&labels=coda&per_page=100") or []:
        numero, nomi = scheda["number"], etichette(scheda)
        segno = re.search(r"<!-- pacchetto:(\S+) impronta:(\S+) uscita:(\S+) -->", scheda.get("body") or "")
        if scheda["user"]["login"] != AUTOMATISMO or nomi & FERMI:
            continue
        if not segno:
            print(f"   scheda {numero}: non trovo il segno del pacchetto nel testo della scheda, la salto")
            continue
        nome, impronta = segno.group(1), segno.group(2)
        if PROVA != nome.startswith("zz-prova"):
            continue  # in prova solo i pacchetti di prova, nei giri veri mai
        cartella = Path("coda", nome)
        if not (cartella / "post.json").exists() or Path("pubblicati", f"{nome}.json").exists():
            continue
        dati = json.loads((cartella / "post.json").read_text(encoding="utf-8"))
        if not dati.get("esce_il_utc"):
            continue
        uscita = datetime.fromisoformat(dati["esce_il_utc"])
        ora_locale = datetime.fromisoformat(dati["esce_il"]).strftime("%d/%m alle %H:%M")
        if adesso >= uscita + timedelta(hours=FINESTRA_ORE):
            scrivi(numero, f"@{PROPRIETARIA} l'ora di uscita ({ora_locale}) è passata da più di {FINESTRA_ORE} ore "
                           + ("e il post era approvato, ma non è uscito in tempo" if "approvato" in nomi else "senza la tua approvazione")
                           + ": NON lo pubblico in ritardo. Per farlo uscire va rimandato in revisione con una data nuova.")
            metti(numero, "scaduto")
        elif "approvato" not in nomi:
            if uscita - timedelta(hours=PROMEMORIA_ORE) <= adesso and "promemoria" not in nomi and not nomi & {"da-rifare", "scartato"}:
                scrivi(numero, f"@{PROPRIETARIA} promemoria: questo post deve uscire il {ora_locale} e non ha ancora la tua risposta. "
                               "Rispondi a questa email con `approva`, `rifare: nota` oppure `scarta`. Senza risposta non esce: "
                               "all'ora fissata, al suo posto, esce una riserva approvata (se ce n'è una).")
                metti(numero, "promemoria")
        elif adesso >= uscita:
            if impronta != f"{dati.get('sha256_jpeg', '')[:16]}-{dati.get('impronta_approvata', '')}" or segno.group(3) != dati["esce_il_utc"]:
                print(f"   scheda {numero}: approvazione data a una versione diversa, non pubblico")
                continue
            print(f"   scheda {numero}: è ora di pubblicare {nome}")
            try:
                pubblica_uno(scheda, nome, cartella, dati)
            except (pubblica.ErroreInstagram, subprocess.CalledProcessError, urllib.error.URLError) as errore:
                metti(numero, "errore")
                scrivi(numero, f"@{PROPRIETARIA} la pubblicazione NON è andata a buon fine e non verrà ritentata da sola. "
                               f"Prima di fare altro va controllato sulla pagina Instagram se il post è uscito. Errore: {errore}")
        else:
            print(f"   scheda {numero}: {nome} approvato, esce il {ora_locale}")
            prossime.append(uscita)
    prossime += giro_delle_riserve(datetime.now(timezone.utc))
    return min(prossime, default=None)


def segno_di(scheda: dict):
    return re.search(r"<!-- pacchetto:(\S+) impronta:(\S+) uscita:(\S+) -->", scheda.get("body") or "")


def riserve_valide(oggi: date) -> list[tuple]:
    """Le riserve che possono uscire adesso: scheda aperta dell'automatismo con "coda" e "approvato", nessuna
    etichetta di fermo, approvazione data a questa versione, riserva_fino non passato. La più vecchia per prima."""
    valide = []
    for scheda in sorted(github("GET", "/issues?state=open&labels=coda&per_page=100") or [], key=lambda s: s["number"]):
        nomi, segno = etichette(scheda), segno_di(scheda)
        if scheda["user"]["login"] != AUTOMATISMO or nomi & FERMI or "approvato" not in nomi or not segno:
            continue
        nome = segno.group(1)
        if PROVA != nome.startswith("zz-prova"):
            continue
        cartella = Path("coda", nome)
        if not (cartella / "post.json").exists() or Path("pubblicati", f"{nome}.json").exists():
            continue
        dati = json.loads((cartella / "post.json").read_text(encoding="utf-8"))
        if not dati.get("riserva_fino") or dati.get("esce_il_utc"):
            continue
        if segno.group(2) != f"{dati.get('sha256_jpeg', '')[:16]}-{dati.get('impronta_approvata', '')}":
            print(f"   scheda {scheda['number']}: riserva {nome} approvata in una versione diversa, non la uso")
            continue
        if date.fromisoformat(dati["riserva_fino"]) < oggi:
            if "riserva-scaduta" not in nomi:
                scrivi(scheda["number"], f"@{PROPRIETARIA} questa riserva valeva fino al {date.fromisoformat(dati['riserva_fino']):%d/%m/%Y}: "
                                         "non la uso più. Il redattore deve ricontrollarla e rimandarla con una scadenza nuova, oppure prepararne un'altra.")
                metti(scheda["number"], "riserva-scaduta")
            continue
        valide.append((scheda, nome, cartella, dati))
    return valide


def orari_coperti(adesso: datetime) -> set[str]:
    """Gli orari (ora universale, come nel segno della scheda) già occupati da un post approvato, in pubblicazione
    o pubblicato, più quelli già coperti da una riserva (scritti in pubblicati/<nome>.json)."""
    da = (adesso - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    coperti = set()
    for scheda in github("GET", f"/issues?state=all&labels=coda&since={da}&per_page=100") or []:
        segno = segno_di(scheda)
        if segno and etichette(scheda) & COPRONO and PROVA == segno.group(1).startswith("zz-prova"):
            coperti.add(segno.group(3))
    for file in Path("pubblicati").glob("*.json") if Path("pubblicati").exists() else []:
        orario = json.loads(file.read_text(encoding="utf-8")).get("orario_riserva")
        if orario:
            coperti.add(orario)
    return coperti


def giro_delle_riserve(adesso: datetime) -> list[datetime]:
    """Copre con una riserva gli orari fissi rimasti senza post approvato; restituisce gli orari futuri vuoti da
    aspettare (solo se c'è una riserva pronta), così il giro resta acceso e la riserva esce all'ora giusta."""
    oggi = adesso.astimezone(fuso_italiano(adesso)).date()
    orari = [o for g in (oggi - timedelta(days=1), oggi, oggi + timedelta(days=1)) for o in orari_del_giorno(g)]
    if not any(o <= adesso + ATTESA_MAX and adesso < o + FINESTRA_RISERVA for o in orari):
        return []
    coperti = orari_coperti(adesso)
    vuoti = [o for o in orari if o.isoformat(timespec="minutes") not in coperti]
    if not vuoti:
        return []
    riserve = riserve_valide(oggi)
    da_aspettare = []
    for orario in vuoti:
        ora_locale = orario.astimezone(fuso_italiano(orario)).strftime("%d/%m alle %H:%M")
        if orario > adesso:
            if riserve and orario - adesso <= ATTESA_MAX:
                da_aspettare.append(orario)
            continue
        if adesso >= orario + FINESTRA_RISERVA:
            continue
        if not riserve:
            print(f"   orario del {ora_locale}: nessun post approvato e NESSUNA RISERVA pronta, resta vuoto")
            continue
        scheda, nome, cartella, dati = riserve.pop(0)
        chiave = orario.isoformat(timespec="minutes")
        print(f"   orario del {ora_locale}: nessun post approvato, esce la riserva {nome}")
        saltati = []
        for altra in github("GET", "/issues?state=open&labels=coda&per_page=100") or []:
            segno = segno_di(altra)
            if segno and segno.group(3) == chiave and not etichette(altra) & (COPRONO | FERMI):
                saltati.append(altra)
        try:
            pubblica_uno(scheda, nome, cartella, dati, extra={"orario_riserva": chiave})
        except (pubblica.ErroreInstagram, subprocess.CalledProcessError, urllib.error.URLError) as errore:
            metti(scheda["number"], "errore")
            scrivi(scheda["number"], f"@{PROPRIETARIA} la riserva doveva uscire il {ora_locale} ma la pubblicazione NON è "
                                     f"andata a buon fine e non verrà ritentata da sola. Controlla sulla pagina Instagram. Errore: {errore}")
            continue
        motivo = (f"il post in programma (scheda {', '.join('#' + str(s['number']) for s in saltati)}) non era approvato"
                  if saltati else "non c'era nessun post in programma")
        scrivi(scheda["number"], f"@{PROPRIETARIA} questa riserva è uscita il {ora_locale} perché {motivo}. "
                                 f"Riserve ancora pronte: {len(riserve)}" + (" (meno di due: il redattore ne prepara una nuova)." if len(riserve) < 2 else "."))
        for altra in saltati:
            scrivi(altra["number"], f"@{PROPRIETARIA} il {ora_locale} questo post non era approvato: al suo posto è uscita la riserva "
                                    f"«{dati.get('titolo', nome)}». Non esce più in ritardo: il redattore lo rimanda con una data nuova.")
            metti(altra["number"], "saltato")
    return da_aspettare


def giro_del_permesso() -> None:
    file = Path("permesso.json")
    if not file.exists():
        return
    mancano = (date.fromisoformat(json.loads(file.read_text(encoding="utf-8"))["scade_il"]) - date.today()).days
    print(f"   permesso di Instagram: mancano {mancano} giorni alla scadenza")
    if mancano > 15 or PROVA:
        return
    aperte = github("GET", "/issues?state=open&labels=permesso&per_page=10") or []
    if not aperte:
        github("POST", "/labels", {"name": "permesso", "color": "7f1d1d"})
        github("POST", "/issues", {"title": f"Il permesso di Instagram scade fra {mancano} giorni", "labels": ["permesso"],
                                   "body": f"@{PROPRIETARIA} il permesso con cui si pubblica su Instagram scade fra {mancano} giorni. Va rinnovato dal PC, "
                                           "dalla cartella del progetto, con il comando:\n\n`.venv\\Scripts\\python.exe pubblicazione\\rinnova_permesso.py`\n\n"
                                           "Un permesso scaduto non si può più rinnovare: andrebbe rifatta a mano tutta la procedura. Dopo il rinnovo chiudi questa scheda."})
    elif mancano <= 5 and "ultimo-avviso" not in etichette(aperte[0]):
        github("POST", "/labels", {"name": "ultimo-avviso", "color": "C8352B"})
        github("POST", f"/issues/{aperte[0]['number']}/comments", {"body": f"@{PROPRIETARIA} ultimo avviso: mancano {mancano} giorni alla scadenza del permesso."})
        github("POST", f"/issues/{aperte[0]['number']}/labels", {"labels": ["ultimo-avviso"]})


def main() -> int:
    inizio = datetime.now(timezone.utc)
    print(f"Giro del {inizio:%d/%m/%Y %H:%M} (ora universale){' · PROVA, non pubblico niente' if PROVA else ''}")
    for nome, colore in (("scaduto", "5C6A62"), ("promemoria", "B98900")):
        github("POST", "/labels", {"name": nome, "color": colore})
    giro_del_permesso()
    while True:
        prossima = giro_dei_post(datetime.now(timezone.utc))
        if prossima is None or prossima - inizio > ATTESA_MAX:
            break
        attesa = (prossima - datetime.now(timezone.utc)).total_seconds()
        print(f"Aspetto fino alle {prossima:%H:%M} (ora universale), cioè {max(0, attesa) / 60:.0f} minuti")
        sys.stdout.flush()
        if attesa > 0:
            time.sleep(attesa + 5)
        # dati freschi prima di pubblicare: una versione nuova del post o una decisione cambiata nel frattempo
        git("pull", "-q", "--rebase", "origin", "main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
