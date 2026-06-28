import os
import io
import csv
import zipfile
import dns.resolver
import requests
from bs4 import BeautifulSoup

_orig_read_resolv_conf = dns.resolver.Resolver.read_resolv_conf

def _patched_read_resolv_conf(self, f):
    try:
        _orig_read_resolv_conf(self, f)
    except (FileNotFoundError, OSError):
        self.nameservers = ["8.8.8.8"]

dns.resolver.Resolver.read_resolv_conf = _patched_read_resolv_conf

from pymongo import MongoClient
from datetime import datetime

BAST_BASE = "https://www.bast.de"
BAST_DZ_URL = f"{BAST_BASE}/DE/Publikationen/Daten/Verkehrstechnik/DZ.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
}


def find_zip_url():
    print(f"Suche aktuellen Download-Link auf {BAST_DZ_URL} ...")
    resp = requests.get(BAST_DZ_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".zip" in href.lower() and ("DZ" in href or "dz" in href):
            return href if href.startswith("http") else BAST_BASE + href
    # Fallback: erster ZIP-Link auf der Seite
    for a in soup.find_all("a", href=True):
        if ".zip" in a["href"].lower():
            href = a["href"]
            return href if href.startswith("http") else BAST_BASE + href
    raise RuntimeError("Kein ZIP-Link auf der BASt-Seite gefunden.")


def download_zip(url):
    print(f"Lade ZIP: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=120)
    resp.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(resp.content))


def find_stammdaten_csv(zf):
    """Findet die Stammdaten-CSV (Zählstellen-Infos) im ZIP."""
    for name in zf.namelist():
        n = name.lower()
        if "stamm" in n and n.endswith(".csv"):
            return name
        if "zaehlstell" in n and n.endswith(".csv"):
            return name
        if "zst" in n and n.endswith(".csv"):
            return name
    # Fallback: erste CSV
    for name in zf.namelist():
        if name.lower().endswith(".csv"):
            return name
    raise RuntimeError(f"Keine Stammdaten-CSV im ZIP. Dateien: {zf.namelist()}")


def parse_stammdaten(zf, csv_name):
    print(f"Parse {csv_name} ...")
    with zf.open(csv_name) as f:
        content = f.read().decode("latin-1")
    reader = csv.DictReader(io.StringIO(content), delimiter=";")
    stationen = []
    for row in reader:
        keys = {k.strip().lower(): v.strip() for k, v in row.items()}

        # Koordinaten-Spalten (verschiedene BASt-Versionen)
        breite = keys.get("breite") or keys.get("lat") or keys.get("y") or ""
        laenge = keys.get("laenge") or keys.get("lon") or keys.get("lng") or keys.get("x") or ""

        try:
            breite_f = float(breite.replace(",", ".")) if breite else None
            laenge_f = float(laenge.replace(",", ".")) if laenge else None
        except ValueError:
            breite_f = laenge_f = None

        station = {
            "zaehlstelle_id": keys.get("zst") or keys.get("id") or keys.get("zaehlstelle"),
            "name":            keys.get("name") or keys.get("strassenname") or keys.get("strassenbez"),
            "strassennummer":  keys.get("strnr") or keys.get("strassennummer"),
            "bundesland":      keys.get("bl") or keys.get("bundesland"),
            "laenge":          laenge_f,
            "breite":          breite_f,
            "rohdaten":        dict(row),
            "importiert_am":   datetime.utcnow(),
        }
        if breite_f and laenge_f:
            station["standort"] = {
                "type": "Point",
                "coordinates": [laenge_f, breite_f]
            }
        stationen.append(station)
    return stationen


def save_to_mongo(stationen):
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise EnvironmentError("MONGO_URI ist nicht gesetzt")
    client = MongoClient(uri)
    db = client["bast_test"]
    col = db["zaehlstellen"]
    col.drop()
    col.create_index([("standort", "2dsphere")], sparse=True)
    result = col.insert_many(stationen)
    print(f"{len(result.inserted_ids)} Zählstellen in MongoDB gespeichert.")
    client.close()


if __name__ == "__main__":
    zip_url = find_zip_url()
    zf = download_zip(zip_url)
    csv_name = find_stammdaten_csv(zf)
    stationen = parse_stammdaten(zf, csv_name)
    print(f"{len(stationen)} Zählstellen geparst.")
    save_to_mongo(stationen)
