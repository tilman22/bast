import os
import dns.resolver
from pymongo import MongoClient
from datetime import datetime

dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ["8.8.8.8"]

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
