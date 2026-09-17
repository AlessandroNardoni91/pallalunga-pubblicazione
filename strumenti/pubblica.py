"""Pubblica su Instagram un pacchetto preparato da prepara.py.

Tre modi, dal più innocuo:
  prova_a_secco(cartella)                    stampa che cosa verrebbe mandato, non manda niente
  pubblica(cartella, indirizzo, davvero=False)  PROVA GENERALE: fa scaricare e preparare l'immagine a
                                             Instagram (il "contenitore") e si ferma lì. Un contenitore
                                             non pubblicato non si vede da nessuna parte e scade da solo
                                             dopo 24 ore.
  pubblica(cartella, indirizzo, davvero=True)   pubblica sul serio.

Contro la doppia pubblicazione: prima dell'ordine di pubblicare scrive nel pacchetto il file
in-pubblicazione.json. Se quel file c'è già, non riparte MAI da solo: qualcuno deve guardare su
Instagram se il post è uscito (funzione cerca_su_instagram) e togliere il file a mano.

Usa solo la libreria standard di Python, perché lo stesso file dovrà girare identico sui
computer di GitHub (lotto 4 del piano).

La prova a secco fa a Instagram due sole domande, che non cambiano niente:
  1. "chi sono?"                          -> deve rispondere pallalungaepedalare
  2. "quanti post posso ancora pubblicare?" -> risponde solo se il permesso di PUBBLICARE c'è

Il permesso (token) si legge dalla variabile IG_ACCESS_TOKEN oppure dal file .env del
progetto. Non viene mai stampato, e gli indirizzi che lo contengono nemmeno.

Uso, dalla cartella del progetto:
    .venv\\Scripts\\python.exe pubblicazione\\pubblica.py pubblicazione\\pronti\\<pacchetto>
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://graph.instagram.com/v23.0"
UTENTE_ATTESO = "pallalungaepedalare"


class ErroreInstagram(Exception):
    pass


def leggi_permesso() -> str:
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    env = Path(__file__).resolve().parent.parent / ".env"
    if not token and env.exists():
        for riga in env.read_text(encoding="utf-8").splitlines():
            if riga.strip().startswith("IG_ACCESS_TOKEN="):
                token = riga.split("=", 1)[1].strip().strip('"').strip("'")
    if not token:
        raise ErroreInstagram("manca il permesso: IG_ACCESS_TOKEN non è né nelle variabili né in .env")
    return token


def chiedi(percorso: str, campi: dict, token: str) -> dict:
    """Una domanda in sola lettura (GET). In caso di errore NON stampa l'indirizzo, che contiene il permesso."""
    url = f"{API}/{percorso}?" + urllib.parse.urlencode({**campi, "access_token": token})
    try:
        with urllib.request.urlopen(url, timeout=30) as risposta:
            return json.loads(risposta.read().decode("utf-8"))
    except urllib.error.HTTPError as errore:
        corpo = errore.read().decode("utf-8", errors="replace").replace(token, "***")
        raise ErroreInstagram(f"Instagram ha risposto {errore.code} a «{percorso}»: {corpo}") from None
    except urllib.error.URLError as errore:
        raise ErroreInstagram(f"errore di rete su «{percorso}»: {errore.reason}") from None


def ordina(percorso: str, campi: dict, token: str) -> dict:
    """Un ordine che cambia qualcosa (POST). Il permesso viaggia nel corpo, non nell'indirizzo."""
    corpo = urllib.parse.urlencode({**campi, "access_token": token}).encode("utf-8")
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{API}/{percorso}", data=corpo, method="POST"), timeout=60) as risposta:
            return json.loads(risposta.read().decode("utf-8"))
    except urllib.error.HTTPError as errore:
        testo = errore.read().decode("utf-8", errors="replace").replace(token, "***")
        raise ErroreInstagram(f"Instagram ha risposto {errore.code} a «{percorso}»: {testo}") from None
    except urllib.error.URLError as errore:
        raise ErroreInstagram(f"errore di rete su «{percorso}»: {errore.reason}") from None


def cerca_su_instagram(didascalia: str, token: str, conto: str) -> dict | None:
    """Guarda fra gli ultimi post della pagina se ce n'è già uno con questa didascalia."""
    ultimi = chiedi(f"{conto}/media", {"fields": "id,caption,permalink,timestamp", "limit": "10"}, token)
    return next((m for m in ultimi.get("data", []) if (m.get("caption") or "").strip() == didascalia.strip()), None)


def pubblica(cartella: Path, indirizzo_immagine: str, davvero: bool = False, avvisa=print) -> dict:
    dati = leggi_pacchetto(cartella)
    segno = cartella / "in-pubblicazione.json"
    if (cartella / "pubblicato.json").exists():
        raise ErroreInstagram("questo pacchetto risulta già pubblicato (c'è pubblicato.json)")
    if segno.exists():
        raise ErroreInstagram("una pubblicazione di questo pacchetto è rimasta a metà (c'è in-pubblicazione.json): "
                              "prima si controlla su Instagram se il post è uscito, poi si toglie il file a mano")
    token = leggi_permesso()
    io = chiedi("me", {"fields": "user_id,username"}, token)
    if io.get("username") != UTENTE_ATTESO or not io.get("user_id"):
        raise ErroreInstagram(f"il permesso non è dell'account {UTENTE_ATTESO}")
    conto = io["user_id"]
    if davvero and cerca_su_instagram(dati["didascalia"], token, conto):
        raise ErroreInstagram("sulla pagina c'è già un post con questa identica didascalia: non pubblico un doppione")

    avvisa("Instagram scarica e prepara l'immagine…")
    contenitore = ordina(f"{conto}/media", {"image_url": indirizzo_immagine, "caption": dati["didascalia"]}, token).get("id")
    if not contenitore:
        raise ErroreInstagram("Instagram non ha restituito il contenitore")
    stato = ""
    for _ in range(5):
        stato = chiedi(contenitore, {"fields": "status_code"}, token).get("status_code", "")
        if stato != "IN_PROGRESS":
            break
        time.sleep(6)
    if stato != "FINISHED":
        raise ErroreInstagram(f"il contenitore non è pronto (stato: {stato or 'nessuno'}): non pubblico")
    if not davvero:
        avvisa("Prova generale riuscita: l'immagine è stata scaricata e preparata da Instagram. Non ho pubblicato.")
        return {"prova_generale": True, "contenitore": contenitore, "stato": stato}

    segno.write_text(json.dumps({"contenitore": contenitore, "quando": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    avvisa("Pubblico…")
    id_post = ordina(f"{conto}/media_publish", {"creation_id": contenitore}, token).get("id")
    if not id_post:
        raise ErroreInstagram("Instagram non ha restituito l'identificativo del post: controllare a mano sulla pagina")
    letto = {}
    for _ in range(3):  # l'indirizzo del post può arrivare con qualche secondo di ritardo; NON si ripubblica mai per questo
        letto = chiedi(id_post, {"fields": "permalink,caption,timestamp"}, token)
        if letto.get("permalink"):
            break
        time.sleep(4)
    ricevuta = {"id": dati["id"], "titolo": dati["titolo"], "id_instagram": id_post, "indirizzo": letto.get("permalink", ""),
                "pubblicato_il": letto.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                "didascalia_uguale": (letto.get("caption") or "").strip() == dati["didascalia"].strip()}
    (cartella / "pubblicato.json").write_text(json.dumps(ricevuta, ensure_ascii=False, indent=2), encoding="utf-8")
    segno.unlink()
    if not ricevuta["indirizzo"]:
        raise ErroreInstagram("il post risulta creato ma Instagram non ne restituisce l'indirizzo: controllare a mano sulla pagina")
    return ricevuta  # "didascalia_uguale": False non blocca (il post è già fuori), ma chi chiama lo deve dire ad Alena


def leggi_pacchetto(cartella: Path) -> dict:
    dati = json.loads((cartella / "post.json").read_text(encoding="utf-8"))
    jpg = cartella / "post.jpg"
    if not dati.get("didascalia", "").strip():
        raise ErroreInstagram("la didascalia del pacchetto è vuota")
    if "Verificato su" in dati["didascalia"]:
        raise ErroreInstagram("nella didascalia c'è la sezione interna «Verificato su»")
    if hashlib.sha256(jpg.read_bytes()).hexdigest() != dati.get("sha256_jpeg"):
        raise ErroreInstagram("post.jpg non è più quello preparato: va rifatto il pacchetto")
    return dati


def prova_a_secco(cartella: Path) -> int:
    dati = leggi_pacchetto(cartella)
    token = leggi_permesso()

    io = chiedi("me", {"fields": "id,user_id,username,account_type"}, token)
    print(f"1. Chi sono? -> {io.get('username')} ({io.get('account_type')})")
    if io.get("username") != UTENTE_ATTESO:
        raise ErroreInstagram(f"il permesso è dell'account {io.get('username')}, non di {UTENTE_ATTESO}")

    # Con "Instagram Login" l'identificativo dell'account professionale è user_id; provo anche gli altri
    # due modi e dico quale ha funzionato, così il lotto 2 usa quello giusto senza tirare a indovinare.
    limite, usato, errori = None, None, []
    for candidato in (io.get("user_id"), io.get("id"), "me"):
        if not candidato:
            continue
        try:
            limite = chiedi(f"{candidato}/content_publishing_limit", {"fields": "quota_usage,config"}, token)
            usato = "user_id" if candidato == io.get("user_id") else "id" if candidato == io.get("id") else "me"
            break
        except ErroreInstagram as errore:
            errori.append(str(errore))
    if limite is None:
        print("2. Quanti post posso ancora pubblicare? -> NESSUNA RISPOSTA: il permesso di pubblicare non risulta.")
        for e in errori:
            print("     ", e)
        return 1
    voce = (limite.get("data") or [{}])[0]
    quota = (voce.get("config") or {}).get("quota_total")
    print(f"2. Quanti post posso ancora pubblicare? -> usati {voce.get('quota_usage')} su {quota} nelle ultime 24 ore"
          f"  (identificativo che funziona: {usato})")

    print("\nCOSA VERREBBE MANDATO A INSTAGRAM (adesso non parte niente):")
    print(f"  mossa 1, creare il contenitore:  POST {API}/<identificativo>/media")
    print(f"      image_url = (l'indirizzo pubblico di {cartella.name}/post.jpg: arriva con il lotto 2)")
    print(f"      caption   = i {dati['caratteri']} caratteri qui sotto")
    print("  mossa 2, attendere che lo stato del contenitore sia FINISHED")
    print(f"  mossa 3, pubblicare:             POST {API}/<identificativo>/media_publish")
    print(f"\n  Immagine: {cartella / 'post.jpg'}  ({dati['peso_jpeg_byte'] / 1e6:.2f} MB)")
    print(f"  Esce il:  {dati.get('esce_il') or 'non fissato'}")
    print("  ----- didascalia, esattamente così -----")
    print(dati["didascalia"])
    print("  ----- fine didascalia -----")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    try:
        return prova_a_secco(Path(sys.argv[1]))
    except (ErroreInstagram, FileNotFoundError) as errore:
        print("ERRORE:", errore)
        return 1


if __name__ == "__main__":
    sys.exit(main())
