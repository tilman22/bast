import os
import dns.resolver
import requests

_orig_read_resolv_conf = dns.resolver.Resolver.read_resolv_conf

def _patched_read_resolv_conf(self, f):
    try:
        _orig_read_resolv_conf(self, f)
    except (FileNotFoundError, OSError):
        self.nameservers = ["8.8.8.8"]

dns.resolver.Resolver.read_resolv_conf = _patched_read_resolv_conf

from pymongo import MongoClient
from datetime import datetime

BAST_WFS_URL = (
    "https://www.bast.de/geoserver/ows"
    "?service=WFS"
    "&version=2.0.0"
    "&request=GetFeature"
    "&typeNames=bast:dauerzaehlstellen"
    "&outputFormat=application/json"
    "&srsName=EPSG:4326"
)

def fetch_zaehlstellen():
    print("Lade BASt-Zählstellen...")
    response = requests.get(BAST_WFS_URL, timeout=30)
    response.raise_for_status()
    data = response.json()
    features = data.get("features", [])
    print(f"{len(features)} Zählstellen gefunden.")
    return features

def parse_zaehlstelle(feature):
    props = feature.get("properties", {})
    geom = feature.get("geometry", {})
    coords = geom.get("coordinates", [None, None])
    return {
        "zaehlstelle_id": props.get("zaehlstelle_id") or props.get("id"),
        "name": props.get("name") or props.get("strassenname"),
        "strassennummer": props.get("strassennummer"),
        "bundesland": props.get("bundesland"),
        "laenge": coords[0],
        "breite": coords[1],
        "standort": {
            "type": "Point",
            "coordinates": coords
        },
        "eigenschaften": props,
        "importiert_am": datetime.utcnow()
    }

def save_to_mongo(zaehlstellen):
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise EnvironmentError("MONGO_URI ist nicht gesetzt")

    client = MongoClient(uri)
    db = client["bast_test"]
    collection = db["zaehlstellen"]

    collection.drop()
    collection.create_index([("standort", "2dsphere")])

    result = collection.insert_many(zaehlstellen)
    print(f"{len(result.inserted_ids)} Zählstellen in MongoDB gespeichert.")
    client.close()

if __name__ == "__main__":
    features = fetch_zaehlstellen()
    zaehlstellen = [parse_zaehlstelle(f) for f in features]
    save_to_mongo(zaehlstellen)
