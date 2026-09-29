import json, sys, re
sys.stdout.reconfigure(encoding="utf-8")

schema = json.load(open("input_schema_full.json", encoding="utf-8"))
sm = schema["properties"]["searchMatching"]

def clean(s):
    return re.sub(r"[^\x00-\x7f]", "", s or "")

print("title:", clean(sm.get("title")))
print("type:", sm.get("type"))
print("enum:", sm.get("enum"))
print("enumTitles:", sm.get("enumTitles"))
print("default:", sm.get("default"))
print("description:", clean(sm.get("description"))[:1000])
