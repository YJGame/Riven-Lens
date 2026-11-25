"""Lightweight zero-dependency audio cue engine with bundled rare-drop chime & custom WAV support.

Provides:
  1. `play_normal_cue()`: gentle 660Hz, 60ms single beep for standard scan / F9 done.
  2. `play_rare_drop_cue()` (alias `play_god_roll_cue()`): plays the bundled 1.0s stereo
     crystal chime (`assets/sounds/rare_drop.wav`) — or a user-selected custom `.wav` file —
     whenever an S-grade positive stat or S-grade positive-benefit negative stat appears.

Playback uses Windows `winsound.PlaySound(..., SND_MEMORY | SND_ASYNC)` or Linux
`paplay`/`aplay` in a daemon thread with complete exception suppression so audio
never blocks the UI or crashes on headless systems.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import List, Optional, Tuple


def get_default_rare_sound_path() -> Path:
    """Return path to bundled `assets/sounds/rare_drop.wav` (supports PyInstaller `_MEIPASS`)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "assets" / "sounds" / "rare_drop.wav"
        if cand.is_file():
            return cand
    return Path(__file__).resolve().parent.parent / "assets" / "sounds" / "rare_drop.wav"


def validate_wav_file(path: str | Path) -> Tuple[bool, str]:
    """Validate that `path` points to a readable, non-empty PCM `.wav` file."""
    p = Path(path)
    if not p.is_file():
        return False, "文件不存在"
    try:
        with wave.open(str(p), "rb") as wf:
            nframes = wf.getnframes()
            rate = wf.getframerate()
            sw = wf.getsampwidth()
            ch = wf.getnchannels()
            if nframes <= 0 or rate <= 0 or sw not in (1, 2, 3, 4) or ch <= 0:
                return False, "无效的 WAV 音频参数"
        return True, p.name
    except Exception as exc:
        return False, f"无法解析 WAV 文件：{exc}"


def _synthesize_wav_bytes(
    notes: List[Tuple[float, int, float]],
    sample_rate: int = 22050,
    gap_ms: int = 18,
) -> bytes:
    """Synthesize a sequence of `(freq_hz, duration_ms, volume)` sine tones into in-memory WAV bytes."""
    frames: List[int] = []
    gap_samples = int(sample_rate * (gap_ms / 1000.0))

    for idx, (freq_hz, duration_ms, volume) in enumerate(notes):
        n_samples = max(1, int(sample_rate * (duration_ms / 1000.0)))
        fade_samples = min(n_samples // 4, int(sample_rate * 0.008))  # 8ms soft envelope
        amp = max(0.0, min(1.0, float(volume))) * 32767.0
        for i in range(n_samples):
            env = 1.0
            if fade_samples > 0:
                if i < fade_samples:
                    env = i / float(fade_samples)
                elif i >= n_samples - fade_samples:
                    env = (n_samples - 1 - i) / float(fade_samples)
            sample = math.sin(2.0 * math.pi * freq_hz * (i / float(sample_rate)))
            val = int(max(-32767, min(32767, round(sample * amp * env))))
            frames.append(val)
        if idx < len(notes) - 1 and gap_samples > 0:
            frames.extend([0] * gap_samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(frames)}h", *frames))
    return buf.getvalue()


def _load_default_rare_wav() -> bytes:
    """Load bundled `assets/sounds/rare_drop.wav`, falling back to a synthesized chime if absent."""
    default_path = get_default_rare_sound_path()
    ok, _ = validate_wav_file(default_path)
    if ok:
        try:
            return default_path.read_bytes()
        except Exception:
            pass
    return _synthesize_wav_bytes(
        [(880.0, 85, 0.28), (1174.66, 145, 0.30)],
        gap_ms=22,
    )


class AudioCuePlayer:
    """Thread-safe, non-blocking lightweight audio feedback player."""

    def __init__(self, enabled: bool = True, custom_rare_sound_path: str = "") -> None:
        self._enabled = bool(enabled)
        self._normal_wav: bytes = _synthesize_wav_bytes([(660.0, 60, 0.24)])
        self._default_rare_wav: bytes = _load_default_rare_wav()
        self._custom_rare_path: str = ""
        self._custom_rare_wav: Optional[bytes] = None
        self._linux_cache_paths: dict[str, Path] = {}
        if custom_rare_sound_path:
            self.set_custom_rare_sound(custom_rare_sound_path)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    @property
    def custom_rare_sound_path(self) -> str:
        return self._custom_rare_path

    @property
    def normal_wav_bytes(self) -> bytes:
        return self._normal_wav

    @property
    def active_rare_wav_bytes(self) -> bytes:
        return self._custom_rare_wav if self._custom_rare_wav is not None else self._default_rare_wav

    @property
    def god_roll_wav_bytes(self) -> bytes:
        """Backwards-compatible alias returning the active rare-drop WAV bytes."""
        return self.active_rare_wav_bytes

    def set_custom_rare_sound(self, path: Optional[str | Path]) -> Tuple[bool, str]:
        """Replace rare-drop sound with a user-selected `.wav` file, or reset to default when empty."""
        raw_str = str(path or "").strip()
        if not raw_str:
            self._custom_rare_path = ""
            self._custom_rare_wav = None
            return True, "rare_drop.wav"

        ok, info = validate_wav_file(raw_str)
        if not ok:
            return False, info
        try:
            data = Path(raw_str).read_bytes()
            self._custom_rare_path = str(Path(raw_str).resolve())
            self._custom_rare_wav = data
            return True, Path(raw_str).name
        except Exception as exc:
            return False, str(exc)

    def play_normal_cue(self, force: bool = False) -> bool:
        """Play short soft beep (660Hz, 60ms) for completed scan / F9 capture."""
        if not self._enabled and not force:
            return False
        self._dispatch_play("normal", self._normal_wav)
        return True

    def play_rare_drop_cue(self, force: bool = False) -> bool:
        """Play rare-drop crystal chime (`assets/sounds/rare_drop.wav` or user custom WAV)."""
        if not self._enabled and not force:
            return False
        wav_bytes = self.active_rare_wav_bytes
        digest = hashlib.md5(wav_bytes[:4096]).hexdigest()[:8]
        self._dispatch_play(f"rare_{digest}", wav_bytes)
        return True

    def play_god_roll_cue(self, force: bool = False) -> bool:
        """Alias for `play_rare_drop_cue`."""
        return self.play_rare_drop_cue(force=force)

    def play_preview(self) -> bool:
        """Play preview of the active rare-drop chime from Control Panel regardless of mute state."""
        return self.play_rare_drop_cue(force=True)

    def _dispatch_play(self, tag: str, wav_bytes: bytes) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        threading.Thread(
            target=self._play_sync,
            args=(tag, wav_bytes),
            name=f"AudioCue-{tag}",
            daemon=True,
        ).start()

    def _resolve_playback_wav_path(self, tag: str, wav_bytes: bytes) -> Path:
        """Resolve a real on-disk `.wav` path for async Win32 / Linux playback.

        Note: Python's `winsound.PlaySound` raises `RuntimeError: Cannot play
        asynchronously from memory` if `SND_MEMORY | SND_ASYNC` are combined, so
        playing via `SND_FILENAME | SND_ASYNC` on a real `.wav` path is both
        completely non-blocking and 100% reliable across all Windows versions.
        """
        if tag.startswith("rare_"):
            if self._custom_rare_path and Path(self._custom_rare_path).is_file():
                return Path(self._custom_rare_path)
            default_p = get_default_rare_sound_path()
            if default_p.is_file() and wav_bytes == self._default_rare_wav:
                return default_p

        cached = self._linux_cache_paths.get(tag)
        if cached is not None and cached.is_file():
            return cached
        tmp_path = Path(tempfile.gettempdir()) / f"riven_lens_cue_{tag}.wav"
        tmp_path.write_bytes(wav_bytes)
        self._linux_cache_paths[tag] = tmp_path
        return tmp_path

    def _play_sync(self, tag: str, wav_bytes: bytes) -> None:
        try:
            wav_path = self._resolve_playback_wav_path(tag, wav_bytes)
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(
                    str(wav_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return

            # Linux / POSIX fallback via PipeWire (`pw-play`), PulseAudio (`paplay`), or ALSA (`aplay`)
            player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("aplay")
            if not player:
                return
            subprocess.run(
                [player, str(wav_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except Exception:
            pass


# Shared singleton audio cue player
AUDIO_CUE = AudioCuePlayer(enabled=True)
