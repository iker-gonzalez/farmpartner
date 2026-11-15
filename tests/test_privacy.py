"""Identifiers typed by farmers never reach Claude; NASA POWER only gets the ~50 km grid cell."""
from app.data.power import grid_lat, grid_lon
from app.privacy import scrub


def test_scrub_removes_identifiers_and_keeps_farm_talk():
    assert scrub("call me on +34 600 111 222 or 600-111-222") == "call me on [phone number] or [phone number]"
    assert scrub("mail iker@example.com") == "mail [email]"
    assert scrub("43.416425, -2.914102 ahora no tengo cultivos") == "[coordinates] ahora no tengo cultivos"
    assert scrub("aquí https://maps.app.goo.gl/QnXv392TbfRwY3Gf6?g_st=aw mi campo") == "aquí [map link] mi campo"
    assert scrub("31°45'12\"N 42°18'34\"E") == "[coordinates]"
    for kept in ("we got 1.5, 2.0 inches of rain", "planted 2026-11-15", "1,500 acres of wheat, ZIP 67846",
                 "yield 3.2 t/ha in 2024", "report: https://farmpartner.space/r/abc"):
        assert scrub(kept) == kept, kept


def test_power_grid_cell():
    assert (grid_lat(38.05), grid_lon(-100.75)) == (38.0, -100.625)
    assert (grid_lat(43.416425), grid_lon(-2.914102)) == (43.5, -3.125)
    assert (grid_lat(-0.2), grid_lon(179.9)) == (0.0, 180.0)


def test_history_sent_to_claude_is_scrubbed(offline):
    from app import agent, onboarding
    from app.db import Farmer, session
    with session() as db:
        fm = Farmer(phone="web:scrub1", channel="web", priorities=[], constraints={})
        db.add(fm); db.commit(); fid = fm.id
    onboarding.record(fid, "in", "my number is +34 600 111 222, field at 41.6512, -0.8773")
    turns = agent._history(fid)
    assert turns[-1]["content"] == "my number is [phone number], field at [coordinates]"
