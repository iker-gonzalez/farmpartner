"""Voice-over for the 30-second video: one clip per scene, so scenes can be timed to it.
Run: python3 docs/video/vo.py
  ELEVENLABS_API_KEY set  → ElevenLabs (voice: ELEVENLABS_VOICE_ID, default "George")
  otherwise               → Google Text-to-Speech Chirp 3 HD (needs `gcloud auth login`)"""
import base64, json, os, pathlib, subprocess, urllib.request

LINES = [
    "A new generation is taking over the farm. With hotter summers, new pests, and tired soil.",
    "FarmPartner is their advisor. Right in the chat.",
    "Drop a pin. Our AI reads twenty years of NASA data for that field, and compares your crop rotation "
    "with better options. With the evidence, and the risks.",
    "No forms. Type, or just talk, in forty-six languages.",
    "FarmPartner. Your field, read from space.",
]
out = pathlib.Path(__file__).parent / "build"
out.mkdir(exist_ok=True)


def eleven(text: str) -> bytes:
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_192",
        json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.25}}).encode(),
        {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    return urllib.request.urlopen(req).read()


def google(text: str) -> bytes:
    tok = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    body = {"input": {"text": text}, "voice": {"languageCode": "en-US", "name": os.environ.get("GOOGLE_VOICE", "en-US-Chirp3-HD-Charon")},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000}}
    req = urllib.request.Request("https://texttospeech.googleapis.com/v1/text:synthesize", json.dumps(body).encode(),
                                 {"Authorization": "Bearer " + tok, "x-goog-user-project": "launchpad-all-dev-01",
                                  "Content-Type": "application/json"})
    return base64.b64decode(json.load(urllib.request.urlopen(req))["audioContent"])


use_eleven = bool(os.environ.get("ELEVENLABS_API_KEY"))
print("voice:", "ElevenLabs" if use_eleven else "Google Chirp 3 HD")
for i, line in enumerate(LINES):
    raw = out / (f"vo{i}.mp3" if use_eleven else f"vo{i}.raw.wav")
    raw.write_bytes(eleven(line) if use_eleven else google(line))
    # trim leading/trailing silence so timing is exact
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af",
                    "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                    "-ar", "48000", "-ac", "1", str(out / f"vo{i}.wav")], check=True)
    dur = float(subprocess.check_output(["ffprobe", "-v", "0", "-show_entries", "format=duration", "-of", "csv=p=0",
                                         str(out / f"vo{i}.wav")], text=True))
    print(i, round(dur, 2), line[:60])
