#!/usr/bin/env python3
"""Synthesise the console's ten notification sounds into app/static/sounds/*.mp3.

Made from scratch (sines, envelopes, a little noise): no sample, no third-party recording, so the files are free to use
for anything, without attribution (CC0). Needs numpy and ffmpeg (with libmp3lame); run it only to change the sounds:

  python3 app/tools/make_sounds.py
"""
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "static" / "sounds"


def t(sec):
    return np.arange(int(RATE * sec)) / RATE


def env(n, attack=0.004, decay=0.4):
    x = np.arange(n) / RATE
    a = np.clip(x / attack, 0, 1)
    return a * np.exp(-x / decay)


def tone(freq, sec, decay=0.4, partials=((1, 1.0),), attack=0.004, detune=0.0):
    x = t(sec)
    y = sum(amp * np.sin(2 * np.pi * freq * ratio * (1 + detune * i) * x) for i, (ratio, amp) in enumerate(partials))
    return y * env(len(x), attack, decay)


def place(total, parts):
    out = np.zeros(int(RATE * total))
    for start, y in parts:
        i = int(RATE * start)
        out[i:i + len(y)] += y[: len(out) - i]
    return out


BELL = ((1, 1.0), (2.76, 0.45), (5.4, 0.22), (8.93, 0.1))
MARIMBA = ((1, 1.0), (3.9, 0.3), (9.2, 0.06))
GLASS = ((1, 1.0), (2.0, 0.3), (3.01, 0.12))


def bell():
    return tone(880, 1.6, 0.55, BELL)


def ding_dong():
    return place(1.6, [(0, tone(784, 0.9, 0.38, GLASS)), (0.32, tone(622.25, 1.2, 0.5, GLASS))])


def marimba():
    return place(0.9, [(0, tone(523.25, 0.5, 0.13, MARIMBA)), (0.14, tone(783.99, 0.7, 0.16, MARIMBA))])


def drop():
    x = t(0.35)
    f = 600 + 1500 * (x / x[-1]) ** 1.6
    y = np.sin(2 * np.pi * np.cumsum(f) / RATE) * env(len(x), 0.002, 0.07)
    return place(0.5, [(0, y), (0.16, 0.35 * y[: int(RATE * 0.2)])])


def chimes():
    notes = (659.25, 783.99, 1046.5)
    return place(1.8, [(i * 0.17, tone(f, 1.3, 0.5, BELL)) for i, f in enumerate(notes)])


def bubble():
    x = t(0.18)
    f = 300 + 900 * (x / x[-1])
    y = np.sin(2 * np.pi * np.cumsum(f) / RATE) * env(len(x), 0.003, 0.05)
    return place(0.4, [(0, y), (0.11, 0.6 * y)])


def arpeggio():
    notes = (523.25, 659.25, 783.99, 1046.5)
    return place(1.4, [(i * 0.09, tone(f, 0.9, 0.3, GLASS)) for i, f in enumerate(notes)])


def small_bell():
    rng = np.random.default_rng(7)
    parts = [(0.05 * i + rng.uniform(0, 0.02), tone(f, 0.8, 0.25, BELL) * 0.7)
             for i, f in enumerate((1568, 1975.5, 2349.3, 1975.5))]
    return place(1.2, parts)


def xylophone():
    return place(0.7, [(0, tone(1046.5, 0.35, 0.08, MARIMBA)), (0.11, tone(1318.5, 0.5, 0.1, MARIMBA))])


def chord():
    x = t(1.6)
    tones = sum(np.sin(2 * np.pi * f * x) for f in (392.0, 493.88, 587.33, 783.99))
    shape = np.clip(x / 0.25, 0, 1) * np.exp(-np.clip(x - 0.25, 0, None) / 0.5)
    return tones * shape * 0.5


SOUNDS = {"bell": bell, "ding-dong": ding_dong, "marimba": marimba, "drop": drop, "chimes": chimes,
          "bubble": bubble, "arpeggio": arpeggio, "small-bell": small_bell, "xylophone": xylophone, "chord": chord}


def write(name, y):
    y = y / (np.max(np.abs(y)) or 1) * 0.7  # about -3 dBFS
    fade = int(RATE * 0.02)
    y[-fade:] *= np.linspace(1, 0, fade)
    pcm = (y * 32767).astype("<i2")
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", f.name, "-codec:a", "libmp3lame", "-b:a", "96k",
                        "-map_metadata", "-1", "-id3v2_version", "3", "-metadata", f"title={name}",
                        "-metadata", "copyright=CC0: made for mmbox console", str(OUT / f"{name}.mp3")], check=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for n, fn in SOUNDS.items():
        write(n, fn())
        print(OUT / f"{n}.mp3")
