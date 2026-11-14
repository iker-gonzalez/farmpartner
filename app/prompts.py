"""Messages with tap-able choices.

A chat message is either a plain string or a prompt dict:
  {"text": "<one line>", "buttons": [{"id": ..., "title": ...}, ...]}             WhatsApp quick replies (max 3)
  {"text": "<one line>", "list": {"button": ..., "items": [{"id", "title", "description"}]}}   list picker (max 10)

The id of a tapped choice comes back as the farmer's message, so ids are phrases the onboarding/agent already
understand ("yes irrigated", "1", "report"). Everything that cannot render choices (SMS, the database log,
tests) uses as_text(), which lists the options as numbered or bracketed text.
"""


def buttons(text: str, *choices: tuple[str, str]) -> dict:
    assert len(choices) <= 3 and "\n" not in text
    return {"text": text, "buttons": [{"id": i, "title": t[:20]} for i, t in choices]}


def picker(text: str, button: str, *items: tuple[str, str, str]) -> dict:
    assert len(items) <= 10 and "\n" not in text
    return {"text": text, "list": {"button": button[:20],
                                   "items": [{"id": i, "title": t[:24], "description": d[:72]} for i, t, d in items]}}


def is_prompt(m) -> bool:
    return isinstance(m, dict)


def as_text(m) -> str:
    if not is_prompt(m):
        return m
    if "buttons" in m:
        return m["text"] + "\n" + "  ".join(f"[{b['title']}]" for b in m["buttons"])
    lines = [m["text"]] + [f"{it['id']}️⃣ {it['title']}" + (f" — {it['description']}" if it["description"] else "")
                           for it in m["list"]["items"]]
    return "\n".join(lines)
