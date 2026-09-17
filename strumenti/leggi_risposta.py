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

Solo libreria standard.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

AUTOMATISMO = "github-actions[bot]"
DECISIONI = {"approva": "approvato", "rifare": "da-rifare", "scarta": "scartato"}


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

    righe = [r.strip() for r in (commento.get("body") or "").replace("\r", "").split("\n")]
    righe = [r for r in righe if r and not r.startswith(">")]
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
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            corpo = risposta.read()
            return json.loads(corpo) if corpo else None
    except urllib.error.HTTPError as errore:
        if errore.code == 404 and metodo == "DELETE":  # l'etichetta non c'era
            return None
        raise SystemExit(f"GitHub ha risposto {errore.code} a {metodo} {percorso}")


def main() -> int:
    evento = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    esito = decidi(evento, os.environ["GITHUB_REPOSITORY_OWNER"])
    if "ignora" in esito:
        print("Ignorato:", esito["ignora"])
        return 0
    numero = evento["issue"]["number"]
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
    print("Registrato:", esito["decisione"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
