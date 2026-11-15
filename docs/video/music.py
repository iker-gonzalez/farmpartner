"""Original background bed for the video (no licence needed): a soft synth pad, a quiet plucked arpeggio and a sub
bass, 30 s. Run: uv run --with numpy python docs/video/music.py"""
import pathlib, wave
import numpy as np

SR, DUR, BPM = 48000, 30.0, 96
t = np.arange(int(SR * DUR)) / SR
N = lambda n: 440.0 * 2 ** ((n - 69) / 12)          # MIDI note → Hz
# F major, hopeful: Fmaj7 → C/E → Dm7 → Bbmaj7 → Fmaj7 (home for the logo)
CHORDS = [(0.0, [53, 60, 64, 69]), (6.3, [52, 60, 67, 72]), (12.5, [50, 60, 65, 69]), (18.75, [46, 58, 62, 69]),
          (25.6, [53, 60, 65, 69, 72])]
out = np.zeros((2, t.size))


def env(start, end, attack, release):
    return np.clip((t - start) / attack, 0, 1) * np.clip((end - t) / release, 0, 1)


for i, (start, notes) in enumerate(CHORDS):
    end = CHORDS[i + 1][0] + 1.2 if i + 1 < len(CHORDS) else DUR
    e = env(start - 0.6, end, 1.6, 1.8)
    for n in notes:                                   # pad: detuned sines, a little brighter on top, wide stereo
        f = N(n)
        for ch, det in ((0, 0.997), (1, 1.003)):
            out[ch] += e * 0.045 * (np.sin(2 * np.pi * f * det * t) + 0.35 * np.sin(2 * np.pi * 2 * f * det * t + 0.5)
                                    + 0.12 * np.sin(2 * np.pi * 3 * f * det * t))
    out += e * 0.10 * np.sin(2 * np.pi * N(notes[0] - 12) * t)       # sub
    step = 60 / BPM / 2                                               # eighth-note arpeggio, enters after the intro
    k = 0
    while start + k * step < min(end - 1.2, DUR - 1.5):
        at = start + k * step
        if at >= 6.3:
            n = (notes + [notes[1] + 12])[[0, 2, 1, 3, 2, 4, 1, 3][k % 8] % (len(notes) + 1) if len(notes) == 4 else k % 5]
            f, dt = N(n + 12), t - at
            pl = np.where(dt >= 0, np.exp(-np.clip(dt, 0, None) * 5.5), 0) * (np.sin(2 * np.pi * f * dt) + 0.3 * np.sin(4 * np.pi * f * dt))
            pan = 0.35 + 0.3 * (k % 2)
            out[0] += pl * 0.035 * (1 - pan); out[1] += pl * 0.035 * pan
        k += 1

out *= np.clip(t / 1.2, 0, 1) * np.clip((DUR - t) / 1.6, 0, 1)        # fade in / out
out /= np.abs(out).max() / 0.7
pcm = (out.T * 32767).astype("<i2")
path = pathlib.Path(__file__).parent / "build" / "music.wav"
with wave.open(str(path), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
print(path, round(DUR, 1), "s")
