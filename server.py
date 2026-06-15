"""MHA Workforce Dashboard — static files + workforce API."""
import json
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFORCE_PATH = os.path.join(ROOT, "data", "workforce.json")

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
