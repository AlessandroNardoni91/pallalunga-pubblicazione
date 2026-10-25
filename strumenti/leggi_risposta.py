"""Gira sui computer di GitHub a ogni commento su una scheda. Legge la risposta della proprietaria
(scritta rispondendo all'email) e registra la decisione con un'etichetta sulla scheda.

CHI PUÒ FARE COSA. L'archivio è pubblico, quindi chiunque abbia un account GitHub può commentare.
Una risposta viene ascoltata solo se TUTTE queste cose sono vere:
  - chi scrive è la proprietaria dell'archivio (stesso nome utente E ruolo OWNER);
  - la scheda è stata aperta dall'automatismo (github-actions[bot]), non da una persona;
  - la scheda ha l'etichetta "coda" ed è ancora aperta;
  - non è una richiesta di modifica del codice (pull request).
In tutti gli altri casi il programma non fa e non scrive niente.

Il testo della risposta non viene mai eseguito né passato a un comando: si legge dal file dell'evento
che GitHub prepara, e si confronta con tre parole.

Con «scarta» il post viene anche tolto dalla cartella coda/ (dal 16/10/2026). Prima restava lì: al primo
caricamento successivo apri_scheda.py gli riapriva una scheda nuova (la riserva under 23 scartata il 16/10
è tornata come scheda 52) e coda_redazione.py contava come pieno il giorno di un post che non esce.

Solo libreria standard.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AUTOMATISMO = "github-actions[bot]"
DECISIONI = {"approva": "approvato", "rifare": "da-rifare", "scarta": "scartato"}


def pulisci(testo: str) -> list[str]:
    """Le righe scritte davvero da lei. I programmi di posta (Gmail compreso) incollano sotto la risposta
    l'email citata, preceduta da un'intestazione tipo «Il gio 17 set 2026, 18:59 ... ha scritto:»:
    si butta via tutto dalla citazione in giù, intestazione compresa."""
    righe = [r.strip() for r in testo.replace("\r", "").split("\n")]
    citazione = next((i for i, r in enumerate(righe) if r.startswith(">")), None)
    if citazione is not None:
        righe = righe[:citazione]
        while righe and not righe[-1]:
            righe.pop()
        if righe and righe[-1].endswith(":"):  # l'intestazione della citazione: l'ultimo paragrafo, che finisce con i due punti
            while righe and righe[-1]:
                righe.pop()
    return [r for r in righe if r]


def decidi(evento: dict, proprietaria: str) -> dict:
    """Funzione pura, provabile sul PC con eventi finti. Restituisce {'ignora': motivo} oppure
    {'decisione': 'approva'|'rifare'|'scarta'|'non_capito', 'nota': str}."""
    commento, scheda = evento.get("comment") or {}, evento.get("issue") or {}
    autore = (commento.get("user") or {}).get("login", "")
    if autore == AUTOMATISMO:
        return {"ignora": "commento dell'automatismo"}
    if autore != proprietaria or commento.get("author_association") != "OWNER":
        return {"ignora": f"commento di {autore or 'sconosciuto'}, che non è la proprietaria"}
    if scheda.get("pull_request"):
        return {"ignora": "è una pull request, non una scheda"}
    if (scheda.get("user") or {}).get("login") != AUTOMATISMO:
        return {"ignora": "scheda non aperta dall'automatismo"}
    if scheda.get("state") != "open" or "coda" not in [e.get("name") for e in scheda.get("labels", [])]:
        return {"ignora": "scheda chiusa o senza etichetta coda"}
    if "pubblicato" in [e.get("name") for e in scheda.get("labels", [])] or "in-pubblicazione" in [e.get("name") for e in scheda.get("labels", [])]:
        return {"ignora": "post già pubblicato o in pubblicazione"}

    righe = pulisci(commento.get("body") or "")
    if not righe:
        return {"decisione": "non_capito", "nota": ""}
    prima = righe[0].lower().strip(" .!\"'«»*`")
    if prima == "approva":
        return {"decisione": "approva", "nota": ""}
    if prima == "scarta":
        return {"decisione": "scarta", "nota": ""}
    m = re.match(r"^rifare\s*[:,]?\s*(.*)$", righe[0].strip(" \"'«»*`"), re.I | re.S)
    if m:
        nota = " ".join([m.group(1).strip()] + righe[1:]).strip()[:1000]
        return {"decisione": "rifare", "nota": nota} if nota else {"decisione": "non_capito", "nota": "rifare senza dire cosa"}
    return {"decisione": "non_capito", "nota": ""}


def github(metodo: str, percorso: str, dati: dict | None = None):
    richiesta = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{percorso}", method=metodo,
                                       data=json.dumps(dati).encode("utf-8") if dati is not None else None,
                                       headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json",
                                                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pallalunga"})
    # 25/10/2026: un errore del server di GitHub (500 e simili, un suo guasto di un momento) si ritenta due volte
    for tentativo in range(3):
        try:
            with urllib.request.urlopen(richiesta, timeout=30) as risposta:
                corpo = risposta.read()
                return json.loads(corpo) if corpo else None
        except urllib.error.HTTPError as errore:
            if errore.code == 404 and metodo == "DELETE":  # l'etichetta non c'era
                return None
            if errore.code >= 500 and tentativo < 2:
                print(f"GitHub ha risposto {errore.code} a {metodo} {percorso}: riprovo fra {15 * (tentativo + 1)} secondi")
                time.sleep(15 * (tentativo + 1))
                continue
            raise SystemExit(f"GitHub ha risposto {errore.code} a {metodo} {percorso}")


def git(*argomenti: str) -> None:
    subprocess.run(["git", *argomenti], check=True)


def togli_dalla_coda(nome: str) -> str:
    """Toglie coda/<nome> dall'archivio con un commit. Restituisce "" se è andata, altrimenti il motivo."""
    if not re.fullmatch(r"[\w.-]+", nome) or not Path("coda", nome).is_dir():
        return f"non trovo la cartella coda/{nome}"
    try:
        git("rm", "-r", "-q", f"coda/{nome}")
        git("-c", "user.name=pallalungaepedalare", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-q", "-m", f"Scartato: {nome}")
        for tentativo in range(3):  # un altro automatismo può aver caricato nel frattempo
            git("pull", "-q", "--rebase", "origin", "main")
            if subprocess.run(["git", "push", "-q", "origin", "HEAD:main"]).returncode == 0:
                return ""
        return "il caricamento è stato rifiutato tre volte"
    except subprocess.CalledProcessError as errore:
        return f"git si è fermato ({errore})"


def main() -> int:
    evento = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    esito = decidi(evento, os.environ["GITHUB_REPOSITORY_OWNER"])
    if "ignora" in esito:
        print("Ignorato:", esito["ignora"])
        return 0
    numero = evento["issue"]["number"]
    # Il commento di lei si cancella appena letto: la posta ci incolla sotto l'email citata, con dentro i suoi
    # link personali di GitHub, e l'archivio è pubblico. La decisione resta scritta nella conferma qui sotto.
    github("DELETE", f"/issues/comments/{evento['comment']['id']}")
    impronta = re.search(r"impronta:([\w-]+)", evento["issue"].get("body") or "")
    versione = f" (versione del post: `{impronta.group(1)}`)" if impronta else ""
    if esito["decisione"] == "non_capito":
        github("POST", f"/issues/{numero}/comments", {"body": "Non ho capito la risposta, quindi non ho registrato niente. Nella **prima riga** scrivi solo una di queste: "
                                                              "`approva` oppure `rifare: cosa va cambiato` oppure `scarta`."})
        return 0
    for etichetta in DECISIONI.values():
        github("DELETE", f"/issues/{numero}/labels/{etichetta}")
    github("POST", f"/issues/{numero}/labels", {"labels": [DECISIONI[esito["decisione"]]]})
    if esito["decisione"] == "approva":
        testo = f"Registrato: **approvato**{versione}. Se il post cambia dopo questo momento, l'approvazione decade e ti riscrivo."
    elif esito["decisione"] == "rifare":
        testo = f"Registrato: **da rifare**{versione}. Nota: «{esito['nota']}». Quando la versione nuova è pronta ti arriva un'altra email da questa scheda."
    else:
        testo = f"Registrato: **scartato**{versione}. Questo post non uscirà. Chiudo la scheda."
    github("POST", f"/issues/{numero}/comments", {"body": testo})
    if esito["decisione"] == "scarta":
        github("PATCH", f"/issues/{numero}", {"state": "closed", "state_reason": "not_planned"})
        pacchetto = re.search(r"<!-- pacchetto:(\S+) ", evento["issue"].get("body") or "")
        problema = togli_dalla_coda(pacchetto.group(1)) if pacchetto else "la scheda non dice quale pacchetto è"
        if problema:
            github("POST", f"/issues/{numero}/comments", {"body": f"Attenzione: il post è scartato e non uscirà, ma non sono riuscito a toglierlo dalla coda ({problema}). "
                                                                  "Va tolto a mano, se no al prossimo caricamento torna una scheda nuova."})
        print("Tolto dalla coda:", "no, " + problema if problema else "sì")
    print("Registrato:", esito["decisione"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
