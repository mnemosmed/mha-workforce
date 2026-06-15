import json

d = json.load(open("data/workforce.json", encoding="utf-8"))
by = {}
for f in d["facilities"]:
    r = f["region"]
    by.setdefault(r, {"psychiatrists": 0, "nurses": 0, "psychologists": 0})
    for k in by[r]:
        by[r][k] += f[k]

print("By region:")
for r, v in sorted(by.items(), key=lambda x: -x[1]["psychiatrists"]):
    print(f"  {r}: psych={v['psychiatrists']}, psychol={v['psychologists']}, nurses={v['nurses']}")

accra = by.get("Greater Accra", {})
print(f"\nAccra psychiatrists: {accra.get('psychiatrists', 0)}/88")
rural = {"Savanna", "Upper West", "Northern", "Bono East", "Western North", "Western", "Volta"}
rn = sum(by.get(r, {}).get("nurses", 0) for r in rural)
print(f"Rural nurses: {rn}/108")
