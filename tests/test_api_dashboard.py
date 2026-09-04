"""Evidence-dashboard endpoints (spec §7.6 trust affordances, extended): facts come from the views, never the model;
unknown ids are reported, not dropped; every figure's SQL re-runs verbatim through the console sandbox."""

from tests.test_api import (  # noqa: F401  (module-scoped fixtures: mini index + scripted model)
    client,
    db_path,
)


def test_evidence_profile(client):  # noqa: F811
    p = client.post(
        "/api/trials/profile", json={"nct_ids": ["NCT02142738", "NCT00000000", "junk", "NCT02142738"]}
    ).json()
    assert p["n_requested"] == 2 and p["n_found"] == 1 and p["missing"] == ["NCT00000000"]
    row = p["rows"][0]
    assert row["nct_id"] == "NCT02142738" and row["phase_norm"] == "PHASE3"
    assert any(a["asset_id"] == "pembrolizumab" for a in row["assets"])
    assert "NCT02142738" in p["sql"]
    assert client.post("/api/trials/profile", json={"nct_ids": []}).json()["rows"] == []
    assert client.post("/api/trials/profile", json={"nct_ids": ["NCT00000001"] * 2001}).status_code == 422


def test_entity_landscape(client):  # noqa: F811
    d = client.get("/api/entities/drug/pembrolizumab/landscape").json()
    assert d["name"].lower().startswith("pembrolizumab") and d["headline"]["conditions"] >= 1
    role_chart = next(c for c in d["charts"] if "role" in c["title"].lower())
    roles = {i["label"]: i["value"] for i in role_chart["items"]}
    assert roles.get("subject", 0) >= 1
    for c in d["charts"]:  # every chart's SQL re-runs verbatim through the same sandbox the console uses
        assert client.post("/api/sql", json={"sql": c["sql"]}).status_code == 200, c["title"]
    assert client.post("/api/sql", json={"sql": d["headline_sql"]}).status_code == 200

    key = client.get(
        "/api/entities/resolve", params={"q": "Carcinoma, Non-Small-Cell Lung", "kind": "condition"}
    )
    cond_key = key.json()["candidates"][0]["id"]
    c = client.get(f"/api/entities/condition/{cond_key}/landscape").json()
    assert c["headline"]["programs"] >= 1
    phase_chart = next(ch for ch in c["charts"] if ch["title"].startswith("Programs by"))
    assert sum(i["value"] for i in phase_chart["items"]) == c["headline"]["programs"]  # missing ≠ dropped

    company = client.get("/api/trials/NCT02142738").json()["lead_company_id"]
    co = client.get(f"/api/entities/company/{company}/landscape").json()
    assert co["headline"]["drug_trials"] >= 1 and len(co["charts"]) == 4

    assert client.get("/api/entities/drug/not-an-asset/landscape").status_code == 404
    assert client.get("/api/entities/moa/x/landscape").status_code == 400
