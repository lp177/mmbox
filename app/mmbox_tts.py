#!/usr/bin/env python3
"""Messages read aloud by the mmbox console (optional, standard library only, besides GStreamer for playback).

A message turns into speech only when it is played, never before: by its Play button, or because it reached a box,
a project, a machine or a service the operator asked to hear (Settings, or the item's ⋮ menu), or the operator box.
No audio is made for a message nobody plays. What is said: the project, who writes to whom, the subject, then the
body (without its code blocks, links read as their site), in the message's language (French or English, guessed).
The audio is kept (tts/cache in the console's folder: 30 days, 300 MB at most) so that it plays again, seeks and
changes speed at no cost.

Voices: Mistral (Voxtral TTS) and Gradium, with the operator's API keys (kept in the system's keyring, as the operator
key), or a local engine installed on demand (Piper, in a private venv, its voices downloaded once), offline.

Playback is this computer's own (GStreamer, the library WebKitGTK plays with): the page's player (play, pause, seek,
±10 s, speed) drives it, the desktop's media controls show it (MPRIS, as for a music player), and a message read by
itself shows a notification with Pause and Stop (the console's Desktop does that, with `on_change`).
"""
from __future__ import annotations

import hashlib, io, json, os, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
import wave
from pathlib import Path

NOWIN = 0x08000000 if os.name == "nt" else 0
CACHE_KEEP_DAYS = 30  # the audio made is kept this long after it was last played (Settings)
CACHE_MAX_MB = 300  # and takes this much at most: past it, the least recently played go first (Settings)
CACHE_KEEP_S = CACHE_KEEP_DAYS * 86400
CACHE_MAX_BYTES = CACHE_MAX_MB * 1000 * 1000
MAX_CHARS_DEFAULT = 6000  # what one message reads at most (about six minutes): the rest is said to be skipped
AUTO_FRESH_S = 900  # a message is read by itself only when it is this recent (never the backlog of a restart)
RATES = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
# A cloud voice that fails (quota, key, network) hands over to the next one (the other key, then the local engine).
# It is tested again only when a reading is asked meanwhile, in parallel with that reading, one test at a time and at
# most once every UPGRADE_TEST_S: nothing is tested while nothing is read.
UPGRADE_TEST_S = float(os.environ.get("MMBOX_TTS_UPGRADE_TEST_S", "30"))
QUOTA_WORDS = ("quota", "credit", "insufficient", "billing", "payment", "exceeded", "limit")


def is_quota(err: str) -> bool:
    """A refusal for want of quota or credits (rather than a bad key or no network): only a real synthesis tells
    whether it is over, a key check would pass."""
    e = str(err).lower()
    return any(c in e for c in ("http 402", "http 429")) or any(w in e for w in QUOTA_WORDS)
ENGINES = ("mistral", "gradium", "local")
ENGINE_NAMES = {"mistral": "Mistral (Voxtral)", "gradium": "Gradium", "local": "Local (Piper)"}
SETTINGS_DEFAULT = {"engine": "", "show_play": False, "auto_operator": False, "auto": {}, "voices": {}, "rate": 1.0,
                    "cache_keep_days": CACHE_KEEP_DAYS, "cache_max_mb": CACHE_MAX_MB,
                    "max_chars": MAX_CHARS_DEFAULT, "lang": "auto"}


def log(*a):
    print("console: tts:", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- what is said
FR_WORDS = set("""le la les des une un est et pour que qui pas dans sur avec ce cette ces sont du au aux mais ou donc
je tu il elle nous vous ils elles on ne plus fait faire être avoir été très tout tous rien peut doit sans comme quand
déjà aussi alors même encore voici voilà merci bonjour oui non""".split())
EN_WORDS = set("""the and is are to of for that with this it be on not have has was were you we they an as at by from
or but will can should would could been done also just there their what when which who all any some no yes please
thanks hello""".split())
WORDS = {
    "fr": {"project": "Projet {}.", "from_to": "Message de {}, pour {}.", "from": "Message de {}.", "you": "vous",
           "subject": "Sujet : {}.", "code": "(bloc de code non lu)", "link": "lien vers {}", "cut": "La suite du message n'est pas lue.",
           "kinds": {"task": "Tâche", "request": "Demande", "reply": "Réponse", "answer": "Réponse", "info": "Information",
                     "question": "Question", "recap": "Bilan de fin de travail", "forward": "Message transmis"},
           "no_body": "Le contenu de ce message n'est pas connu de cet ordinateur."},
    "en": {"project": "Project {}.", "from_to": "Message from {}, to {}.", "from": "Message from {}.", "you": "you",
           "subject": "Subject: {}.", "code": "(code block not read)", "link": "link to {}", "cut": "The rest of the message is not read.",
           "kinds": {"task": "Task", "request": "Request", "reply": "Reply", "answer": "Answer", "info": "Information",
                     "question": "Question", "recap": "End of work recap", "forward": "Forwarded message"},
           "no_body": "This computer does not know the content of this message."},
}


def default_lang() -> str:
    loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "").lower()
    return "fr" if loc.startswith("fr") else "en"


def detect_lang(text: str) -> str:
    """French or English, from the most common words and the accents: good enough to pick a voice and its preamble."""
    sample = text[:5000].lower()
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœæ']+", sample)
    fr = sum(1 for w in words if w in FR_WORDS) + len(re.findall(r"[àâçéèêëîïôûùüœ]", sample)) / 3
    en = sum(1 for w in words if w in EN_WORDS)
    if not fr and not en:
        return default_lang()
    return "fr" if fr >= en else "en"


def clean(text: str, lang: str) -> str:
    """The body as it is said: no code blocks, links as their site, no Markdown marks, paths as their last part."""
    w = WORDS[lang]
    t = str(text or "").replace("\r", "")
    t = re.sub(r"```.*?(?:```|\Z)", f"\n{w['code']}\n", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)  # images
    t = re.sub(r"\[([^\]]+)\]\((?:[^)]*)\)", r"\1", t)  # [text](url): the text
    t = re.sub(r"https?://(?:www\.)?([^/\s)>\]\"']+)[^\s)>\]\"']*", lambda m: w["link"].format(m[1]), t)
    t = re.sub(r"`([^`\n]{1,80})`", r"\1", t)
    t = re.sub(r"`[^`\n]*`", " ", t)
    t = re.sub(r"(?<![\w.])(?:~|\.{1,2})?(?:/[\w.@+-]+){2,}/?", lambda m: m[0].rstrip("/").rsplit("/", 1)[-1], t)
    t = re.sub(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{12,64}\b", " ", t)  # hashes
    out = []
    for line in t.split("\n"):
        s = line.strip()
        s = re.sub(r"^(?:#{1,6}\s+|>\s*|[-*+•]\s+|\d{1,3}[.)]\s+)", "", s)
        s = re.sub(r"^\|?\s*:?-{3,}.*$", "", s)  # a table's rule
        s = s.replace("|", ", ")
        s = re.sub(r"(\*\*|__|~~|\*|(?<!\w)_|_(?!\w))", "", s)
        s = re.sub(r"[ \t]+", " ", s).strip(" ,")
        if not s:
            continue
        if not re.search(r"[.!?:;…]$", s):
            s += "."
        out.append(s)
    return re.sub(r"\s+([.,])", r"\1", re.sub(r"\s+", " ", " ".join(out))).strip()


def speech_text(item: dict, lang_setting: str = "auto", max_chars: int = MAX_CHARS_DEFAULT) -> tuple[str, str]:
    """(text, language) of a message: the project, who writes to whom, its kind and subject, then its body."""
    body = item.get("body")
    sample = " ".join(str(x) for x in (item.get("subject"), body) if x)
    lang = lang_setting if lang_setting in WORDS else detect_lang(sample)
    w = WORDS[lang]
    parts = []
    if item.get("project"):
        parts.append(w["project"].format(item["project"]))
    frm, to = item.get("from_name") or "?", (w["you"] if item.get("to_operator") else item.get("to_name"))
    parts.append(w["from_to"].format(frm, to) if to else w["from"].format(frm))
    kind = w["kinds"].get(item.get("kind") or "")
    subject = clean(item.get("subject") or "", lang).rstrip(".")
    if subject:
        parts.append(f"{kind} : {subject}." if kind and lang == "fr" else f"{kind}: {subject}." if kind else w["subject"].format(subject))
    elif kind:
        parts.append(kind + ".")
    head = " ".join(parts)
    text = clean(body, lang) if isinstance(body, str) and body.strip() else w["no_body"]
    room = max(200, int(max_chars or MAX_CHARS_DEFAULT) - len(head))
    if len(text) > room:
        cut = text[:room]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = (cut[:end + 1] if end > room * 0.6 else cut.rsplit(" ", 1)[0] + ".") + " " + w["cut"]
    return head + " " + text, lang


def chunks(text: str, size: int) -> list[str]:
    """The text in pieces of `size` characters at most, cut between sentences (else between words)."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?…])\s+", text):
        while len(sent) > size:
            cut = sent[:size].rsplit(" ", 1)[0] or sent[:size]
            if cur:
                out.append(cur)
                cur = ""
            out.append(cut)
            sent = sent[len(cut):].lstrip()
        if cur and len(cur) + 1 + len(sent) > size:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return [c for c in out if c.strip()]


# ---------------------------------------------------------------- audio files
class Pcm:
    """Mono 16-bit PCM and its rate: what every engine's audio becomes, so that pieces join into one WAV."""

    def __init__(self, data: bytes, rate: int, channels: int = 1, width: int = 2):
        self.data, self.rate, self.channels, self.width = data, rate, channels, width

    @classmethod
    def from_wav(cls, raw: bytes) -> "Pcm":
        """A WAV, read by its chunks; a streamed one (its sizes left at 0 or 0xFFFFFFFF) is all the data after its
        header."""
        if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
            raise EngineError("not a WAV file")
        i, fmt = 12, None
        while i + 8 <= len(raw):
            cid, size = raw[i:i + 4], int.from_bytes(raw[i + 4:i + 8], "little")
            if cid == b"fmt ":
                tag, ch, rate = int.from_bytes(raw[i + 8:i + 10], "little"), int.from_bytes(raw[i + 10:i + 12], "little"), int.from_bytes(raw[i + 12:i + 16], "little")
                bits = int.from_bytes(raw[i + 22:i + 24], "little")
                if tag not in (1, 0xFFFE) or bits != 16:
                    return decode_to_pcm(raw, ".wav")  # float or 24-bit samples: GStreamer converts them
                fmt = (rate, ch)
            elif cid == b"data":
                if not fmt:
                    break
                end = len(raw) if size in (0, 0xFFFFFFFF) or i + 8 + size > len(raw) else i + 8 + size
                data = raw[i + 8:end]
                data = data[:len(data) - len(data) % (2 * fmt[1])]
                p = cls(data, fmt[0], fmt[1], 2)
                return p.mono()
            i += 8 + size + (size & 1)
        raise EngineError("a WAV file without its audio")

    def mono(self) -> "Pcm":
        if self.channels == 1:
            return self
        import array
        a = array.array("h", self.data)
        if sys.byteorder == "big":
            a.byteswap()
        n = self.channels
        m = array.array("h", (sum(a[i:i + n]) // n for i in range(0, len(a), n)))
        if sys.byteorder == "big":
            m.byteswap()
        return Pcm(m.tobytes(), self.rate, 1, 2)

    def seconds(self) -> float:
        return len(self.data) / float(self.rate * self.channels * self.width or 1)


def join_wav(pieces: list[Pcm], path: Path, gap_s: float = 0.25) -> float:
    """The pieces in one WAV, a short silence between two; its length in seconds."""
    first = pieces[0]
    silence = b"\0" * (int(first.rate * gap_s) * first.channels * first.width)
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(first.channels)
        w.setsampwidth(first.width)
        w.setframerate(first.rate)
        for i, p in enumerate(pieces):
            if (p.rate, p.channels, p.width) != (first.rate, first.channels, first.width):
                raise ValueError("the engine changed its audio format between two pieces")
            if i:
                w.writeframes(silence)
            w.writeframes(p.data)
    os.replace(tmp, path)
    return sum(p.seconds() for p in pieces) + gap_s * (len(pieces) - 1)


def decode_to_pcm(raw: bytes, suffix: str) -> Pcm:
    """Compressed audio (MP3, Opus, FLAC) to PCM, with GStreamer (else ffmpeg)."""
    if raw[:4] == b"RIFF" and suffix != ".wav":
        return Pcm.from_wav(raw)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="mmbox-tts-") as d:
        src, dst = Path(d) / f"in{suffix}", Path(d) / "out.wav"
        src.write_bytes(raw)
        try:
            Gst = gst()
            pipe = Gst.parse_launch(f'filesrc location="{src}" ! decodebin ! audioconvert ! audioresample ! '
                                    f'audio/x-raw,format=S16LE,channels=1 ! wavenc ! filesink location="{dst}"')
            pipe.set_state(Gst.State.PLAYING)
            msg = pipe.get_bus().timed_pop_filtered(60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            pipe.set_state(Gst.State.NULL)
            if not msg or msg.type == Gst.MessageType.ERROR:
                raise RuntimeError(msg.parse_error()[0].message if msg else "decoding timed out")
        except (ImportError, ValueError):
            if not shutil.which("ffmpeg"):
                raise RuntimeError("no decoder for this audio: install GStreamer (gir1.2-gstreamer-1.0) or ffmpeg")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-f", "wav", str(dst)],
                           check=True, timeout=120, creationflags=NOWIN)
        with wave.open(str(dst)) as w:
            return Pcm(w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getsampwidth()).mono()


class Cache:
    """The audio made for each message, kept to be played again: `<message id>-<digest>.wav`, the digest naming the
    text, the engine and the voice (another voice or a longer limit makes another file)."""

    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def _safe(mid: str) -> str:
        return re.sub(r"[^\w.-]", "_", mid)[:80]

    def path(self, mid: str, digest: str) -> Path:
        return self.root / f"{self._safe(mid)}-{digest[:16]}.wav"

    def has(self, mid: str) -> bool:
        return self.root.is_dir() and any(self.root.glob(f"{self._safe(mid)}-*.wav"))

    def remove_all(self, spare: Path | None = None) -> int:
        n = 0
        for p in self.root.glob("*.wav") if self.root.is_dir() else []:
            if p != spare:
                p.unlink(missing_ok=True)
                n += 1
        return n

    def remove(self, ids) -> int:
        n = 0
        for mid in ids:
            for p in self.root.glob(f"{self._safe(mid)}-*.wav") if self.root.is_dir() else []:
                p.unlink(missing_ok=True)
                n += 1
        return n

    def usage(self) -> tuple[int, int]:
        files = list(self.root.glob("*.wav")) if self.root.is_dir() else []
        return len(files), sum(f.stat().st_size for f in files if f.is_file())

    def prune(self, keep_s: float = CACHE_KEEP_S, max_bytes: int = CACHE_MAX_BYTES, spare: Path | None = None) -> None:
        if not self.root.is_dir():
            return
        now, files = time.time(), []
        for f in self.root.glob("*.wav"):
            try:
                st = f.stat()
            except OSError:
                continue
            # its time: when it was made or last played (Tts.audio touches it at each play; atime is unreliable)
            if now - st.st_mtime > keep_s and f != spare:
                f.unlink(missing_ok=True)
            else:
                files.append((st.st_mtime, st.st_size, f))
        total = sum(s for _, s, _ in files)
        for _, size, f in sorted(files):
            if total <= max_bytes:
                break
            if f != spare:
                f.unlink(missing_ok=True)
                total -= size
        for t in self.root.glob("*.tmp"):
            if now - t.stat().st_mtime > 3600:
                t.unlink(missing_ok=True)


# ---------------------------------------------------------------- engines
class EngineError(RuntimeError):
    pass


def http(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None, timeout: float = 60) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "mmbox-console", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise EngineError(f"cannot reach {urllib.parse.urlparse(url).netloc}: {getattr(e, 'reason', e)}") from None


def api_error(code: int, raw: bytes) -> str:
    try:
        j = json.loads(raw)
        msg = j.get("message") or j.get("detail") or j.get("error") or j
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg)[:200]
        if isinstance(msg, list):
            msg = "; ".join(str(x.get("msg") if isinstance(x, dict) else x) for x in msg)[:300]
    except ValueError:
        msg = raw[:200].decode("utf-8", "replace")
    return f"HTTP {code}: {msg}" if msg else f"HTTP {code}"


# the preset voices (2026-11): the APIs have no default voice, so each language gets one here; Settings can change it
MISTRAL_VOICES = [
    {"id": "voice_592", "lang": "fr", "label": "Marie (French, female)"},
    {"id": "voice_3137", "lang": "fr", "label": "Manon (French, female)"},
    {"id": "voice_589", "lang": "fr", "label": "Léa (French, female)"},
    {"id": "voice_3141", "lang": "fr", "label": "Camille (French, female)"},
    {"id": "voice_447", "lang": "en", "label": "Margaret (US English, female)"},
    {"id": "voice_478", "lang": "en", "label": "Sheila (US English, female)"},
    {"id": "voice_485", "lang": "en", "label": "Rob (US English, male)"},
    {"id": "voice_3150", "lang": "en", "label": "James (US English, male)"},
    {"id": "voice_1787", "lang": "en", "label": "Mary (UK English, female)"},
    {"id": "voice_1788", "lang": "en", "label": "David (UK English, male)"},
    {"id": "voice_3149", "lang": "en", "label": "Hugo (UK English, male)"},
]
GRADIUM_VOICES = [
    {"id": "6oIkS98REoVZ1dEw", "lang": "fr", "label": "Apolline (French, female)"},
    {"id": "YhIHaAfQ0cQPDV9R", "lang": "fr", "label": "Solène (French, female)"},
    {"id": "FXxJ9mANRq6BCTX5", "lang": "fr", "label": "Noémie (French, female)"},
    {"id": "iEu63s1rhn_kegTr", "lang": "fr", "label": "Gaspard (French, male)"},
    {"id": "YKeBw3OV1RgpdhLh", "lang": "fr", "label": "Jules (French, male)"},
    {"id": "biuhvu17TxVKOcyy", "lang": "fr", "label": "Marius (French, male)"},
    {"id": "Tek4tJXiX6_yvXq7", "lang": "fr", "label": "Augustin (French, male)"},
    {"id": "xynYWquoAsrvM7UY", "lang": "fr", "label": "Mélanie (Québec French, female)"},
    {"id": "Bla6SbVMczYnOhfK", "lang": "en", "label": "Marlowe (US English, female)"},
    {"id": "_6Aslh2DxfmnRLmP", "lang": "en", "label": "Russell (US English, male)"},
    {"id": "6PWnV0Nq4wu7RVBT", "lang": "en", "label": "Maeve (UK English, female)"},
    {"id": "dME3IWyZBvmh1n1q", "lang": "en", "label": "Toby (UK English, male)"},
]


def default_voice(voices: list[dict], lang: str) -> str:
    return next(v["id"] for v in voices if v["lang"] == lang)


class MistralEngine:
    """Mistral's text to speech (Voxtral TTS 3), POST /v1/audio/speech: JSON in, the WAV in base64 out (24 kHz)."""
    name, piece = "mistral", 2500  # about 4 000 characters per generation at most: pieces cut between sentences
    BASE = os.environ.get("MMBOX_TTS_MISTRAL_URL", "https://api.mistral.ai")
    MODEL = os.environ.get("MMBOX_TTS_MISTRAL_MODEL", "voxtral-mini-tts-3-0")
    VOICES = MISTRAL_VOICES

    def __init__(self, key: str):
        self.key = key

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "Accept": "application/json"}

    def test(self) -> str:
        code, raw, _ = http(self.BASE + "/v1/models", headers=self.headers(), timeout=20)
        if code != 200:
            raise EngineError(("key refused: " if code in (401, 403) else "") + api_error(code, raw))
        try:
            ids = [m.get("id") for m in (json.loads(raw).get("data") or []) if isinstance(m, dict)]
        except (ValueError, AttributeError):
            ids = []
        tts = [i for i in ids if i and "tts" in i]
        return "key accepted" + (f" (text to speech: {', '.join(tts[:2])})" if tts else "")

    def usable(self) -> str:
        """Whether a reading would work now (a check, no audio): raises EngineError when not."""
        return self.test()

    def synth(self, text: str, lang: str, voice: str | None) -> Pcm:
        body = {"model": self.MODEL, "input": text, "voice_id": voice or default_voice(self.VOICES, lang), "response_format": "wav"}
        code, raw, hdr = http(self.BASE + "/v1/audio/speech", "POST", self.headers(), json.dumps(body).encode(), timeout=180)
        if code != 200:
            raise EngineError("Mistral: " + api_error(code, raw))
        return audio_of(raw, hdr)


class GradiumEngine:
    """Gradium's text to speech, POST /api/post/speech/tts: JSON in, raw WAV out (48 kHz)."""
    name, piece = "gradium", 1400  # the free plan reads 1 500 characters per request
    BASE = os.environ.get("MMBOX_TTS_GRADIUM_URL", "https://api.gradium.ai/api")
    VOICES = GRADIUM_VOICES

    def __init__(self, key: str):
        self.key = key

    def headers(self) -> dict:
        return {"x-api-key": self.key, "Content-Type": "application/json"}

    def test(self) -> str:
        code, raw, _ = http(self.BASE + "/usages/credits", headers=self.headers(), timeout=20)
        if code != 200:
            raise EngineError(("key refused: " if code in (401, 403) else "") + api_error(code, raw))
        try:
            j = json.loads(raw)
            left, plan = j.get("remaining_credits"), j.get("plan_name")
        except (ValueError, AttributeError):
            left = plan = None
        return "key accepted" + (f" ({left:,} characters left" + (f", plan {plan}" if plan else "") + ")" if isinstance(left, int) else "")

    def usable(self) -> str:
        """Whether a reading would work now: the key answers and credits are left (raises EngineError when not)."""
        msg = self.test()
        if "(0 characters left" in msg:
            raise EngineError("Gradium: no credits left")
        return msg

    def synth(self, text: str, lang: str, voice: str | None) -> Pcm:
        body = {"text": text, "voice_id": voice or default_voice(self.VOICES, lang), "output_format": "wav", "only_audio": True}
        code, raw, hdr = http(self.BASE + "/post/speech/tts", "POST", self.headers(), json.dumps(body).encode(), timeout=180)
        if code != 200:
            raise EngineError("Gradium: " + api_error(code, raw))
        return audio_of(raw, hdr)


def audio_of(raw: bytes, hdr: dict) -> Pcm:
    """An engine's answer: raw audio, or JSON carrying it in base64."""
    ctype = (hdr.get("Content-Type") or hdr.get("content-type") or "").lower()
    if "json" in ctype or raw[:1] == b"{":
        import base64
        j = json.loads(raw)
        b64 = j.get("audio_data") or j.get("audio") or j.get("data")
        if isinstance(b64, dict):
            b64 = b64.get("data")
        if not isinstance(b64, str):
            raise EngineError("no audio in the answer")
        raw = base64.b64decode(b64)
    if raw[:4] == b"RIFF":
        return Pcm.from_wav(raw)
    suffix = ".mp3" if raw[:3] == b"ID3" or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") else ".ogg" if raw[:4] == b"OggS" \
        else ".flac" if raw[:4] == b"fLaC" else ".bin"
    return decode_to_pcm(raw, suffix)


# ---------------------------------------------------------------- the local engine: Piper, in a venv of its own
PIPER_VOICES = {  # rhasspy/piper-voices on Hugging Face (MIT code, each voice its own licence): name -> (lang, path, label)
    "fr_FR-siwis-medium": ("fr", "fr/fr_FR/siwis/medium", "French, Siwis (female), medium"),
    "fr_FR-upmc-medium": ("fr", "fr/fr_FR/upmc/medium", "French, UPMC (female and male), medium"),
    "fr_FR-tom-medium": ("fr", "fr/fr_FR/tom/medium", "French, Tom (male), medium"),
    "fr_FR-gilles-low": ("fr", "fr/fr_FR/gilles/low", "French, Gilles (male), low"),
    "en_US-lessac-medium": ("en", "en/en_US/lessac/medium", "English (US), Lessac (female), medium"),
    "en_US-ryan-high": ("en", "en/en_US/ryan/high", "English (US), Ryan (male), high"),
    "en_GB-alba-medium": ("en", "en/en_GB/alba/medium", "English (UK), Alba (female), medium"),
}
PIPER_DEFAULT = {"fr": "fr_FR-siwis-medium", "en": "en_US-lessac-medium"}
PIPER_PACKAGE = os.environ.get("MMBOX_TTS_PIPER_PACKAGE", "piper-tts")
HF_VOICES = os.environ.get("MMBOX_TTS_PIPER_VOICES_URL", "https://huggingface.co/rhasspy/piper-voices/resolve/main")


class LocalEngine:
    """Piper (offline, on the CPU), installed by the console into `<data>/piper` (a venv and the voices)."""
    name, piece = "local", 1500

    def __init__(self, root: Path):
        self.root = root
        self.venv, self.voices_dir = root / "venv", root / "voices"

    def python(self) -> Path:
        return self.venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def installed(self) -> bool:
        return self.python().is_file() and (self.root / "installed.json").is_file()

    def voice_files(self, name: str) -> tuple[Path, Path]:
        return self.voices_dir / f"{name}.onnx", self.voices_dir / f"{name}.onnx.json"

    def have_voices(self) -> list[str]:
        return sorted(n for n in PIPER_VOICES if all(p.is_file() for p in self.voice_files(n)))

    def test(self) -> str:
        if not self.installed():
            raise EngineError("the local engine is not installed")
        return f"installed, {len(self.have_voices())} voice(s)"

    def pick(self, lang: str, voice: str | None) -> str:
        have = self.have_voices()
        name = voice if voice in have else PIPER_DEFAULT.get(lang) if PIPER_DEFAULT.get(lang) in have else \
            next((n for n in have if PIPER_VOICES[n][0] == lang), None) or (have[0] if have else None)
        if not name:
            raise EngineError("no voice of the local engine is downloaded (Settings, Reading aloud)")
        return name

    def synth(self, text: str, lang: str, voice: str | None) -> Pcm:
        return self.synth_many([text], lang, voice)[0]

    def synth_many(self, texts: list[str], lang: str, voice: str | None, progress=None) -> list[Pcm]:
        """Every piece with one load of the voice (a process of the venv: Piper is GPL, it stays a program apart)."""
        if not self.installed():
            raise EngineError("the local engine is not installed (Settings, Reading aloud)")
        onnx, _ = self.voice_files(self.pick(lang, voice))
        import tempfile
        with tempfile.TemporaryDirectory(prefix="mmbox-piper-") as d:
            p = subprocess.Popen([str(self.python()), "-c", PIPER_DRIVER, str(onnx), d], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=NOWIN)
            p.stdin.write(json.dumps(texts))
            p.stdin.close()
            done = 0
            for line in p.stdout:
                if line.strip().isdigit():
                    done += 1
                    if progress:
                        progress(done)
            err = p.stderr.read()
            if p.wait(timeout=600) != 0 or done != len(texts):
                raise EngineError("Piper: " + ((err.strip().splitlines() or ["failed"])[-1])[:300])
            return [Pcm.from_wav((Path(d) / f"{i}.wav").read_bytes()) for i in range(len(texts))]


PIPER_DRIVER = """
import json, sys, wave
from piper import PiperVoice
voice = PiperVoice.load(sys.argv[1])
for i, text in enumerate(json.load(sys.stdin)):
    with wave.open(f"{sys.argv[2]}/{i}.wav", "wb") as w:
        voice.synthesize_wav(text, w)
    print(i, flush=True)
"""


class LocalInstaller:
    """Install, update the voices of, or remove the local engine, in a thread; its progress for the page."""

    def __init__(self, engine: LocalEngine, on_change):
        self.e, self.on_change = engine, on_change
        self.job: dict | None = None  # {"op", "step", "pct", "err", "done"}
        self.lock = threading.Lock()
        self._size: tuple[float, int] | None = None

    def busy(self) -> bool:
        return bool(self.job and not self.job.get("done"))

    def size(self) -> int:
        """What the engine takes on disk, counted again at most every 2 minutes (a venv has thousands of files)."""
        now = time.time()
        if self._size and now - self._size[0] < 120 and not self.busy():
            return self._size[1]
        n = 0
        if self.e.root.is_dir():
            for f in self.e.root.rglob("*"):
                try:
                    n += f.stat().st_size if f.is_file() else 0
                except OSError:
                    pass
        self._size = (now, n)
        return n

    def state(self) -> dict:
        e = self.e
        size = self.size()
        try:
            meta = json.loads((e.root / "installed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        return {"installed": e.installed(), "voices": e.have_voices(), "size": size, "path": str(e.root),
                "version": meta.get("version"), "at": meta.get("at"), "job": dict(self.job) if self.job else None,
                "catalog": [{"id": k, "lang": v[0], "label": v[2]} for k, v in PIPER_VOICES.items()],
                "can_install": sys.version_info >= (3, 9) and self._venv_ok()}

    @staticmethod
    def _venv_ok() -> bool:
        try:
            import ensurepip  # noqa: F401 - Debian: python3-venv
            return True
        except ImportError:
            return False

    def _step(self, step: str, pct: float | None = None) -> None:
        self.job.update(step=step, pct=pct)
        self.on_change()

    def start(self, op: str, voices: list[str] | None = None) -> dict:
        with self.lock:
            if self.busy():
                return {"ok": False, "err": f"already busy: {self.job.get('op')}"}
            if op == "install" and not self._venv_ok():
                return {"ok": False, "err": "Python's venv is missing: sudo apt install python3-venv"}
            self.job = {"op": op, "step": "starting", "pct": 0, "started": time.time()}
        threading.Thread(target=self._run, args=(op, voices or []), daemon=True, name="tts-local").start()
        self.on_change()
        return {"ok": True}

    def _run(self, op: str, voices: list[str]) -> None:
        try:
            if op == "uninstall":
                self._step("removing the engine and its voices")
                shutil.rmtree(self.e.root, ignore_errors=True)
            elif op == "remove_voice":
                for v in voices:
                    for p in self.e.voice_files(v):
                        p.unlink(missing_ok=True)
            else:
                if op == "install" or not self.e.installed():
                    self._install()
                for i, v in enumerate(voices):
                    self._voice(v, i, len(voices))
            self.job.update(done=True, step="done", pct=100)
        except Exception as e:  # noqa: BLE001 - shown in Settings
            log(f"local engine, {op}: {type(e).__name__}: {e}")
            self.job.update(done=True, err=f"{type(e).__name__}: {e}"[:500])
        self._size = None
        self.on_change()

    def _install(self) -> None:
        e = self.e
        e.root.mkdir(parents=True, exist_ok=True)
        self._step("making a private Python environment", 5)
        if not e.python().is_file():
            subprocess.run([sys.executable, "-m", "venv", str(e.venv)], check=True, capture_output=True, timeout=600, creationflags=NOWIN)
        self._step(f"installing {PIPER_PACKAGE} (pip: a few minutes, about 100 MB)", 15)
        pip = subprocess.run([str(e.python()), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", PIPER_PACKAGE],
                             capture_output=True, text=True, timeout=1800, creationflags=NOWIN)
        if pip.returncode != 0:
            raise RuntimeError("pip: " + (pip.stderr.strip().splitlines() or ["failed"])[-1][:300])
        ver = subprocess.run([str(e.python()), "-c", f"import importlib.metadata as m; print(m.version({PIPER_PACKAGE!r}))"],
                             capture_output=True, text=True, timeout=60, creationflags=NOWIN).stdout.strip()
        (e.root / "installed.json").write_text(json.dumps({"package": PIPER_PACKAGE, "version": ver or None,
                                                           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")

    def _voice(self, name: str, i: int, n: int) -> None:
        if name not in PIPER_VOICES:
            raise ValueError(f"unknown voice {name}")
        onnx, js = self.e.voice_files(name)
        if onnx.is_file() and js.is_file():
            return
        self.e.voices_dir.mkdir(parents=True, exist_ok=True)
        base = f"{HF_VOICES}/{PIPER_VOICES[name][1]}/{name}"
        for url, dst in ((base + ".onnx.json", js), (base + ".onnx", onnx)):
            tmp = dst.with_name(dst.name + ".part")
            req = urllib.request.Request(url + "?download=true", headers={"User-Agent": "mmbox-console"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while True:
                    buf = r.read(1 << 16)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    if total and dst is onnx:
                        self._step(f"downloading the voice {name} ({got / 1e6:.0f} / {total / 1e6:.0f} MB)",
                                   40 + 60 * (i + got / total) / max(1, n))
            os.replace(tmp, dst)


# ---------------------------------------------------------------- playback: GStreamer
_GST = None


def gst():
    """GStreamer, initialized once; ImportError or ValueError when this computer has no GObject bindings for it."""
    global _GST
    if _GST is None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        _GST = Gst
    return _GST


class Player:
    """One playbin, its bus read by a thread of its own (no main loop needed). Speed changes keep the pitch
    (scaletempo). Every change calls `on_change`."""

    def __init__(self, on_change):
        Gst = gst()
        self.Gst, self.on_change = Gst, on_change
        self.pb = Gst.ElementFactory.make("playbin", "mmbox-tts")
        if self.pb is None:
            raise RuntimeError("GStreamer has no playbin (gstreamer1.0-plugins-base)")
        # scaletempo keeps the pitch at any speed; fed one fixed format, because it crashes (segfault) when a new file
        # of another sample rate comes at a speed other than 1 (Mistral 24 kHz, Gradium 48 kHz, Piper 22.05 kHz)
        if Gst.ElementFactory.find("scaletempo"):
            try:
                self.pb.set_property("audio-filter", Gst.parse_bin_from_description(
                    "audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=1 ! scaletempo", True))
            except Exception as e:  # noqa: BLE001 - then the speed changes the pitch, nothing worse
                log(f"no pitch keeping: {e}")
        if os.environ.get("MMBOX_TTS_SINK"):  # tests: fakesink (sync, real time, silent)
            sink = Gst.ElementFactory.make(os.environ["MMBOX_TTS_SINK"], None)
            if sink is not None:
                sink.set_property("sync", True)
                self.pb.set_property("audio-sink", sink)
        self.lock = threading.RLock()
        self.path: Path | None = None
        self.dur = 0.0
        self.rate = 1.0
        self.status = "idle"  # idle | playing | paused | ended
        self.on_end = None
        threading.Thread(target=self._bus, daemon=True, name="tts-bus").start()

    def _bus(self) -> None:
        Gst, bus = self.Gst, self.pb.get_bus()
        while True:
            msg = bus.timed_pop_filtered(200 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if msg is None:
                continue
            if msg.type == Gst.MessageType.EOS:
                with self.lock:
                    if self.status == "playing":
                        self.pb.set_state(Gst.State.PAUSED)
                        self.status = "ended"
                cb = self.on_end
                self.on_change()
                if cb:
                    cb()
            elif msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                log(f"playback: {err.message} ({dbg})")
                with self.lock:
                    self.pb.set_state(Gst.State.NULL)
                    self.status, self.error = "idle", err.message
                self.on_change()

    def load(self, path: Path, dur: float, rate: float, play: bool = True) -> None:
        Gst = self.Gst
        with self.lock:
            self.pb.set_state(Gst.State.NULL)
            self.path, self.dur, self.error = path, dur, None
            self.pb.set_property("uri", path.resolve().as_uri())
            self.pb.set_state(Gst.State.PAUSED)
            self.pb.get_state(5 * Gst.SECOND)
            self.rate = 1.0
            if abs(rate - 1.0) > 1e-3:
                self._seek(0.0, rate)
            self.status = "paused"
            if play:
                self.pb.set_state(Gst.State.PLAYING)
                self.status = "playing"
        self.on_change()

    def _seek(self, pos: float, rate: float | None = None) -> None:
        Gst = self.Gst
        rate = self.rate if rate is None else rate
        pos = max(0.0, min(pos, max(0.0, self.dur - 0.05)))
        self.pb.seek(rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                     Gst.SeekType.SET, int(pos * Gst.SECOND), Gst.SeekType.NONE, 0)
        self.pb.get_state(2 * Gst.SECOND)
        self.rate = rate

    def position(self) -> float:
        with self.lock:
            if self.status == "idle":
                return 0.0
            if self.status == "ended":
                return self.dur
            ok, pos = self.pb.query_position(self.Gst.Format.TIME)
            return pos / self.Gst.SECOND if ok and pos >= 0 else 0.0

    def play(self) -> None:
        with self.lock:
            if self.status == "idle" and self.path:
                return self.load(self.path, self.dur, self.rate)
            if self.status == "ended":
                self._seek(0.0)
            if self.status in ("paused", "ended"):
                self.pb.set_state(self.Gst.State.PLAYING)
                self.status = "playing"
        self.on_change()

    def pause(self) -> None:
        with self.lock:
            if self.status == "playing":
                self.pb.set_state(self.Gst.State.PAUSED)
                self.status = "paused"
        self.on_change()

    def stop(self) -> None:
        with self.lock:
            self.pb.set_state(self.Gst.State.NULL)
            self.status = "idle"
        self.on_change()

    def seek(self, pos: float) -> None:
        with self.lock:
            if self.status == "idle":
                return
            if self.status == "ended":
                self.status = "paused"
            self._seek(pos)
        self.on_change()

    def set_rate(self, rate: float) -> None:
        rate = max(0.5, min(3.0, float(rate)))
        with self.lock:
            if self.status == "idle":
                self.rate = rate
            else:
                was_end = self.status == "ended"
                pos = 0.0 if was_end else self.position()
                self._seek(pos, rate)
                if was_end:
                    self.status = "paused"
        self.on_change()


# ---------------------------------------------------------------- the desktop's media controls (MPRIS 2)
MPRIS_XML = """<node>
 <interface name="org.mpris.MediaPlayer2">
  <method name="Raise"/><method name="Quit"/>
  <property name="CanQuit" type="b" access="read"/><property name="CanRaise" type="b" access="read"/>
  <property name="HasTrackList" type="b" access="read"/><property name="Identity" type="s" access="read"/>
  <property name="DesktopEntry" type="s" access="read"/>
  <property name="SupportedUriSchemes" type="as" access="read"/><property name="SupportedMimeTypes" type="as" access="read"/>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/><method name="Stop"/>
  <method name="Play"/>
  <method name="Seek"><arg direction="in" type="x" name="Offset"/></method>
  <method name="SetPosition"><arg direction="in" type="o" name="TrackId"/><arg direction="in" type="x" name="Position"/></method>
  <method name="OpenUri"><arg direction="in" type="s" name="Uri"/></method>
  <signal name="Seeked"><arg type="x" name="Position"/></signal>
  <property name="PlaybackStatus" type="s" access="read"/><property name="LoopStatus" type="s" access="readwrite"/>
  <property name="Rate" type="d" access="readwrite"/><property name="Shuffle" type="b" access="readwrite"/>
  <property name="Metadata" type="a{sv}" access="read"/><property name="Volume" type="d" access="readwrite"/>
  <property name="Position" type="x" access="read"/><property name="MinimumRate" type="d" access="read"/>
  <property name="MaximumRate" type="d" access="read"/><property name="CanGoNext" type="b" access="read"/>
  <property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
  <property name="CanPause" type="b" access="read"/><property name="CanSeek" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/>
 </interface>
</node>"""


class Mpris:
    """The player as the desktop sees a music player (KDE's media controller, GNOME's notification list, lock
    screens): org.mpris.MediaPlayer2.mmbox_console, owned while something is loaded. Built and called on the
    console's GLib loop (the Desktop's)."""

    PATH = "/org/mpris/MediaPlayer2"

    def __init__(self, Gio, GLib, tts: "Tts", art: Path | None, on_raise):
        self.Gio, self.GLib, self.tts, self.art, self.on_raise = Gio, GLib, tts, art, on_raise
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.ifaces = Gio.DBusNodeInfo.new_for_xml(MPRIS_XML).interfaces
        self.regs = [self.bus.register_object(self.PATH, i, self._call, self._get, self._set) for i in self.ifaces]
        self.own = None
        self.last: dict = {}

    def _st(self) -> dict:
        return self.tts.state()

    def _status(self, st) -> str:
        return {"playing": "Playing", "paused": "Paused", "ended": "Paused"}.get(st["st"], "Stopped")

    def _meta(self, st) -> dict:
        V = self.GLib.Variant
        tid = "/org/mmbox/track/" + re.sub(r"[^A-Za-z0-9_]", "_", st.get("id") or "none")
        m = {"mpris:trackid": V("o", tid), "xesam:title": V("s", st.get("title") or "mmbox"),
             "xesam:artist": V("as", [st.get("who") or "mmbox"]), "xesam:album": V("s", st.get("project") or "mmbox"),
             "mpris:length": V("x", int((st.get("dur") or 0) * 1e6))}
        if self.art and self.art.is_file():
            m["mpris:artUrl"] = V("s", self.art.resolve().as_uri())
        return m

    def _get(self, bus, sender, path, iface, prop):
        V, st = self.GLib.Variant, self._st()
        if iface == "org.mpris.MediaPlayer2":
            return {"CanQuit": V("b", False), "CanRaise": V("b", True), "HasTrackList": V("b", False),
                    "Identity": V("s", "mmbox console"), "DesktopEntry": V("s", "mmbox-console"),
                    "SupportedUriSchemes": V("as", []), "SupportedMimeTypes": V("as", [])}.get(prop)
        loaded = st["st"] in ("playing", "paused", "ended")
        return {"PlaybackStatus": V("s", self._status(st)), "LoopStatus": V("s", "None"), "Rate": V("d", st.get("rate") or 1.0),
                "Shuffle": V("b", False), "Metadata": V("a{sv}", self._meta(st)), "Volume": V("d", 1.0),
                "Position": V("x", int(self.tts.player_position() * 1e6)), "MinimumRate": V("d", 0.5), "MaximumRate": V("d", 3.0),
                "CanGoNext": V("b", bool(st.get("queue"))), "CanGoPrevious": V("b", loaded), "CanPlay": V("b", loaded),
                "CanPause": V("b", loaded), "CanSeek": V("b", loaded), "CanControl": V("b", True)}.get(prop)

    def _set(self, bus, sender, path, iface, prop, value):
        if prop == "Rate":
            threading.Thread(target=self.tts.ctl, args=("rate", value.unpack()), daemon=True).start()
        return True

    def _call(self, bus, sender, path, iface, method, params, inv):
        t, args = self.tts, params.unpack()
        work = {"Next": lambda: t.ctl("next"), "Previous": lambda: t.ctl("prev"), "Pause": lambda: t.ctl("pause"),
                "PlayPause": lambda: t.ctl("toggle"), "Stop": lambda: t.ctl("stop"), "Play": lambda: t.ctl("play"),
                "Seek": lambda: t.ctl("seek_by", args[0] / 1e6), "SetPosition": lambda: t.ctl("seek", args[1] / 1e6),
                "Raise": lambda: self.on_raise(t.state().get("id")), "Quit": lambda: None, "OpenUri": lambda: None}.get(method)
        inv.return_value(None)
        if work:  # off the loop: a seek waits for GStreamer
            threading.Thread(target=work, daemon=True, name="mpris").start()

    def update(self) -> bool:
        """After each change (on the GLib loop): the name owned while something is loaded, the properties told."""
        st = self._st()
        loaded = st["st"] in ("playing", "paused", "ended", "generating")
        if loaded and self.own is None:
            self.own = self.Gio.bus_own_name_on_connection(self.bus, "org.mpris.MediaPlayer2.mmbox_console",
                                                           self.Gio.BusNameOwnerFlags.NONE, None, None)
        elif not loaded and self.own is not None:
            self.Gio.bus_unown_name(self.own)
            self.own = None
        if self.own is None:
            return False
        V = self.GLib.Variant
        changed = {"PlaybackStatus": V("s", self._status(st)), "Metadata": V("a{sv}", self._meta(st)),
                   "Rate": V("d", st.get("rate") or 1.0), "CanGoNext": V("b", bool(st.get("queue"))),
                   "CanPlay": V("b", True), "CanPause": V("b", True), "CanSeek": V("b", True), "CanGoPrevious": V("b", True)}
        try:
            self.bus.emit_signal(None, self.PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                 V("(sa{sv}as)", ("org.mpris.MediaPlayer2.Player", changed, [])))
            if st.get("seeked") != self.last.get("seeked"):
                self.bus.emit_signal(None, self.PATH, "org.mpris.MediaPlayer2.Player", "Seeked",
                                     V("(x)", (int(self.tts.player_position() * 1e6),)))
        except Exception as e:  # noqa: BLE001 - the media controls are a bonus
            log(f"mpris: {e}")
        self.last = st
        return False


# ---------------------------------------------------------------- the whole: settings, keys, queue, playback
class Tts:
    """What the console calls. `settings` and `save` read and write the console's state.json part ("tts"); `keys` is
    a KeyStore (the API keys, in the system's keyring); `on_change(state)` tells the Desktop (notification, MPRIS)."""

    def __init__(self, home: Path, data: Path, keys, settings_get, settings_save):
        self.home, self.data, self.keys = home, data, keys
        self._get, self._save = settings_get, settings_save
        self.cache = Cache(home / "tts" / "cache")
        self.local = LocalEngine(data / "piper")
        self.installer = LocalInstaller(self.local, self._cfg)
        self.cfg_version = 1  # the settings as the page shows them: a new number at each change
        self.lock = threading.RLock()
        self.listeners: list = []
        self.version = 0
        self.player: Player | None = None
        self.player_error: str | None = None
        self.current: dict | None = None  # the item loaded: {"id", "item", "manual", ...}
        self.queue: list[dict] = []  # what auto-reading still has to read
        self.history: list[dict] = []  # what was read, for Previous
        self.gen: dict | None = None  # {"id", "done", "total", "manual"}
        self.error: dict | None = None  # {"id", "err", "at"}
        self.generating: dict[str, threading.Event] = {}
        self.seen: dict[str, set] = {}  # message ids known, per source: none read at the opening
        self._keys_cache: dict | None = None
        self.seeked = 0
        self.down: dict[str, dict] = {}  # a cloud voice that failed: {since, err, quota, last_test, test_err}
        self.test_lock = threading.Lock()  # one upgrade test at a time, its 30 s checked and taken under it
        threading.Thread(target=self.prune, daemon=True).start()

    # -- settings
    def settings(self) -> dict:
        st = {**SETTINGS_DEFAULT, **(self._get() or {})}
        st["auto"] = dict(st.get("auto") or {})
        st["voices"] = dict(st.get("voices") or {})
        return st

    def _put(self, st: dict) -> None:
        self._save({k: v for k, v in st.items() if k in SETTINGS_DEFAULT})
        self._cfg()

    def _cfg(self) -> None:
        self.cfg_version += 1
        self._changed()

    def _keys(self) -> dict:
        if self._keys_cache is None:
            try:
                self._keys_cache = self.keys.load() or {}
            except Exception as e:  # noqa: BLE001 - a locked keyring: no cloud voice until it opens
                log(f"API keys unreadable: {e}")
                return {}
        return self._keys_cache

    def key_of(self, provider: str) -> str | None:
        v = self._keys().get(provider)
        return v if isinstance(v, str) and v else None

    def available(self) -> list[str]:
        out = [p for p in ("mistral", "gradium") if self.key_of(p)]
        if self.local.installed() and self.local.have_voices():
            out.append("local")
        return out

    def chain(self) -> list[str]:
        """The voices in the order they are tried: the one chosen (else the first cloud voice), the other cloud voice,
        then the local engine. Chosen, the local engine reads alone."""
        av = self.available()
        want = self.settings().get("engine")
        if want == "local" and "local" in av:
            return ["local"]
        clouds = [e for e in ("mistral", "gradium") if e in av]
        first = want if want in clouds else (clouds[0] if clouds else None)
        return ([first] if first else []) + [e for e in clouds if e != first] + (["local"] if "local" in av else [])

    def preferred(self) -> str | None:
        ch = self.chain()
        return ch[0] if ch else None

    def engine_name(self) -> str | None:
        """The voice the next audio is made with: the first of the chain that has not failed (else the first)."""
        ch = self.chain()
        up = [e for e in ch if e not in self.down]
        return (up or ch or [None])[0]

    def mark_down(self, name: str, err) -> None:
        with self.lock:
            d = self.down.setdefault(name, {"since": time.time()})
            d.update(err=str(err)[:300], quota=is_quota(str(err)), last_test=time.time())
            d.pop("test_err", None)
        log(f"{name} failed ({err}): the next voice reads; {name} is tested again with a later reading")
        self._cfg()

    def upgrade_test(self) -> bool:
        """A reading is asked while a voice better than the one used failed: test it again, in a thread (the reading
        goes on meanwhile), one test at a time and at most once every UPGRADE_TEST_S (both decided under an exclusive
        lock). Returns whether a test started."""
        ch = self.chain()
        used = next((i for i, e in enumerate(ch) if e not in self.down), None)
        if used is None:
            return False  # every voice failed: the reading tries them itself, in order
        better = [e for e in ch[:used] if e in self.down]
        if not better or not self.test_lock.acquire(blocking=False):
            return False
        try:
            now = time.time()
            with self.lock:
                due = [e for e in better if now - float((self.down.get(e) or {}).get("last_test") or 0) >= UPGRADE_TEST_S]
                for e in due:
                    self.down[e]["last_test"] = now
            if not due:
                self.test_lock.release()
                return False
            threading.Thread(target=self._upgrade_run, args=(due,), daemon=True, name="tts-upgrade").start()
            return True
        except BaseException:
            self.test_lock.release()
            raise

    def _upgrade_run(self, names: list) -> None:
        try:
            for n in names:  # the best first: once one answers, the next readings use it
                eng, d = self.engine(n), dict(self.down.get(n) or {})
                try:
                    if eng is None:
                        raise EngineError("its key was removed")
                    if d.get("quota"):  # a key check passes without quota: a short synthesis tells
                        eng.synth("Test.", "en", None)
                    else:
                        eng.usable()
                    ok, msg = True, "answers again"
                except EngineError as e:
                    ok, msg = False, str(e)
                except Exception as e:  # noqa: BLE001 - a test never stops a reading
                    ok, msg = False, f"{type(e).__name__}: {e}"
                with self.lock:
                    if n in self.down:
                        if ok:
                            self.down.pop(n)
                        else:
                            self.down[n].update(test_err=msg[:300], tested=time.time())
                log(f"{n}: {msg}" + (": used again from the next reading" if ok else ": still unavailable"))
                if ok:
                    break
            self._cfg()
        finally:
            self.test_lock.release()

    def engine(self, name: str | None = None):
        name = name or self.engine_name()
        if name == "mistral" and self.key_of("mistral"):
            return MistralEngine(self.key_of("mistral"))
        if name == "gradium" and self.key_of("gradium"):
            return GradiumEngine(self.key_of("gradium"))
        if name == "local":
            return self.local
        return None

    def can_play(self) -> bool:
        """Whether this computer can play audio (GStreamer and its playbin), without making the player yet."""
        if self.player is not None:
            return True
        if self.player_error is None and not hasattr(self, "_gst_ok"):
            try:
                Gst = gst()
                self._gst_ok = bool(Gst.ElementFactory.find("playbin"))
                if not self._gst_ok:
                    self.player_error = "no playback on this computer: GStreamer has no playbin (gstreamer1.0-plugins-base)"
            except (ImportError, ValueError) as e:
                self._gst_ok = False
                self.player_error = f"no playback on this computer ({e}): install gir1.2-gstreamer-1.0 and gstreamer1.0-plugins-good"
        return bool(getattr(self, "_gst_ok", False)) and self.player_error is None

    def public(self) -> dict:
        """The settings as the page shows them (never a key: its last four characters)."""
        st, keys = self.settings(), self._keys()
        tested = (self._get() or {}).get("tested") or {}
        av = self.available()
        return {**{k: st[k] for k in ("show_play", "auto_operator", "auto", "voices", "rate", "max_chars", "lang",
                                      "cache_keep_days", "cache_max_mb")},
                "engine": self.preferred(), "engine_now": self.engine_name(), "engine_set": st.get("engine") or None,
                "chain": self.chain(), "upgrade_every_s": UPGRADE_TEST_S,
                "down": {n: {k: d.get(k) for k in ("since", "err", "quota", "last_test", "test_err")} for n, d in self.down.items()},
                "available": av,
                "names": ENGINE_NAMES,
                "keys": {p: ({"set": True, "hint": "…" + keys[p][-4:], **(tested.get(p) or {})} if isinstance(keys.get(p), str) and keys.get(p)
                             else {"set": False}) for p in ("mistral", "gradium")},
                "local": self.installer.state(), "playback": self.can_play(), "playback_error": self.player_error,
                "cache": dict(zip(("files", "bytes"), self.cache.usage())), "rates": RATES,
                "piper_default": PIPER_DEFAULT, "voice_list": {"mistral": MISTRAL_VOICES, "gradium": GRADIUM_VOICES},
                "voice_default": {"mistral": {lg: default_voice(MISTRAL_VOICES, lg) for lg in ("fr", "en")},
                                  "gradium": {lg: default_voice(GRADIUM_VOICES, lg) for lg in ("fr", "en")}}}

    def set(self, patch: dict) -> dict:
        st = self.settings()
        for k in ("show_play", "auto_operator"):
            if k in patch:
                st[k] = bool(patch[k])
        if "engine" in patch:
            if patch["engine"] not in ("", *ENGINES):
                return {"ok": False, "err": "unknown engine"}
            st["engine"] = patch["engine"]
        if "rate" in patch:
            st["rate"] = min(3.0, max(0.5, float(patch["rate"])))
        prune = False
        if "cache_keep_days" in patch:  # how long the audio stays after it was last played
            v = float(patch["cache_keep_days"])
            if not 0.04 <= v <= 3650:
                return {"ok": False, "err": "keep the audio between one hour (0.04 day) and 3650 days"}
            st["cache_keep_days"], prune = round(v, 2), True
        if "cache_max_mb" in patch:  # what the audio may take: past it, the least recently played go first
            v = int(float(patch["cache_max_mb"]))
            if not 10 <= v <= 1_000_000:
                return {"ok": False, "err": "between 10 MB and 1,000,000 MB"}
            st["cache_max_mb"], prune = v, True
        if "max_chars" in patch:
            st["max_chars"] = min(100_000, max(300, int(patch["max_chars"])))
        if "lang" in patch and patch["lang"] in ("auto", "fr", "en"):
            st["lang"] = patch["lang"]
        if isinstance(patch.get("voice"), dict):  # {"engine": "mistral", "lang": "fr", "voice": "..."}
            v = patch["voice"]
            if v.get("engine") in ENGINES and v.get("lang") in ("fr", "en"):
                st["voices"].setdefault(v["engine"], {})[v["lang"]] = str(v.get("voice") or "")[:120]
        self._put(st)
        if prune:  # the new limits apply now, not at the next audio made
            self.prune(spare=(self.current or {}).get("path"))
        return {"ok": True, "tts": self.public()}

    def prune(self, spare: Path | None = None) -> None:
        """The kept audio within the operator's limits (Settings): older than the delay since it was last played,
        then the least recently played while the total is over the size."""
        st = self.settings()
        try:
            keep_s = float(st.get("cache_keep_days") or CACHE_KEEP_DAYS) * 86400
            max_b = int(float(st.get("cache_max_mb") or CACHE_MAX_MB) * 1000 * 1000)
        except (TypeError, ValueError):
            keep_s, max_b = CACHE_KEEP_S, CACHE_MAX_BYTES
        try:
            self.cache.prune(keep_s, max_b, spare=spare or (self.current or {}).get("path"))
        except OSError as e:
            log(f"pruning the audio: {e}")

    def set_key(self, provider: str, key: str) -> dict:
        """Save & test: the key tested first; kept when the service accepts it (or cannot be reached: said so)."""
        if provider not in ("mistral", "gradium"):
            return {"ok": False, "err": "unknown provider"}
        key = (key or "").strip()
        if not key:
            return {"ok": False, "err": "an API key"}
        if not re.fullmatch(r"[\x21-\x7e]{8,400}", key):
            return {"ok": False, "err": "this does not look like an API key"}
        had = bool(self.available())
        eng = MistralEngine(key) if provider == "mistral" else GradiumEngine(key)
        try:
            msg, ok = eng.test(), True
        except EngineError as e:
            msg, ok = str(e), False
            if msg.startswith("key refused"):
                self._tested(provider, False, msg)
                return {"ok": False, "err": msg, "tts": self.public()}
        keys = dict(self._keys())
        keys[provider] = key
        self.keys.save(keys)
        self._keys_cache = keys
        if ok:
            self.down.pop(provider, None)
        self._tested(provider, ok, msg)
        st = self.settings()
        if not had:  # the first voice: the Play button shows by itself (it can still be turned off)
            st["show_play"] = True
            self._put(st)
        return {"ok": True, "tested": ok, "out": msg, "tts": self.public()}

    def test_key(self, provider: str) -> dict:
        if provider == "local":
            try:
                return {"ok": True, "out": self.local.test()}
            except EngineError as e:
                return {"ok": False, "err": str(e)}
        k = self.key_of(provider)
        if not k:
            return {"ok": False, "err": "no key saved"}
        try:
            msg, ok = (MistralEngine(k) if provider == "mistral" else GradiumEngine(k)).test(), True
        except EngineError as e:
            msg, ok = str(e), False
        self._tested(provider, ok, msg)
        return {"ok": ok, "out": msg, "err": None if ok else msg, "tts": self.public()}

    def clear_key(self, provider: str) -> dict:
        keys = dict(self._keys())
        keys.pop(provider, None)
        self.keys.save(keys)
        self.down.pop(provider, None)
        self._keys_cache = keys
        tested = dict((self._get() or {}).get("tested") or {})
        tested.pop(provider, None)
        st = self.settings()
        st["tested"] = tested
        self._save({**{k: v for k, v in st.items() if k in SETTINGS_DEFAULT}, "tested": tested})
        if not self.available():
            st = self.settings()
            st["show_play"] = False
            self._put(st)
        self._cfg()
        return {"ok": True, "tts": self.public()}

    def _tested(self, provider: str, ok: bool, msg: str) -> None:
        cur = self._get() or {}
        tested = dict(cur.get("tested") or {})
        tested[provider] = {"ok": ok, "msg": msg[:300], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self._save({**{k: v for k, v in self.settings().items() if k in SETTINGS_DEFAULT}, "tested": tested})
        self._cfg()

    def local_op(self, op: str, voices: list[str] | None = None) -> dict:
        had = bool(self.available())
        if op not in ("install", "uninstall", "voices", "remove_voice"):
            return {"ok": False, "err": "unknown operation"}
        voices = [v for v in (voices or []) if v in PIPER_VOICES]
        if op == "install" and not voices:
            voices = list(PIPER_DEFAULT.values())
        r = self.installer.start(op, voices)
        if r.get("ok") and op in ("install", "voices") and not had:
            def first():  # the first voice: the Play button shows by itself, once the engine works
                while self.installer.busy():
                    time.sleep(0.5)
                if not had and self.available() and not self.settings().get("show_play"):
                    st = self.settings()
                    st["show_play"] = True
                    self._put(st)
            threading.Thread(target=first, daemon=True).start()
        return {**r, "tts": self.public()}

    # -- reading by itself: the operator's rules
    def rule_set(self, kind: str, key: str, label: str, on: bool) -> dict:
        if kind not in ("box", "project", "machine", "server") or not key:
            return {"ok": False, "err": "a box, project, machine or service"}
        st = self.settings()
        rk = f"{kind}:{key}"
        if on:
            st["auto"][rk] = {"type": kind, "key": key, "label": str(label or key)[:200],
                              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        else:
            st["auto"].pop(rk, None)
        self._put(st)
        return {"ok": True, "tts": self.public()}

    def rules_for(self, ctx: dict) -> list[str]:
        """Which of the operator's rules a message matches: ctx = {to, to_operator, machine, work, servers}."""
        st, hit = self.settings(), []
        if st.get("auto_operator") and ctx.get("to_operator"):
            hit.append("operator")
        for rk, r in st["auto"].items():
            t, k = r.get("type"), r.get("key")
            if (t == "box" and ctx.get("to") == k) or (t == "machine" and ctx.get("machine") == k) \
                    or (t == "project" and k == f"{ctx.get('machine')}\u0000{ctx.get('work') or ''}") \
                    or (t == "server" and k in (ctx.get("servers") or [])):
                hit.append(rk)
        return hit

    def watch(self, candidates: list[tuple[dict, dict]], source: str = "messages") -> None:
        """After each refresh: [(item, ctx)] of every message (or question) seen; the new ones a rule names are read
        in turn. The first call of each source only learns what was there before the console started."""
        ids = {it["id"] for it, _ in candidates}
        with self.lock:
            seen = self.seen.get(source)
            if seen is None:
                self.seen[source] = ids
                return
            new = [(it, ctx) for it, ctx in candidates if it["id"] not in seen and not any(it["id"] in v for v in self.seen.values())]
            seen |= ids
        if not new or not self.engine_name() or not self.can_play():
            return
        now = time.time()
        for it, ctx in sorted(new, key=lambda x: x[0].get("ts_s") or 0):
            if now - (it.get("ts_s") or now) > AUTO_FRESH_S:
                continue
            rules = self.rules_for(ctx)
            if rules:
                self.enqueue({**it, "rules": rules})

    # -- playback
    def ensure_player(self) -> Player | None:
        if self.player is None and self.player_error is None:
            try:
                self.player = Player(self._changed)
                self.player.on_end = self._ended
            except (ImportError, ValueError, RuntimeError) as e:
                self.player_error = f"no playback on this computer ({e}): install gir1.2-gstreamer-1.0 and gstreamer1.0-plugins-good"
                log(self.player_error)
        return self.player

    def player_position(self) -> float:
        return self.player.position() if self.player else 0.0

    def state(self) -> dict:
        p, cur = self.player, self.current
        st = "idle"
        if self.gen:
            st = "generating"
        elif p and cur and p.status != "idle":
            st = p.status
        out = {"v": self.version, "st": st, "rate": p.rate if p else self.settings()["rate"],
               "queue": [q["id"] for q in self.queue], "at": time.time(), "seeked": self.seeked}
        if self.gen:
            out["gen"] = dict(self.gen)
        it = (cur or {}).get("item") if st != "generating" else (self.gen or {}).get("item")
        if it:
            out.update(id=it["id"], title=it.get("subject") or "(no subject)", project=it.get("project"),
                       who=" → ".join(x for x in (it.get("from_name"), "you" if it.get("to_operator") else it.get("to_name")) if x),
                       manual=bool((cur if st != "generating" else self.gen or {}).get("manual")), lang=(cur or {}).get("lang"),
                       engine=(cur if st != "generating" else self.gen or {}).get("engine"), preferred=self.preferred())
        if p and cur and st in ("playing", "paused", "ended"):
            out.update(pos=round(p.position(), 3), dur=round(p.dur, 3))
        if self.error:
            out["error"] = dict(self.error)
        return out

    def _changed(self) -> None:
        self.version += 1
        st = self.state()
        for fn in list(self.listeners):
            try:
                fn(st)
            except Exception as e:  # noqa: BLE001 - a listener never stops playback
                log(f"listener: {type(e).__name__}: {e}")

    def audio(self, item: dict, progress=None, on_engine=None) -> tuple[Path, float, str]:
        """The audio of a message: from the cache, else made now (once, whoever else asks meanwhile waits for it), by
        the first voice of the chain that works: a cloud voice that fails hands over to the next one and is marked
        down; while one is, each reading asks for its test, in parallel (upgrade_test). progress(done, total): the
        pieces made so far; on_engine(name): the voice making it."""
        st = self.settings()
        chain = self.chain()
        if not chain:
            raise EngineError("no voice: set an API key or install the local engine (Settings, Reading aloud)")
        text, lang = speech_text(item, st.get("lang") or "auto", st.get("max_chars") or MAX_CHARS_DEFAULT)
        order = [e for e in chain if e not in self.down] + [e for e in chain if e in self.down]
        self.upgrade_test()  # every reading asked, even one played again: the voice that failed tested beside it
        for name in chain:  # made already (the chosen voice's first): played again at no cost, never made twice
            path = self._cache_path(name, item, text, lang, st)
            if path and path.is_file():
                os.utime(path)
                if on_engine:
                    on_engine(name)
                return path, self._wav_len(path), lang
        last: Exception | None = None
        for name in order:
            if on_engine:
                on_engine(name)
            try:
                return self._make(name, item, text, lang, st, progress)
            except EngineError as e:
                last = e
                if name != "local":
                    self.mark_down(name, e)
                log(f"audio of {item['id']} with {name}: {e}")
        raise last or EngineError("no voice could make it")

    def _cache_path(self, name: str, item: dict, text: str, lang: str, st: dict) -> Path | None:
        eng = self.engine(name)
        if eng is None:
            return None
        voice = ((st.get("voices") or {}).get(name) or {}).get(lang) or None
        digest = hashlib.sha256(json.dumps([name, voice, lang, text, getattr(eng, "MODEL", "")]).encode()).hexdigest()
        return self.cache.path(item["id"], digest)

    def _make(self, name: str, item: dict, text: str, lang: str, st: dict, progress=None) -> tuple[Path, float, str]:
        eng = self.engine(name)
        if eng is None:
            raise EngineError(f"{name}: not set up")
        voice = ((st.get("voices") or {}).get(name) or {}).get(lang) or None
        digest = hashlib.sha256(json.dumps([name, voice, lang, text, getattr(eng, "MODEL", "")]).encode()).hexdigest()
        path = self.cache.path(item["id"], digest)
        if path.is_file():
            os.utime(path)
            return path, self._wav_len(path), lang
        with self.lock:
            ev = self.generating.get(digest)
            mine = ev is None
            if mine:
                ev = self.generating[digest] = threading.Event()
        if not mine:
            ev.wait(900)
            if path.is_file():
                return path, self._wav_len(path), lang
            raise EngineError("the audio could not be made")
        try:
            parts = chunks(text, eng.piece)
            tell = (lambda n: progress(n, len(parts))) if progress else (lambda n: None)
            tell(0)
            if hasattr(eng, "synth_many"):  # the local engine: one load of the voice for every piece
                try:
                    pieces = eng.synth_many(parts, lang, voice, tell)
                except EngineError:
                    raise
                except Exception as e:  # noqa: BLE001
                    raise EngineError(f"{type(e).__name__}: {e}") from None
            else:  # the services: three pieces at a time
                pieces: list = [None] * len(parts)
                errs: list = []

                def one(i):
                    try:
                        pieces[i] = eng.synth(parts[i], lang, voice)
                    except Exception as e:  # noqa: BLE001 - the first error is said
                        errs.append(e)
                    tell(sum(1 for p in pieces if p is not None))
                for start in range(0, len(parts), 3):
                    ts = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(start, min(len(parts), start + 3))]
                    for t in ts:
                        t.start()
                    for t in ts:
                        t.join()
                    if errs:
                        e = errs[0]
                        raise e if isinstance(e, EngineError) else EngineError(f"{type(e).__name__}: {e}")
            path.parent.mkdir(parents=True, exist_ok=True)
            dur = join_wav(pieces, path)
            self.prune(spare=path)
            return path, dur, lang
        finally:
            with self.lock:
                self.generating.pop(digest, None)
            ev.set()

    @staticmethod
    def _wav_len(path: Path) -> float:
        try:
            with wave.open(str(path)) as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except (OSError, wave.Error, EOFError):
            return 0.0

    def play(self, item: dict, manual: bool = True) -> dict:
        """Read a message now (a manual play replaces what plays; the auto-reading queue waits behind it)."""
        if self.ensure_player() is None:
            return {"ok": False, "err": self.player_error}
        if not self.engine_name():
            return {"ok": False, "err": "no voice: set an API key or install the local engine (Settings, Reading aloud)"}
        self.upgrade_test()  # a reading asked: the voice that failed is tested beside it (at most every 30 s)
        cur = self.current
        if cur and cur["item"]["id"] == item["id"] and self.player.status in ("paused", "ended", "playing"):
            if self.player.status != "playing":
                cur["manual"] = cur["manual"] or manual
                self.player.play()
            return {"ok": True, "state": self.state()}
        with self.lock:
            if self.gen and self.gen.get("id") == item["id"]:
                return {"ok": True, "state": self.state()}  # already being made
            self.gen = {"id": item["id"], "item": item, "manual": manual, "done": 0, "total": 0}
            self.error = None
        threading.Thread(target=self._load, args=(item, manual), daemon=True, name="tts-gen").start()
        return {"ok": True, "state": self.state()}

    def _load(self, item: dict, manual: bool) -> None:
        """Make the audio of an item already claimed in self.gen (play, _next), then load it into the player.
        Runs in a thread of its own: a service may take seconds, the refresh and the page never wait for it."""
        self._changed()
        def progress(done, total):
            if self.gen and self.gen.get("id") == item["id"]:
                self.gen.update(done=done, total=total)
                self._changed()
        made_by = {}

        def on_engine(name):
            made_by["engine"] = name
            if self.gen and self.gen.get("id") == item["id"] and self.gen.get("engine") != name:
                self.gen.update(engine=name, done=0, total=0)
                self._changed()
        try:
            path, dur, lang = self.audio(item, progress, on_engine)
        except Exception as e:  # noqa: BLE001 - said in the page and the log
            log(f"audio of {item['id']}: {e}")
            with self.lock:
                if self.gen and self.gen.get("id") == item["id"]:
                    self.gen = None
                self.error = {"id": item["id"], "err": str(e)[:400], "at": time.time()}
            self._changed()
            if not manual:
                self._next()  # the next one of the queue (this thread is already apart)
            return
        with self.lock:
            if not self.gen or self.gen.get("id") != item["id"]:
                return  # stopped (or another one asked) meanwhile
            self.gen = None
            if self.current and self.current["item"]["id"] != item["id"]:
                self.history = (self.history + [self.current])[-30:]
            self.current = {"item": item, "manual": manual, "path": path, "lang": lang, "engine": made_by.get("engine")}
        self.player.load(path, dur, self.settings()["rate"] if not self.player or self.player.status == "idle" else self.player.rate)

    def enqueue(self, item: dict) -> None:
        """A message to read by itself: after what plays (or waits paused), never over it."""
        with self.lock:
            if any(q["id"] == item["id"] for q in self.queue) or (self.current and self.current["item"]["id"] == item["id"]) \
                    or (self.gen and self.gen.get("id") == item["id"]):
                return
            self.queue.append(item)
        self._changed()
        threading.Thread(target=self._next, daemon=True, name="tts-next").start()

    def _busy(self) -> bool:
        return self.gen is not None or bool(self.player and self.current and self.player.status in ("playing", "paused"))

    def _next(self, force: bool = False) -> None:
        """The first message of the queue, when nothing is being made or played (force: Next, over what plays)."""
        with self.lock:
            if not self.queue or (self._busy() and not force):
                return
            item = self.queue.pop(0)
            self.gen = {"id": item["id"], "item": item, "manual": False, "done": 0, "total": 0}
            self.error = None
        self._load(item, False)

    def _ended(self) -> None:
        """A message read to its end: the next of the queue, else the player stays on it (to play it again)."""
        if self.queue:
            threading.Thread(target=self._next, daemon=True).start()

    def ctl(self, op: str, value=None) -> dict:
        p = self.ensure_player()
        if p is None:
            return {"ok": False, "err": self.player_error}
        if op in ("play", "toggle", "next", "prev"):
            self.upgrade_test()  # listening again is a reading asked too
        if op == "pause":
            p.pause()
        elif op == "play":
            p.play()
        elif op == "toggle":
            p.pause() if p.status == "playing" else p.play()
        elif op == "stop":  # the queue too: Stop silences the console
            with self.lock:
                self.queue.clear()
                self.gen = None
            p.stop()
            with self.lock:
                self.current = None
            self._changed()
        elif op == "seek":
            p.seek(float(value or 0))
            self.seeked += 1
        elif op == "seek_by":
            p.seek(p.position() + float(value or 0))
            self.seeked += 1
        elif op == "rate":
            p.set_rate(float(value or 1))
            st = self.settings()
            st["rate"] = p.rate  # the next message starts at this speed too
            self._save({k: v for k, v in st.items() if k in SETTINGS_DEFAULT})
        elif op == "next":
            if self.queue:
                p.pause()
                threading.Thread(target=self._next, args=(True,), daemon=True, name="tts-next").start()
            elif p.status != "idle":
                p.seek(p.dur)
        elif op == "prev":
            if p.position() > 3 or not self.history:
                p.seek(0)
                self.seeked += 1
            else:
                with self.lock:
                    prev = self.history.pop()
                    if self.current:
                        self.queue.insert(0, self.current["item"])
                    self.current = prev
                try:
                    os.utime(prev["path"])  # played again: kept as long again
                except OSError:
                    pass
                p.load(prev["path"], self._wav_len(prev["path"]), p.rate)
        elif op == "dismiss_error":
            self.error = None
            self._changed()
        else:
            return {"ok": False, "err": "unknown control"}
        return {"ok": True, "state": self.state()}

    def forget(self, ids) -> int:
        """The purge: these messages' audio goes too (unless it plays now)."""
        ids = set(ids)
        if self.current and self.current["item"]["id"] in ids:
            ids.discard(self.current["item"]["id"])
        return self.cache.remove(ids)
