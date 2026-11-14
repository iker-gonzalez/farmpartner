"""USDA NRCS SSURGO via Soil Data Access (the database behind Web Soil Survey). U.S. only."""
from app.data.http import post_form_json

URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"


def soil_at(lat: float, lon: float) -> dict | None:
    q = (
        "SELECT TOP 1 mu.muname, c.compname, c.slope_r, c.drainagecl, c.hydgrp, h.om_r, h.claytotal_r, "
        "h.sandtotal_r, h.kwfact, h.awc_r FROM SDA_Get_Mukey_from_intersection_with_WktWgs84("
        f"'point({lon:.5f} {lat:.5f})') m JOIN mapunit mu ON mu.mukey=m.mukey "
        "JOIN component c ON c.mukey=m.mukey JOIN chorizon h ON h.cokey=c.cokey "
        "WHERE h.hzdept_r=0 ORDER BY c.comppct_r DESC"
    )
    try:
        res = post_form_json(URL, {"query": q, "format": "JSON+COLUMNNAME"}, ttl_s=0,
                             keep=lambda r: bool(r.get("Table")))
    except Exception:
        return None
    table = res.get("Table") or []
    if len(table) < 2:
        return None
    row = dict(zip(table[0], table[1]))

    def num(k):
        try:
            return float(row[k])
        except (TypeError, ValueError, KeyError):
            return None

    return {"map_unit": row.get("muname"), "series": row.get("compname"), "slope_pct": num("slope_r"),
            "drainage": row.get("drainagecl"), "hydrologic_group": row.get("hydgrp"), "om_pct": num("om_r"),
            "clay_pct": num("claytotal_r"), "sand_pct": num("sandtotal_r"), "k_factor": num("kwfact"),
            "awc": num("awc_r")}
