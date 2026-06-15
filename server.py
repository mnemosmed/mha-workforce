"""MHA Workforce Dashboard — static files + workforce API."""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFORCE_PATH = os.path.join(ROOT, "data", "workforce.json")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {
    "User-Agent": "MHA-Workforce-Dashboard/1.0 (mental health authority ghana)",
}
GHANA_REGIONS = {
    "greater accra", "ashanti", "central", "eastern", "volta", "western",
    "western north", "northern", "savanna", "savannah", "upper east", "upper west",
    "bono", "bono east", "ahafo", "oti", "north east",
}
FACILITY_SUFFIXES = (
    " Psychiatric Hospital",
    " Regional Hospital",
    " General Hospital",
    " Municipal Hospital",
    " Teaching Hospital",
    " Catholic Hospital",
    " Polyclinic",
    " Hospital",
    " Health Centre",
    " Health Center",
    " CHPS",
    " Clinic",
)

app = Flask(__name__, static_folder=ROOT, static_url_path="")


def portal_auth_configured():
    return bool(os.environ.get("PORTAL_USERNAME") and os.environ.get("PORTAL_PASSWORD"))


def portal_authorized():
    if not portal_auth_configured():
        return True
    auth = request.authorization
    return (
        auth is not None
        and auth.username == os.environ["PORTAL_USERNAME"]
        and auth.password == os.environ["PORTAL_PASSWORD"]
    )


def require_portal_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if portal_authorized():
            return view(*args, **kwargs)
        return Response(
            "Admin access required.",
            401,
            {"WWW-Authenticate": 'Basic realm="MHA Data Portal"'},
        )

    return wrapped


def load_workforce():
    with open(WORKFORCE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_workforce(data):
    data = recalc_totals(data)
    data["meta"]["updatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(WORKFORCE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def recalc_totals(data):
    cadre_ids = [c["id"] for c in data["cadres"]]
    totals = {cid: 0 for cid in cadre_ids}
    for facility in data["facilities"]:
        for cid in cadre_ids:
            totals[cid] += int(facility.get(cid) or 0)
    totals["all"] = sum(totals[cid] for cid in cadre_ids)
    data["meta"]["totals"] = totals
    return data


def validate_workforce(data):
    if "facilities" not in data or "cadres" not in data:
        raise ValueError("Payload must include facilities and cadres")
    required = {"facility", "region", "psychiatrists", "psychologists", "nurses"}
    for i, f in enumerate(data["facilities"]):
        missing = required - set(f.keys())
        if missing:
            raise ValueError(f"Facility {i + 1} missing fields: {', '.join(sorted(missing))}")
        for cid in ("psychiatrists", "psychologists", "nurses"):
            f[cid] = max(0, int(f.get(cid) or 0))
        for coord in ("lat", "lng"):
            if f.get(coord) is not None:
                f[coord] = float(f[coord])
    return data


@app.get("/api/workforce")
@require_portal_auth
def get_workforce():
    return jsonify(load_workforce())


@app.put("/api/workforce")
@require_portal_auth
def put_workforce():
    try:
        data = validate_workforce(request.get_json(force=True))
        existing = load_workforce()
        data["meta"] = {**existing.get("meta", {}), **data.get("meta", {})}
        data["cadres"] = existing["cadres"]
        saved = save_workforce(data)
        return jsonify({"ok": True, "data": saved})
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def normalize_region(region):
    region = region.strip()
    return re.sub(r"\s+region\s*$", "", region, flags=re.IGNORECASE).strip()


def normalize_district(district):
    district = district.strip()
    for suffix in (" - District", " District", " Municipal", " Metro", " Municipal District"):
        if district.lower().endswith(suffix.lower()):
            district = district[: -len(suffix)].strip()
    return district


def extract_place_token(facility):
    name = facility.strip()
    lowered = name.lower()
    for suffix in FACILITY_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            name = name[: -len(suffix)].strip()
            break
    return name.split()[0] if name else facility.split()[0]


def build_geocode_queries(facility, region, district, sub_district):
    region = normalize_region(region)
    district = normalize_district(district)
    place = extract_place_token(facility)

    candidates = []
    full_parts = [facility]
    if sub_district:
        full_parts.append(sub_district)
    if district:
        full_parts.append(district)
    if region:
        full_parts.append(region)
    full_parts.append("Ghana")
    candidates.append(", ".join(full_parts))

    if district and region:
        candidates.append(f"{facility}, {district}, {region}, Ghana")
    if region:
        candidates.append(f"{facility}, {region} Region, Ghana")
        candidates.append(f"{facility}, {region}, Ghana")
        if place and place.lower() != facility.lower():
            candidates.append(f"{place}, {region} Region, Ghana")
            candidates.append(f"{place}, {region}, Ghana")
    candidates.append(f"{facility}, Ghana")

    if place and place.lower() != facility.lower():
        if district and region:
            candidates.append(f"{place}, {district}, {region}, Ghana")
        candidates.append(f"{place}, Ghana")

    seen = set()
    queries = []
    for query in candidates:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            queries.append(query)
    return queries, region, district, place


def score_geocode_hit(hit, facility, region, district, place):
    display = hit.get("display_name", "").lower()
    region_key = normalize_region(region).lower()
    district_key = normalize_district(district).lower()
    place_key = place.lower()
    score = 0

    if region_key and region_key in display:
        score += 15
    if district_key:
        for part in district_key.replace("/", " ").split():
            if len(part) > 3 and part in display:
                score += 4
    if place_key and place_key in display:
        score += 20

    hit_type = hit.get("type", "")
    hit_class = hit.get("class", "")
    if hit_type in {"hospital", "clinic", "doctors", "healthcare"}:
        score += 25
    elif hit_class == "amenity":
        score += 12
    elif hit_type in {"village", "town", "hamlet", "suburb", "locality"}:
        score += 10

    if hit_type in {"road", "highway", "residential"}:
        score -= 12

    for other_region in GHANA_REGIONS:
        if other_region != region_key and other_region in display:
            score -= 8

    if facility.lower() in display:
        score += 30

    return score


def nominatim_search(query, limit=5):
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "limit": limit,
            "countrycodes": "gh",
            "addressdetails": 1,
        }
    )
    url = f"{NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode_facility(facility, region="", district="", sub_district=""):
    queries, region, district, place = build_geocode_queries(
        facility, region, district, sub_district
    )

    best_hit = None
    best_score = -999
    best_query = queries[0]

    for index, query in enumerate(queries):
        if index > 0:
            time.sleep(1.05)
        try:
            results = nominatim_search(query)
        except Exception:
            continue

        for hit in results:
            score = score_geocode_hit(hit, facility, region, district, place)
            if score > best_score:
                best_score = score
                best_hit = hit
                best_query = query

        if best_score >= 25:
            break

    if not best_hit or best_score < 5:
        return None

    approximate = best_score < 25 or facility.lower() not in best_hit.get("display_name", "").lower()
    return {
        "lat": float(best_hit["lat"]),
        "lng": float(best_hit["lon"]),
        "displayName": best_hit.get("display_name", best_query),
        "query": best_query,
        "approximate": approximate,
    }


@app.get("/api/geocode")
@require_portal_auth
def geocode():
    facility = request.args.get("facility", "").strip()
    if not facility:
        return jsonify({"ok": False, "error": "Facility name is required"}), 400

    region = request.args.get("region", "").strip()
    district = request.args.get("district", "").strip()
    sub_district = request.args.get("subDistrict", "").strip()

    try:
        result = geocode_facility(facility, region, district, sub_district)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Geocoding failed: {exc}"}), 502

    if not result:
        return jsonify(
            {
                "ok": False,
                "error": "No location found. Try a clearer facility name or check the region.",
            }
        ), 404

    message = result["displayName"]
    if result["approximate"]:
        message = f"Approximate location near {result['displayName']}"

    return jsonify(
        {
            "ok": True,
            "lat": result["lat"],
            "lng": result["lng"],
            "displayName": message,
            "query": result["query"],
            "approximate": result["approximate"],
        }
    )


@app.get("/")
def dashboard():
    return send_from_directory(ROOT, "index.html")


@app.get("/portal")
@require_portal_auth
def portal():
    return send_from_directory(ROOT, "portal.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(ROOT, path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    print("MHA Workforce Dashboard")
    print(f"  Dashboard: http://localhost:{port}")
    print(f"  Data portal: http://localhost:{port}/portal")
    if not portal_auth_configured():
        print("  Warning: set PORTAL_USERNAME and PORTAL_PASSWORD to protect /portal")
    app.run(host="0.0.0.0", port=port, debug=debug)
