import os
import time
import dns.resolver
import requests

_orig_read_resolv_conf = dns.resolver.Resolver.read_resolv_conf

def _patched_read_resolv_conf(self, f):
    try:
        _orig_read_resolv_conf(self, f)
    except (FileNotFoundError, OSError):
        self.nameservers = ["8.8.8.8"]

dns.resolver.Resolver.read_resolv_conf = _patched_read_resolv_conf

from pymongo import MongoClient, UpdateOne
from datetime import datetime, date

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_VARS = [
    "temperature_2m",
    "precipitation",
    "sunshine_duration",
    "cloudcover",
    "windspeed_10m",
    "relativehumidity_2m",
]

START_DATE = "2025-01-01"
END_DATE   = "2025-12-31"

# Eigene Koordinatentabelle (wird durch MongoDB-Zählstellen ergänzt)
EIGENE_STANDORTE = [
    # {"id": "berlin",  "name": "Berlin",  "laenge": 13.405, "breite": 52.520},
    # {"id": "hamburg", "name": "Hamburg", "laenge": 9.993,  "breite": 53.550},
]


def get_standorte(db):
    """Liest Zählstellen aus MongoDB – nur solche mit Koordinaten."""
    col = db["zaehlstellen"]
    standorte = []
    for doc in col.find({"breite": {"$ne": None}, "laenge": {"$ne": None}}):
        standorte.append({
            "id":     str(doc.get("zaehlstelle_id") or doc["_id"]),
            "name":   doc.get("name", ""),
            "laenge": doc["laenge"],
            "breite": doc["breite"],
        })
    standorte += EIGENE_STANDORTE
    return standorte


def fetch_wetter(laenge, breite):
    params = {
        "latitude":   breite,
        "longitude":  laenge,
        "start_date": START_DATE,
        "end_date":   END_DATE,
        "hourly":     ",".join(HOURLY_VARS),
        "timezone":   "Europe/Berlin",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def parse_stunden(data):
    """Wandelt die API-Antwort in eine Liste von stündlichen Einträgen um."""
    hourly = data.get("hourly", {})
    timestamps = hourly.get("time", [])
    stunden = []
    for i, ts in enumerate(timestamps):
        eintrag = {"zeitpunkt": ts}
        for var in HOURLY_VARS:
            werte = hourly.get(var, [])
            eintrag[var] = werte[i] if i < len(werte) else None
        stunden.append(eintrag)
    return stunden


def aggregiere_tage(stunden):
    """Aggregiert Stundenwerte zu Tageswerten."""
    tage = {}
    for s in stunden:
        tag = s["zeitpunkt"][:10]
        if tag not in tage:
            tage[tag] = {
                "datum": tag,
                "temperatur_min":    None,
                "temperatur_max":    None,
                "temperatur_mittel": None,
                "niederschlag_sum":  0.0,
                "sonnenstunden":     0.0,
                "stunden":           [],
            }
        t = tage[tag]
        temp = s.get("temperature_2m")
        if temp is not None:
            t["temperatur_min"] = min(t["temperatur_min"], temp) if t["temperatur_min"] is not None else temp
            t["temperatur_max"] = max(t["temperatur_max"], temp) if t["temperatur_max"] is not None else temp
        prec = s.get("precipitation") or 0
        t["niederschlag_sum"] = round(t["niederschlag_sum"] + prec, 2)
        sun = s.get("sunshine_duration") or 0
        t["sonnenstunden"] = round(t["sonnenstunden"] + sun / 3600, 2)
        t["stunden"].append(s)

    for t in tage.values():
        temps = [s["temperature_2m"] for s in t["stunden"] if s.get("temperature_2m") is not None]
        if temps:
            t["temperatur_mittel"] = round(sum(temps) / len(temps), 1)

    return list(tage.values())


def save_wetter(db, standort_id, standort_name, tage):
    col = db["wetterdaten"]
    ops = []
    for tag in tage:
        ops.append(UpdateOne(
            {"standort_id": standort_id, "datum": tag["datum"]},
            {"$set": {
                "standort_id":       standort_id,
                "standort_name":     standort_name,
                "datum":             tag["datum"],
                "temperatur_min":    tag["temperatur_min"],
                "temperatur_max":    tag["temperatur_max"],
                "temperatur_mittel": tag["temperatur_mittel"],
                "niederschlag_sum":  tag["niederschlag_sum"],
                "sonnenstunden":     tag["sonnenstunden"],
                "stunden":           tag["stunden"],
                "aktualisiert_am":   datetime.utcnow(),
            }},
            upsert=True
        ))
    if ops:
        result = col.bulk_write(ops)
        return result.upserted_count + result.modified_count
    return 0


def main():
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise EnvironmentError("MONGO_URI ist nicht gesetzt")

    client = MongoClient(uri)
    db = client["bast_test"]

    # Index anlegen
    db["wetterdaten"].create_index([("standort_id", 1), ("datum", 1)], unique=True)

    standorte = get_standorte(db)
    print(f"{len(standorte)} Standorte gefunden.")

    for i, standort in enumerate(standorte):
        sid   = standort["id"]
        name  = standort["name"]
        lat   = standort["breite"]
        lon   = standort["laenge"]
        print(f"[{i+1}/{len(standorte)}] {name} ({sid}) lat={lat} lon={lon} ...")

        try:
            data   = fetch_wetter(lon, lat)
            stunden = parse_stunden(data)
            tage   = aggregiere_tage(stunden)
            n      = save_wetter(db, sid, name, tage)
            print(f"  → {len(tage)} Tage, {n} gespeichert/aktualisiert")
        except Exception as e:
            print(f"  ⚠ Fehler: {e}")

        # Rate-Limit: Open-Meteo erlaubt ~10.000 Anfragen/Tag
        time.sleep(0.2)

    client.close()
    print("Fertig.")


if __name__ == "__main__":
    main()
