"""Team page data. Edit the six entries below; photos go in app/static/team/ (square or 4:5 JPG/PNG, ~800px).

Leave "photo" empty (or point it at a file that doesn't exist yet) and the page shows a pixel placeholder
with the person's initials. Links are optional: drop any you don't want to show.
"""
from app import config

PHOTO_DIR = config.ROOT / "app" / "static" / "team"

TEAM = [
    {"name": "Iker Gonzalez", "role": "Cloud, Data & AI Lead", "profession": "Cloud, Data & AI specialist",
     "photo": "member-01.jpg",
     "bio": "Helping businesses do more with Cloud, Data & AI. He built FarmPartner's entire digital "
            "infrastructure and the AI model at its core, from the cloud setup to the assistant that turns NASA "
            "data into plain-language advice, with an eye on doing it responsibly.",
     "links": {"GitHub": "https://github.com/iker-gonzalez"}},
    {"name": "Israel Conde C.", "role": "Storyboard & Design", "profession": "Industrial Mechatronics student",
     "photo": "member-02.jpg",
     "bio": "Industrial mechatronics student who gave FarmPartner its visual story. He designed the storyboard "
            "and the look of the project, translating satellites, data and fields into images anyone can "
            "understand at a glance. He also helped shape the product itself, bringing ideas that grew into "
            "features of the app.",
     "links": {}},
    {"name": "Janire Martinez", "role": "Agri Data & Research", "profession": "Mechanical Engineering student",
     "photo": "member-03.jpg",
     "bio": "Mechanical engineering student who turned two decades of satellite records and scientific "
            "literature into insight for farmers. She dug into NASA datasets and agricultural research papers to "
            "understand what the data can (and can't) tell us about a field, and made sure every recommendation "
            "stands on solid evidence.",
     "links": {}},
    {"name": "Oier Muñoz", "role": "Measurement & Growth", "profession": "Electronic Engineering student",
     "photo": "member-04.jpg",
     "bio": "Electronics engineering student working on both ends of the problem: how we measure the field and "
            "how we reach the farmer. He refined the measurement techniques behind FarmPartner's indicators and "
            "shaped the way we tell its story, so the product is accurate and easy to discover.",
     "links": {}},
    {"name": "June Ramirez", "role": "Challenge Strategy",
     "profession": "Production Planning in Mechanical Manufacturing student", "photo": "member-05.jpg",
     "bio": "Production planning student in mechanical manufacturing who gave the project its backbone. She "
            "broke the NASA Field Shift challenge down into clear problems, priorities and steps, and built the "
            "structure that kept the team focused on what farmers actually need.",
     "links": {}},
    {"name": "Ruben Ramirez", "role": "Web Design & Development", "profession": "Mechanical Engineer & Physicist",
     "photo": "member-06.jpg",
     "bio": "Mechanical engineer and physicist who built the face of FarmPartner on the web. He designed the "
            "website so complex science reads simply, combining an engineer's precision with a clean, accessible "
            "experience for farmers and partners alike.",
     "links": {}},
]


def members() -> list[dict]:
    """TEAM entries ready for the template: photo URL only if the file exists, initials, non-empty links."""
    out = []
    for m in TEAM:
        photo = m.get("photo") or ""
        initials = "".join(w[0] for w in m["name"].split()[:2] if w[:1].isalpha()).upper() or m["name"][:2].upper()
        out.append({**m,
                    "photo_url": f"/static/team/{photo}" if photo and (PHOTO_DIR / photo).is_file() else None,
                    "initials": initials,
                    "links": {k: v for k, v in (m.get("links") or {}).items() if v}})
    return out
