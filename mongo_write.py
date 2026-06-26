import os
import dns.resolver

_orig_read_resolv_conf = dns.resolver.Resolver.read_resolv_conf

def _patched_read_resolv_conf(self, f):
    try:
        _orig_read_resolv_conf(self, f)
    except (FileNotFoundError, OSError):
        self.nameservers = ["8.8.8.8"]

dns.resolver.Resolver.read_resolv_conf = _patched_read_resolv_conf

from pymongo import MongoClient
from datetime import datetime

uri = os.getenv("MONGO_URI")
if not uri:
    raise EnvironmentError("MONGO_URI ist nicht gesetzt")

client = MongoClient(uri)
db = client["bast_test"]
collection = db["bast"]

dummy = {
    "name": "Testdatensatz",
    "wert": 42,
    "erstellt_am": datetime.utcnow()
}

result = collection.insert_one(dummy)
print(f"Dokument eingefügt mit ID: {result.inserted_id}")

client.close()
