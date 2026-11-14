from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app)


def test_landcheck_classes():
    from app.data import landcheck
    feats = lambda cats, elev=500.0: {"elevation_m": elev, "rotation": {"sequence": [  # noqa: E731
        {"year": 2020 + i, "crop": None, "category": c} for i, c in enumerate(cats)]}}
    assert landcheck.problem(feats([], 0.0)) == "open water"                   # Pacific: no crop map, sea level
    assert landcheck.problem(feats([], -42.0)) is None                         # Imperial Valley, below sea level
    assert landcheck.problem(feats(["Corn", "Developed/Medium Intensity"])) == "a town or built-up area"
    assert landcheck.problem(feats(["Corn", "Open Water"])) == "open water"
    for ok in ("Corn", "Grassland/Pasture", "Fallow/Idle Cropland"):
        assert landcheck.problem(feats(["Developed/Low Intensity", ok])) is None   # latest year decides


def test_pin_in_the_ocean_is_reasked(offline, monkeypatch):
    from app.data import landcheck
    from app.db import Farmer, session
    answers = iter(["open water", None])  # the first pin is in the sea, the second on a field
    monkeypatch.setattr(landcheck, "problem", lambda f: next(answers))
    c = _client()
    c.post("/api/chat", json={"phone": "sea", "text": "hi"})
    msgs = c.post("/api/chat", json={"phone": "sea", "lat": 35.0, "lon": -125.5}).json()["messages"]
    assert "open water" in msgs[-1] and "not a farm field" in msgs[-1]
    with session() as db:
        fm = db.query(Farmer).filter_by(phone="web:sea").one()
        assert fm.state == "awaiting_location" and not fm.fields
    msgs = c.post("/api/chat", json={"phone": "sea", "lat": 38.05, "lon": -100.75}).json()["messages"]
    assert "Step 2 of 3" in msgs[-1]


def _webhook(c, **form):
    return c.post("/twilio/webhook", data={"From": "whatsapp:+15550001111", **form})


def test_voice_note_is_transcribed_and_answered(offline, monkeypatch):
    from app import media, messaging
    sent = []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    monkeypatch.setattr(media, "download", lambda url: (b"OggS...OpusHead", "audio/ogg"))
    monkeypatch.setattr(media, "transcribe", lambda data, ct: "hi")
    c = _client()
    assert _webhook(c, MediaUrl0="https://api.twilio.com/m/1", MediaContentType0="audio/ogg", NumMedia="1").status_code == 200
    assert sent[0] == "🎙️ I heard: “hi”" and "Step 1 of 3" in sent[1]


def test_unclear_voice_note(offline, monkeypatch):
    from app import media, messaging
    sent = []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    monkeypatch.setattr(media, "download", lambda url: (b"x", "audio/ogg"))
    monkeypatch.setattr(media, "transcribe", lambda data, ct: None)
    _webhook(_client(), MediaUrl0="u", MediaContentType0="audio/ogg", NumMedia="1")
    assert "couldn't make out" in sent[0]


def test_photo_before_and_after_onboarding(offline, monkeypatch):
    from app import agent, media, messaging
    from app.db import Farmer, session
    sent, seen = [], []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    monkeypatch.setattr(media, "download", lambda url: (b"\xff\xd8jpeg", "image/jpeg"))
    c = _client()
    _webhook(c, MediaUrl0="u", MediaContentType0="image/jpeg", NumMedia="1", Body="what is this?")
    assert "once your field is set up" in sent[-1]
    with session() as db:
        db.query(Farmer).filter_by(phone="whatsapp:+15550001111").one().state = "ready"
        db.commit()
    monkeypatch.setattr(agent, "answer", lambda f, t, image=None: seen.append((t, image["type"])) or "Looks like aphids.")
    _webhook(c, MediaUrl0="u", MediaContentType0="image/jpeg", NumMedia="1", Body="what is this?")
    assert seen == [("what is this?", "image")] and sent[-1] == "Looks like aphids."


def test_photo_block_and_opus_rate():
    from app import media
    assert media.image_block(b"x", "image/jpeg")["source"]["media_type"] == "image/jpeg"
    assert media.image_block(b"x", "image/heic") is None
    head = b"OggS" + b"\0" * 20 + b"OpusHead" + bytes([1, 1, 0, 0]) + (48000).to_bytes(4, "little")
    assert media._opus_rate(head) == 48000 and media._opus_rate(b"none") == 16000


def test_crop_step_unknown_crop_and_irrigation_alone(offline, monkeypatch):
    from app.db import Farmer, session
    c = _client()
    say = lambda **kw: c.post("/api/chat", json={"phone": "cane", **kw}).json()["messages"]  # noqa: E731
    say(text="hi")
    say(lat=38.05, lon=-100.75)
    with session() as db:  # pretend the crop map saw nothing here
        fm = db.query(Farmer).filter_by(phone="web:cane").one()
        fm.fields[-1].current_rotation = None
        db.commit()
    assert "I don't have “Sugarcane”" in say(text="Sugarcane")[0]
    out = say(text="Dryland")[0]
    assert out.startswith("👍 Dryland — noted.") and "Add" not in out
    out = say(text="Corn")
    assert "Continuous corn (dryland)" in out[0] and "Step 3 of 3" in out[1]
