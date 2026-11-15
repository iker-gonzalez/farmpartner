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
    monkeypatch.setattr(media, "transcribe", lambda data, ct, langs=None: "hi")
    c = _client()
    assert _webhook(c, MediaUrl0="https://api.twilio.com/m/1", MediaContentType0="audio/ogg", NumMedia="1").status_code == 200
    assert sent[0] == "🎙️ I heard: “hi”" and "Step 1 of 3" in sent[1]


def test_unclear_voice_note(offline, monkeypatch):
    from app import media, messaging
    sent = []
    monkeypatch.setattr(messaging, "send", lambda to, m: sent.append(m))
    monkeypatch.setattr(media, "download", lambda url: (b"x", "audio/ogg"))
    monkeypatch.setattr(media, "transcribe", lambda data, ct, langs=None: None)
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
    out = say(text="Quinoa")[0]   # without Claude we can't tell a crop from anything else: never echo it
    assert "didn't recognise a crop" in out and "Quinoa" not in out
    out = say(text="Dryland")[0]
    assert out.startswith("👍 Dryland — noted.") and "Add" not in out
    out = say(text="Corn")
    assert "you grow corn (dryland)" in out[0] and "Step 3 of 3" in out[1]


def test_typing_indicator_while_slow_work(monkeypatch):
    import time

    from app import messaging
    calls = []
    monkeypatch.setattr(messaging, "typing", lambda sid: calls.append(sid))
    with messaging.typing_while("SM1", every_s=0.05, first_after_s=0.01):
        time.sleep(0.2)
    n = len(calls)
    time.sleep(0.15)
    assert n >= 2 and set(calls) == {"SM1"} and len(calls) == n  # refreshed while working, stops after
    with messaging.typing_while(None):
        pass


def test_location_from_text(monkeypatch):
    from app.data import maplinks
    f = maplinks.location_from_text
    assert f("36.314251,-98.116206") == (36.314251, -98.116206)
    assert f(" 36.31 , -98.11 ") == (36.31, -98.11)
    assert f("https://www.google.com/maps/place/36.3,-98.1/data=!3m5!3d36.3142512!4d-98.1162062") == (36.3142512, -98.1162062)
    assert f("look: https://maps.apple.com/?ll=38.05,-100.75&q=Pin") == (38.05, -100.75)
    assert f("https://evil.example/?q=1.0,2.0") is None
    assert f("67846") is None and f("got 1.2, maybe 1.5 inches") is None
    monkeypatch.setattr(maplinks, "_resolve", lambda url: "https://www.google.com/maps/place/@38.05,-100.75,15z")
    assert f("https://maps.app.goo.gl/abc") == (38.05, -100.75)


def test_pasted_coordinates_and_duplicate_pins(offline):
    c = _client()
    say = lambda **kw: c.post("/api/chat", json={"phone": "dup", **kw}).json()["messages"]  # noqa: E731
    say(text="hi")
    assert "Step 2 of 3" in say(text="38.05, -100.75")[-1]          # typed coordinates work as a pin
    assert say(lat=38.05, lon=-100.75) == []                         # the same pin delivered twice: ignored
    assert "Save water" in "\n".join(say(text="yes dryland"))        # and the crop step is undisturbed


def test_favicon(offline):
    r = _client().get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_place_on_the_equator(monkeypatch):
    from app.data import geocode
    monkeypatch.setattr(geocode, "get_json", lambda *a, **k: {"results": [
        {"name": "Islas galápagos", "longitude": -90.5, "country_code": "EC", "admin1": "Galápagos"}]})
    assert geocode.place_to_point("Islas Galapago", "es") == (0.0, -90.5, "Islas galápagos, Galápagos, EC")


def test_degree_minute_second_coordinates():
    from app.data.maplinks import location_from_text as f
    lat, lon = f('En estas coordenadas 31°45\'12.0"N 42°18\'34.0')
    assert round(lat, 3) == 31.753 and round(lon, 3) == 42.309
    assert f("38° 3' N, 100° 45' W") == (38.05, -100.75)
    assert f("33°34′30″S 70°39′W") == (-33.575, -70.65)
    assert f("hace 31° de calor") is None


def test_voice_replies(offline, monkeypatch, tmp_path):
    from app import config, media, messaging, voice
    from app.db import Farmer, session
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "synthesize", lambda text, lang: b"OggS" + text.encode()[:20])
    texts, media_sent = [], []
    monkeypatch.setattr(messaging, "send", lambda to, m: texts.append(m))
    monkeypatch.setattr(messaging, "send_media", lambda to, url: media_sent.append(url))
    c = _client()
    hook = lambda **f: c.post("/twilio/webhook", data={"From": "whatsapp:+15550007777", **f})  # noqa: E731
    hook(Body="hi")
    assert media_sent == []                                         # text farmer: text only
    hook(Body="please reply with voice notes")
    assert texts[-2].startswith("🔊 Done") and len(media_sent) == 1  # asked for audio: confirmation is spoken too
    with session() as db:
        assert db.query(Farmer).filter_by(phone="whatsapp:+15550007777").one().constraints["voice_replies"] is True
    clip = media_sent[-1].rsplit("/", 1)[1]
    r = c.get(f"/voice/{clip}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content.startswith(b"OggS")
    assert c.get("/voice/../../etc.ogg").status_code == 404
    hook(Body="text only")
    n = len(media_sent)
    hook(Body="hi")
    assert len(media_sent) == n                                      # turned off
    # a voice note gets a voice note back even when the preference is off
    monkeypatch.setattr(media, "download", lambda url: (b"OggS", "audio/ogg"))
    monkeypatch.setattr(media, "transcribe", lambda data, ct, langs=None: "hi")
    hook(MediaUrl0="u", MediaContentType0="audio/ogg", NumMedia="1")
    assert len(media_sent) == n + 1
    assert voice.speakable(["🛰️ *Rain*: ~2 in. Report: https://x.y/r/1", {"text": "Pick one", "buttons": []}]) == \
        "Rain: 2 in. Report: Pick one"


def test_browser_demo_voice_photo_and_locale(offline, monkeypatch, tmp_path):
    """The /chat demo works like WhatsApp: voice notes in and out, photos, and the browser's language and units."""
    import base64

    from app import agent, config, media, voice
    from app.db import Farmer, session
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "synthesize", lambda text, lang: b"OggS" + text.encode()[:20])
    c = _client()
    # first visit from a Spanish browser: Spanish and metric
    j = c.post("/api/chat", json={"phone": "loc1", "text": "hi", "wait": True, "locale": "es-ES"}).json()
    assert j["audio"] == []
    with session() as db:
        fm = db.query(Farmer).filter_by(phone="web:loc1").one()
        assert fm.language == "es" and fm.constraints["units"] == "metric"
    # a voice note: transcribed, echoed with "I heard", answered, and answered aloud (a /voice/ clip)
    monkeypatch.setattr(media, "transcribe", lambda data, ct, langs=None: "hola" if ct == "audio/wav" else None)
    wav = base64.b64encode(b"RIFF....WAVEfmt ").decode()
    j = c.post("/api/media", json={"phone": "v1", "kind": "audio", "data": wav, "content_type": "audio/wav",
                                   "wait": True}).json()
    assert "hola" in j["messages"][0] and j["messages"][0].startswith("🎙️")
    assert len(j["messages"]) > 1 and len(j["audio"]) == 1 and j["audio"][0].startswith("/voice/")
    assert c.get(j["audio"][0]).content.startswith(b"OggS")
    # unclear note
    j = c.post("/api/media", json={"phone": "v1", "kind": "audio", "data": wav, "content_type": "audio/webm"}).json()
    assert "couldn't make out" in j["messages"][0]
    # a photo after onboarding goes to Claude with the image
    seen = []
    monkeypatch.setattr(agent, "answer", lambda f, t, image=None: seen.append(image["type"]) or "Looks like aphids.")
    c.post("/api/chat", json={"phone": "p1", "text": "hi", "wait": True})
    with session() as db:
        db.query(Farmer).filter_by(phone="web:p1").one().state = "ready"
        db.commit()
    jpg = base64.b64encode(b"\xff\xd8jpeg").decode()
    j = c.post("/api/media", json={"phone": "p1", "kind": "image", "data": jpg, "content_type": "image/jpeg",
                                   "caption": "what is this bug?", "wait": True}).json()
    assert seen == ["image"] and any("aphids" in m for m in j["messages"])
    assert c.post("/api/media", json={"phone": "p1", "kind": "video", "data": jpg, "content_type": "x"}).status_code == 400


def test_transcript_skips_silent_stretches(monkeypatch):
    from app import media

    class R:
        status_code = 200

        def json(self):
            return {"results": [{"alternatives": [{}]}, {"alternatives": [{"transcript": " should I water "}]}, {}]}

    monkeypatch.setattr(media.httpx, "post", lambda *a, **k: R())
    monkeypatch.setattr(media, "_google_headers", lambda: {})
    assert media.transcribe(b"RIFF\x00\x00\x00\x00WAVE", "audio/wav", ["en"]) == "should I water"
    R.json = lambda self: {"results": [{"alternatives": [{}]}]}
    assert media.transcribe(b"x", "audio/wav", ["en"]) is None


def test_coordinates_with_words_and_reply_read_in_its_own_language():
    from app.data.maplinks import location_from_text
    from app.main import _spoken_lang
    assert location_from_text("43.416425, -2.914102 \n ahora mismo no tengo cultivos") == (43.416425, -2.914102)
    assert location_from_text("we got 1.5, 2.0 inches of rain") is None
    assert _spoken_lang("قارنتُ دورتك الزراعية مع بدائل تناسب مناخ حقلك", "es") == "ar"
    assert _spoken_lang("ok", "es") == "es"


def test_questions_are_not_places_and_ha_is_not_hectares(offline, monkeypatch):
    """From a live chat: "Quién soy?" was geocoded to Quien Sabe (Chiapas); "se me ha olvidado" switched to metric."""
    from app import onboarding, units
    assert units.asked("Me dices mi número de teléfono que se me ha olvidado") is None
    assert units.asked("en hectáreas por favor") == "metric"
    called = []
    monkeypatch.setattr(onboarding, "place_to_point", lambda t, *a: called.append(t) or (16.0, -92.0, "Quien Sabe, MX"))
    c = _client()
    say = lambda t: c.post("/api/chat", json={"phone": "q1", "text": t, "wait": True}).json()["messages"]  # noqa: E731
    say("hi")
    out = say("Quién soy?")
    assert called == [] and not any("NASA" in m and "Quien Sabe" in m for m in out)
