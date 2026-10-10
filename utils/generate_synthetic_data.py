#!/usr/bin/env python3
"""
generate_synthetic_data.py

Synthetic AML dataset generator for Sentinel.

Features
--------
- 1,000 KYC profiles
- 50,000 chronologically sorted transactions
- ~5% fraud
- Known device hashes per account
- Legitimate transactions always use known devices
- Fraud transactions always use unseen devices
- Fraud breakdown matches generated records
"""

import json
import random
import hashlib
import os
from datetime import datetime, timedelta

NUM_PROFILES=1000
NUM_TRANSACTIONS=50000

STRUCTURING=750
MULE_BASE=750
CHANNEL=500
VELOCITY_BASE=500

from datetime import timezone
START = datetime.now(timezone.utc)

CHANNELS=["UPI","NEFT","RTGS","CARD","IMPS","CASH"]
COUNTRIES=["IN","US","AE","SG"]
SAFE_MCC=["5411","5311","5732","5812","4900"]
RISK_MCC=["7995","6051","4829"]
TIERS=["TIER_1","TIER_2","TIER_3"]
OCC=["SALARIED","BUSINESS","STUDENT","RETIRED"]
INC=["LOW","MEDIUM","HIGH","ULTRA_HIGH"]

random.seed(42)
os.makedirs("data",exist_ok=True)

def h():
    return hashlib.sha256(str(random.random()).encode()).hexdigest()[:20]

def unknown(known):
    x=h()
    while x in known:
        x=h()
    return x

def acc(i): return f"ACC_{1000+i}"
def txn(i): return f"TXN_{100000+i}"

def vol(b):
    return {
        "LOW":random.uniform(15000,50000),
        "MEDIUM":random.uniform(50000,200000),
        "HIGH":random.uniform(200000,1000000),
        "ULTRA_HIGH":random.uniform(1000000,10000000)
    }[b]

profiles=[]
lookup={}
for i in range(1,NUM_PROFILES+1):
    band=random.choices(INC,[45,30,18,7])[0]
    occ=random.choices(OCC,[45,25,20,10])[0]
    known=[h() for _ in range(random.randint(1,3))]
    p={
        "account_id":acc(i),
        "account_age_days":random.randint(1,1800),
        "kyc_tier":random.choice(TIERS),
        "occupation_category":occ,
        "declared_income_band":band,
        "historical_avg_monthly_volume":round(vol(band),2),
        "linked_phone_count":random.randint(1,3),
        "linked_email_count":random.randint(1,2),
        "linked_device_count":len(known),
        "known_device_hashes":known
    }
    profiles.append(p)
    lookup[p["account_id"]]=p

with open("data/kyc_profiles.json","w") as f:
    json.dump(profiles,f,indent=2)

transactions=[]
fraud={"structuring":0,"mule":0,"channel":0,"velocity":0}

def rtime():
    return START+timedelta(seconds=random.randint(0,30*24*3600))

def amt(p):
    b=p["declared_income_band"]
    if b=="LOW": return random.uniform(200,5000)
    if b=="MEDIUM": return random.uniform(1000,25000)
    if b=="HIGH": return random.uniform(5000,100000)
    return random.uniform(50000,500000)

def add(ts,s,r,a,ch,mcc,c,f):
    dev=random.choice(lookup[s]["known_device_hashes"]) if f==0 else unknown(lookup[s]["known_device_hashes"])
    transactions.append({
        "transaction_id":txn(len(transactions)+1),
        "timestamp":ts.isoformat(),
        "sender_account":s,
        "receiver_account":r,
        "amount":round(a,2),
        "channel":ch,
        "device_fingerprint_hash":dev,
        "ip_geolocation":c,
        "merchant_category_code":mcc,
        "is_fraud":f
    })

# base fraud
for _ in range(STRUCTURING):
    s,r=random.sample(profiles,2)
    add(rtime(),s["account_id"],r["account_id"],
        random.choice([random.uniform(9500,9950),random.uniform(950000,995000)]),
        random.choice(["UPI","IMPS","NEFT"]),"4829","IN",1)
    fraud["structuring"]+=1

students=[p for p in profiles if p["occupation_category"] in ("STUDENT","RETIRED")]
for _ in range(MULE_BASE):
    mule=random.choice(students)
    s=random.choice(profiles)
    add(rtime(),s["account_id"],mule["account_id"],random.uniform(4000,15000),"UPI","5411","IN",1)
    fraud["mule"]+=1

for _ in range(CHANNEL):
    s,r=random.sample(profiles,2)
    add(rtime(),s["account_id"],r["account_id"],random.uniform(20000,500000),
        random.choice(["UPI","RTGS"]),random.choice(RISK_MCC),random.choice(["US","AE","SG"]),1)
    fraud["channel"]+=1

for _ in range(VELOCITY_BASE):
    s,r=random.sample(profiles,2)
    add(rtime(),s["account_id"],r["account_id"],random.uniform(5000,20000),
        "IMPS","5411","IN",1)
    fraud["velocity"]+=1

# realism expansions
for _ in range(100):
    mule=random.choice(students)
    t=rtime()
    senders=random.sample(profiles,6)
    for i,s in enumerate(senders):
        add(t+timedelta(minutes=i*5),s["account_id"],mule["account_id"],random.uniform(4000,12000),"UPI","5411","IN",1)
        fraud["mule"]+=1
    r=random.choice(profiles)
    add(t+timedelta(minutes=35),mule["account_id"],r["account_id"],random.uniform(50000,90000),"IMPS","4829","IN",1)
    fraud["mule"]+=1

for _ in range(50):
    s=random.choice(profiles)
    t=rtime()
    rec=random.sample([x for x in profiles if x["account_id"]!=s["account_id"]],10)
    for i,r in enumerate(rec):
        add(t+timedelta(seconds=30*i),s["account_id"],r["account_id"],random.uniform(3000,10000),"IMPS","5411","IN",1)
        fraud["velocity"]+=1

fraud_total=sum(fraud.values())
normal=max(0,NUM_TRANSACTIONS-fraud_total)
for _ in range(normal):
    s,r=random.sample(profiles,2)
    add(rtime(),s["account_id"],r["account_id"],amt(s),random.choice(CHANNELS),random.choice(SAFE_MCC),
        random.choices(COUNTRIES,[90,4,3,3])[0],0)

transactions.sort(key=lambda x:x["timestamp"])

assert len(transactions)==NUM_TRANSACTIONS

with open("data/raw_transactions.jsonl","w") as f:
    for t in transactions:
        f.write(json.dumps(t)+"\n")

print("===================================")
print("Synthetic Dataset Complete")
print("Profiles:",len(profiles))
print("Transactions:",len(transactions))
print("Fraud:",fraud_total)
print(f"Fraud Rate: {fraud_total/NUM_TRANSACTIONS*100:.2f}%")
print("Breakdown:")
for k,v in fraud.items():
    print(f"  {k}: {v}")
print("===================================")
