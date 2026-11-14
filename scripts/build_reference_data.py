"""Build FarmPartner's small reference tables from public datasets (run once; outputs are committed in data/).

    uv run python scripts/build_reference_data.py <papers_dir> <geonames_dir>

papers_dir:   FAOSTAT Production_Crops_Livestock_E_All_Data.zip + DRIVES CSVs (site_info.csv, rotation_phases.csv)
geonames_dir: cities15000.txt + countryInfo.txt from https://download.geonames.org/export/dump/

Outputs
  data/country_points.csv   lat,lon,m49  — towns ≥15k people (GeoNames, CC BY 4.0): nearest town → country
  data/fao_country_crops.csv m49,country,item,crop_id,area_ha,yield_t_ha — mean of the last 5 years (FAOSTAT, CC BY-NC-SA 3.0 IGO)
  data/drives_sites.json     long-term rotation experiments in North America (DRIVES database, Muller et al.)
"""
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data"
FAO_TO_CROP = {
    "Maize (corn)": "corn", "Wheat": "winter_wheat", "Barley": "barley", "Sorghum": "sorghum", "Soya beans": "soybeans",
    "Sunflower seed": "sunflower", "Seed cotton, unginned": "cotton", "Peas, dry": "field_peas",
    "Chick peas, dry": "field_peas", "Lentils, dry": "field_peas", "Oats": "oats", "Rape or colza seed": "canola",
    "Millet": "proso_millet", "Triticale": "triticale", "Rye": "triticale", "Beans, dry": "dry_beans",
    "Groundnuts, excluding shelled": "peanuts", "Potatoes": "potatoes", "Tomatoes": "tomatoes",
    "Sugar beet": "sugarbeets", "Rice": "rice", "Sugar cane": "sugarcane",
    "Onions and shallots, dry (excluding dehydrated)": "vegetables", "Cabbages": "vegetables",
    "Lettuce and chicory": "vegetables", "Chillies and peppers, green (Capsicum spp. and Pimenta spp.)": "vegetables",
    "Carrots and turnips": "vegetables", "Cucumbers and gherkins": "vegetables", "Watermelons": "vegetables",
    "Pumpkins, squash and gourds": "vegetables", "Eggplants (aubergines)": "vegetables",
    "Cauliflowers and broccoli": "vegetables", "Other vegetables, fresh n.e.c.": "vegetables",
}
DRIVES_TO_CROP = {
    "corn": "corn", "silage corn": "corn", "soybean": "soybeans", "alfalfa": "alfalfa", "alfalfa mix": "alfalfa",
    "cotton": "cotton", "canola": "canola", "wheat": "winter_wheat", "winter wheat": "winter_wheat",
    "spring wheat": "oats", "spring oats": "oats", "winter oats": "oats", "spring barley": "barley",
    "spring small grain": "oats", "winter small grain": "triticale", "spring triticale": "triticale",
    "pea": "field_peas", "Austrian winter pea": "field_peas", "tomato": "tomatoes", "sorghum": "sorghum",
    "bean": "dry_beans", "sunflower": "sunflower", "fallow": "fallow",
}
DRIVES_COVERS = {"hairy vetch", "winter cereal rye", "cereal rye", "red clover", "crimson clover", "clover mix",
                 "annual grass-legume mix", "annual legume-only mix", "annual grass-legume-forb mix", "radish",
                 "sorghum sudangrass", "sunn hemp", "ryegrass", "winter annual cover crop", "pennycress"}


def country_points(geo: Path) -> dict:
    m49 = {}
    for line in (geo / "countryInfo.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            m49[f[0]] = f[2]
    rows = set()
    for line in (geo / "cities15000.txt").open(encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if f[8] in m49:
            rows.add((round(float(f[4]), 2), round(float(f[5]), 2), int(m49[f[8]])))
    with (OUT / "country_points.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lat", "lon", "m49"])
        w.writerows(sorted(rows))
    return m49


def fao(papers: Path):
    years = [f"Y{y}" for y in range(2020, 2025)]
    acc: dict = defaultdict(lambda: {"area": [], "yield": []})
    names = {}
    with zipfile.ZipFile(papers / "Production_Crops_Livestock_E_All_Data.zip") as z:
        with z.open("Production_Crops_Livestock_E_All_Data_NOFLAG.csv") as fh:
            for r in csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1")):
                code = r["Area Code (M49)"].strip("'")
                if int(r["Item Code"]) >= 1700 or int(r["Area Code"]) >= 5000 or r["Element"] not in (
                        "Area harvested", "Yield"):
                    continue  # aggregates (regions, 'Cereals, primary'…) and other elements
                vals = [float(r[y]) for y in years if r.get(y) not in ("", None) and float(r[y]) > 0]
                if not vals:
                    continue
                key = (int(code), r["Item"])
                names[int(code)] = r["Area"]
                acc[key]["area" if r["Element"] == "Area harvested" else "yield"].append(sum(vals) / len(vals))
    with (OUT / "fao_country_crops.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["m49", "country", "item", "crop_id", "area_ha", "yield_t_ha"])
        for (code, item), v in sorted(acc.items()):
            if not v["area"] or v["area"][0] < 1000:
                continue
            y = v["yield"][0] if v["yield"] else None  # FAOSTAT yield is kg/ha (2024+ files) or hg/ha (older)
            y_t = round(y / 1000, 2) if y and y < 200000 else (round(y / 10000, 2) if y else "")
            w.writerow([code, names[code], item, FAO_TO_CROP.get(item, ""), round(v["area"][0]), y_t])


def drives(papers: Path):
    sites = {r["site_id"]: r for r in csv.DictReader((papers / "site_info.csv").open(encoding="utf-8"))}
    phases = defaultdict(list)
    for r in csv.DictReader((papers / "rotation_phases.csv").open(encoding="utf-8")):
        phases[r["rotation_id"]].append(r)
    rot_info = {r["rotation_id"]: r for r in csv.DictReader((papers / "rotation_id_info.csv").open(encoding="utf-8"))}
    out = []
    for sid, s in sites.items():
        rots = []
        for rid, ph in phases.items():
            if rot_info.get(rid, {}).get("site_id") != sid:
                continue
            ph.sort(key=lambda r: int(r["phase"]))
            seq, covers = [], set()
            for r in ph:
                cid = r["crop_id"]
                if cid in DRIVES_COVERS:
                    covers.add(cid)
                elif DRIVES_TO_CROP.get(cid) and (not seq or seq[-1] != DRIVES_TO_CROP[cid] or True):
                    seq.append(DRIVES_TO_CROP[cid])
            if seq:
                rots.append({"id": rid, "years": seq, "covers": sorted(covers)})
        uniq = {(tuple(r["years"]), tuple(r["covers"])): r for r in rots}
        try:
            lat, lon = float(s["lat_dd"]), float(s["lon_dd"])
        except ValueError:
            continue
        out.append({"site_id": sid, "name": s["site_full_name"], "institution": s["institution"],
                    "city": s["city"], "state": s["state"], "country": s["country"], "start_year": s["start_year"],
                    "lat": lat, "lon": lon, "map_mm": float(s["map_mm"]) if s["map_mm"] else None,
                    "mat_c": float(s["mat_celsius"]) if s["mat_celsius"] else None,
                    "doi": s.get("citation_doi") or "", "rotations": list(uniq.values())})
    (OUT / "drives_sites.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    papers, geo = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    country_points(geo)
    fao(papers)
    sites = drives(papers)
    print("country points, FAO crops and", len(sites), "DRIVES sites written to", OUT)
