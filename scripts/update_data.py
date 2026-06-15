"""Build workforce.json from spreadsheet + illustrative psychiatrist & nurse placements."""
import json
import os
import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = os.path.join(ROOT, "clinical psychologists ghana 2025.xlsx")
OUT = os.path.join(ROOT, "data", "workforce.json")

REGION_MAP = {
    "Accra": "Greater Accra",
    "Westren": "Western",
    "Northern": "Northern",
    "Bon East": "Bono East",
    "Savanna Region": "Savanna",
}

# Accra psychiatric hubs (dummy placements — psychiatrists concentrated in Accra)
ACCRA_PSYCH_FACILITIES = [
    {
        "facility": "Pantang Hospital",
        "region": "Greater Accra",
        "district": "La Nkwantanang Madina",
        "subDistrict": "Pantang",
        "lat": 5.681,
        "lng": -0.1667,
        "psychiatrists": 28,
        "psychologists": 0,
        "nurses": 4,
    },
    {
        "facility": "Accra Psychiatric Hospital",
        "region": "Greater Accra",
        "district": "Accra Metro",
        "subDistrict": "Asylum Down",
        "lat": 5.556,
        "lng": -0.196,
        "psychiatrists": 22,
        "psychologists": 0,
        "nurses": 3,
    },
    {
        "facility": "Korle Bu Teaching Hospital — Psychiatric Unit",
        "region": "Greater Accra",
        "district": "Accra Metro",
        "subDistrict": "Korle Bu",
        "lat": 5.536,
        "lng": -0.227,
        "psychiatrists": 12,
        "psychologists": 0,
        "nurses": 5,
    },
]

# Psychiatrist placements on mapped facilities (remainder after Accra hubs = 26)
FACILITY_PSYCHIATRISTS = {
    "LEKMA Hospital": 4,
    "Ga East Municapal Hospital": 3,
    "Shi-Osudoku Hospital": 2,
    "Tema General Hosptal": 2,
    "Northern Regional Hospital": 3,
    "Presbytarian Hospital": 4,
    "Eastern Regional Hospital": 2,
    "Trauma & Specilist Hospital": 2,
    "Richard Novati Catholic Hospital": 2,
    "Upper West Regional Hospital": 1,
    "Sunyani Municipal Hospital (Penkwase)": 1,
}

# Nurse weights — higher for rural / CHPS facilities
RURAL_NURSE_WEIGHT = 4
URBAN_NURSE_WEIGHT = 1


def clean_coord(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("\xa0", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def load_psychologists():
    ws = openpyxl.load_workbook(XLSX)["facilities"]
    records = []
    current_region = None

    for row in ws.iter_rows(min_row=2, max_row=29, values_only=True):
        region, district, sub, facility, workers, lng, lat = row[:7]
        if region:
            current_region = str(region).strip()
        if workers is None and facility is None:
            continue
        if not facility:
            facility = "Sunyani Municipal Hospital (Penkwase)"

        lng_v = clean_coord(lng)
        lat_v = clean_coord(lat)
        if facility == "Sunyani Municipal Hospital (Penkwase)" and lat_v is None:
            lat_v, lng_v = 7.3349, -2.3117
        if facility == "Presbytarian Hospital" and lng_v and lng_v > 0:
            lng_v = -lng_v

        records.append(
            {
                "region": REGION_MAP.get(current_region, current_region),
                "district": str(district).strip() if district else "",
                "subDistrict": str(sub).strip() if sub else "",
                "facility": str(facility).strip(),
                "lat": lat_v,
                "lng": lng_v,
                "psychologists": int(workers) if workers else 0,
                "psychiatrists": 0,
                "nurses": 0,
            }
        )
    return records


def nurse_weight(f):
    rural_regions = {"Savanna", "Upper West", "Northern", "Bono East", "Western North", "Western", "Volta"}
    is_chps = "CHPS" in f["facility"].upper() or "CHP" in f["facility"].upper()
    w = RURAL_NURSE_WEIGHT if f["region"] in rural_regions or is_chps else URBAN_NURSE_WEIGHT
    return w * (2 if is_chps else 1)


def distribute_nurses(facilities, total=108):
    weights = [nurse_weight(f) for f in facilities]
    wsum = sum(weights)
    assigned = []
    remainder = total

    for i, f in enumerate(facilities):
        if i == len(facilities) - 1:
            n = remainder
        else:
            n = max(0, round(total * weights[i] / wsum))
            remainder -= n
        assigned.append(n)

    # Fix rounding drift
    diff = total - sum(assigned)
    if diff:
        assigned[0] += diff

    for f, n in zip(facilities, assigned):
        f["nurses"] = n


def main():
    facilities = load_psychologists()

    for f in facilities:
        f["psychiatrists"] = FACILITY_PSYCHIATRISTS.get(f["facility"], 0)

    facilities = ACCRA_PSYCH_FACILITIES + facilities
    distribute_nurses(facilities, total=108)

    totals = {
        "psychiatrists": sum(f["psychiatrists"] for f in facilities),
        "psychologists": sum(f["psychologists"] for f in facilities),
        "nurses": sum(f["nurses"] for f in facilities),
    }
    totals["all"] = sum(totals.values())

    payload = {
        "meta": {
            "title": "Mental Health Workforce Ghana 2025",
            "note": "Psychologists from MHA facility mapping; psychiatrist & nurse figures illustrative pending full HR audit.",
            "totals": totals,
        },
        "cadres": [
            {"id": "psychiatrists", "label": "Psychiatrists", "color": "#6b2d8e"},
            {"id": "psychologists", "label": "Psychologists", "color": "#2e9b4f"},
            {"id": "nurses", "label": "Mental Health Nurses", "color": "#1a7fa0"},
        ],
        "facilities": facilities,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(facilities)} facilities to {OUT}")
    print(f"Totals: {totals}")


if __name__ == "__main__":
    main()
