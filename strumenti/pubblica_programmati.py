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
e non cambia etichette né file.

Solo libreria standard.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pubblica  # noqa: E402  (lo stesso file che pubblica dal PC)

ARCHIVIO = os.environ["GITHUB_REPOSITORY"]
PROPRIETARIA = os.environ["GITHUB_REPOSITORY_OWNER"]
VERSIONE = os.environ["GITHUB_SHA"]
PROVA = os.environ.get("PROVA") == "1"
AUTOMATISMO = "github-actions[bot]"
FINESTRA_ORE = 2
PROMEMORIA_ORE = 3
FERMI = {"in-pubblicazione", "pubblicato", "errore", "scaduto"}


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


def pubblica_uno(scheda: dict, nome: str, cartella: Path, dati: dict) -> None:
    numero = scheda["number"]
    indirizzo = f"https://raw.githubusercontent.com/{ARCHIVIO}/{VERSIONE}/coda/{nome}/post.jpg"
    with urllib.request.urlopen(urllib.request.Request(indirizzo, headers={"User-Agent": "curl/8"}), timeout=30) as r:
        if hashlib.sha256(r.read()).hexdigest() != dati["sha256_jpeg"]:
            raise pubblica.ErroreInstagram("l'immagine all'indirizzo pubblico non è quella approvata")
    metti(numero, "in-pubblicazione")
    esito = pubblica.pubblica(cartella, indirizzo, davvero=not PROVA)
    if PROVA:
        print(f"   PROVA riuscita per {nome}: Instagram ha scaricato e preparato l'immagine, niente è stato pubblicato")
        return
    Path("pubblicati").mkdir(exist_ok=True)
    Path("pubblicati", f"{nome}.json").write_text(json.dumps(esito, ensure_ascii=False, indent=2), encoding="utf-8")
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


def giro_dei_post(adesso: datetime) -> None:
    for scheda in github("GET", "/issues?state=open&labels=coda&per_page=100") or []:
        numero, nomi = scheda["number"], etichette(scheda)
        segno = re.search(r"<!-- pacchetto:(\S+) impronta:(\S+) uscita:(\S+) -->", scheda.get("body") or "")
        if scheda["user"]["login"] != AUTOMATISMO or nomi & FERMI:
            continue
        if not segno:
            print(f"   scheda {numero}: non trovo il segno del pacchetto nel testo della scheda, la salto")
            continue
        nome, impronta = segno.group(1), segno.group(2)
        if nome.startswith("zz-prova") and not PROVA:
            print(f"   scheda {numero}: {nome} è un pacchetto di prova, in un giro vero non esce mai")
            continue
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
                               "Rispondi a questa email con `approva`, `rifare: nota` oppure `scarta`. Senza risposta non esce.")
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
    adesso = datetime.now(timezone.utc)
    print(f"Giro del {adesso:%d/%m/%Y %H:%M} (ora universale){' · PROVA, non pubblico niente' if PROVA else ''}")
    for nome, colore in (("scaduto", "5C6A62"), ("promemoria", "B98900")):
        github("POST", "/labels", {"name": nome, "color": colore})
    giro_dei_post(adesso)
    giro_del_permesso()
    return 0


if __name__ == "__main__":
    sys.exit(main())
