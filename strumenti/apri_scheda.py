"""Gira sui computer di GitHub a ogni caricamento in coda/. Per ogni pacchetto coda/<nome>/ apre una
"scheda" (issue) con immagine, didascalia e istruzioni: GitHub manda l'email alla proprietaria, che
risponde all'email con approva / rifare: nota / scarta.

Se il pacchetto cambia dopo che la scheda è stata aperta (impronta diversa), la scheda viene aggiornata,
l'eventuale approvazione viene tolta e parte un nuovo avviso con la versione nuova intera (grafica e
didascalia), così l'email basta per decidere.

Solo libreria standard. Usa il permesso temporaneo GITHUB_TOKEN che GitHub dà al programma: vale solo
per questo archivio e solo per la durata del giro.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ARCHIVIO = os.environ["GITHUB_REPOSITORY"]
PROPRIETARIA = os.environ["GITHUB_REPOSITORY_OWNER"]
VERSIONE = os.environ["GITHUB_SHA"]
API = f"https://api.github.com/repos/{ARCHIVIO}"
ETICHETTE = {"coda": "5C6A62", "approvato": "1E8A4C", "da-rifare": "B98900", "scartato": "C8352B",
             "in-pubblicazione": "E07B00", "pubblicato": "1B4F9C", "errore": "7f1d1d"}


def github(metodo: str, percorso: str, dati: dict | None = None):
    richiesta = urllib.request.Request(API + percorso, method=metodo, data=json.dumps(dati).encode("utf-8") if dati is not None else None,
                                       headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json",
                                                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pallalunga"})
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            corpo = risposta.read()
            return json.loads(corpo) if corpo else None
    except urllib.error.HTTPError as errore:
        if errore.code == 422 and percorso == "/labels":  # l'etichetta esiste già
            return None
        raise SystemExit(f"GitHub ha risposto {errore.code} a {metodo} {percorso}: {errore.read().decode('utf-8', 'replace')[:400]}")


def testo_scheda(nome: str, d: dict) -> str:
    base = f"https://raw.githubusercontent.com/{ARCHIVIO}/{VERSIONE}/coda/{nome}"
    # un carosello (07/10/2026) mostra tutte le pagine, una sotto l'altra, così si giudica dall'email
    pagine = d.get("pagine") or [{"file": "post.jpg"}]
    if len(pagine) > 1:
        immagini = f"**Carosello di {len(pagine)} pagine**, nell'ordine in cui si sfogliano:\n\n" + "\n\n".join(
            f"Pagina {n}\n\n![Pagina {n}]({base}/{p['file']})" for n, p in enumerate(pagine, 1))
    else:
        immagini = f"![Il post]({base}/post.jpg)"
    didascalia = "\n".join("> " + riga for riga in d["didascalia"].splitlines())
    uscita = "non ancora fissata"
    if d.get("esce_il"):  # "2026-09-30T18:30+02:00" -> "mercoledì 30/09/2026 alle 18:30 (ora italiana)"
        quando = datetime.fromisoformat(d["esce_il"])
        giorno = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"][quando.weekday()]
        uscita = f"{giorno} {quando:%d/%m/%Y} alle {quando:%H:%M} (ora italiana)"
    controlli = ""
    if d.get("controlli_fatti"):
        controlli = "\n**Controlli automatici superati:** " + "; ".join(d["controlli_fatti"]) + ".\n"
    if d.get("avvisi"):
        controlli += "\n**Avvisi del controllo automatico (non bloccano, ma guardali):**\n" + "\n".join("- " + a for a in d["avvisi"]) + "\n"
    return f"""@{PROPRIETARIA} c'è un post da approvare: **{d.get('titolo', nome)}**

**Uscita prevista:** {uscita}
{controlli}
{immagini}

### Didascalia, esattamente come uscirebbe

{didascalia}

---

### Come rispondere

Rispondi a questa email scrivendo **nella prima riga** una di queste tre cose:

- `approva`
- `rifare: ` seguito da cosa va cambiato
- `scarta`

Maiuscole o minuscole è uguale: `Approva` vale come `approva`.

Questo archivio è pubblico: la nota che scrivi si può leggere da fuori. Non inoltrare questa email a nessuno: chi la riceve potrebbe rispondere al posto tuo.

<!-- pacchetto:{nome} impronta:{d.get('sha256_jpeg', '')[:16]}-{d.get('impronta_approvata', '')} uscita:{d.get('esce_il_utc') or 'nessuna'} -->
"""


def main() -> None:
    for nome, colore in ETICHETTE.items():
        github("POST", "/labels", {"name": nome, "color": colore})
    aperte = github("GET", "/issues?state=open&labels=coda&per_page=100") or []
    for file in sorted(Path("coda").glob("*/post.json")):
        nome = file.parent.name
        d = json.loads(file.read_text(encoding="utf-8"))
        corpo = testo_scheda(nome, d)
        segno = re.search(r"<!-- pacchetto:.*? -->", corpo).group(0)
        scheda = next((s for s in aperte if f"<!-- pacchetto:{nome} " in (s.get("body") or "")), None)
        if scheda is None:
            nuova = github("POST", "/issues", {"title": f"Da approvare: {d.get('titolo', nome)}", "body": corpo,
                                                "labels": ["coda"]})  # niente "assegnata a": farebbe partire una seconda email inutile
            print(f"scheda aperta per {nome}: numero {nuova['number']}")
        elif segno not in (scheda.get("body") or ""):
            numero = scheda["number"]
            github("PATCH", f"/issues/{numero}", {"body": corpo})
            for etichetta in ("approvato", "da-rifare", "scartato", "promemoria", "scaduto"):
                if any(e["name"] == etichetta for e in scheda.get("labels", [])):
                    github("DELETE", f"/issues/{numero}/labels/{etichetta}")
            # L'email nasce dal commento: ci va la versione nuova intera (grafica, didascalia, uscita),
            # così si approva dal telefono senza aprire la scheda (richiesta di Alena del 05/10/2026).
            resto = re.sub(r"\n?<!-- pacchetto:.*? -->\n?", "\n", corpo.split("\n", 1)[1])
            github("POST", f"/issues/{numero}/comments", {"body": f"@{PROPRIETARIA} **versione nuova** del post **{d.get('titolo', nome)}**. "
                                                                  "La decisione di prima non vale più: rispondi di nuovo a questa email.\n" + resto})
            print(f"scheda {numero} aggiornata per {nome}")
        else:
            print(f"{nome}: scheda già aperta e invariata")


if __name__ == "__main__":
    main()
