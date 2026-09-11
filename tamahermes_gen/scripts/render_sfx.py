from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 22050
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "tamahermes" / "sfx"
RENDER_TRANSPOSE_SEMITONES = -5

SEMITONES = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}


@dataclass(frozen=True)
class Hit:
    notes: tuple[str, ...] | None
    duration: float
    gap: float = 0.018
    velocity: float = 1.0


@dataclass(frozen=True)
class Voice:
    triangle: float
    square: float
    sine: float
    duty: float = 0.5
    attack: float = 0.018
    release: float = 0.045
    lowpass_hz: float = 4300.0
    quantize_steps: int = 72


@dataclass(frozen=True)
class Motif:
    hits: tuple[Hit, ...]
    voice: Voice
    target_peak_dbfs: float
    echo_delay: float = 0.0
    echo_mix: float = 0.0


SOFT = Voice(triangle=0.74, square=0.10, sine=0.16, attack=0.022, release=0.055, lowpass_hz=3400.0, quantize_steps=96)
CHIP = Voice(triangle=0.64, square=0.20, sine=0.16, attack=0.020, release=0.052, lowpass_hz=3700.0, quantize_steps=88)
ROUND = Voice(triangle=0.70, square=0.12, sine=0.18, attack=0.024, release=0.062, lowpass_hz=3100.0, quantize_steps=112)
GLOW = Voice(triangle=0.74, square=0.08, sine=0.18, attack=0.026, release=0.070, lowpass_hz=2850.0, quantize_steps=112)


MOTIFS: dict[str, Motif] = {
    "care.wav": Motif(
        hits=(Hit(("E5",), 0.078), Hit(("G5",), 0.082), Hit(("E5",), 0.110, 0.0, 0.82)),
        voice=ROUND,
        target_peak_dbfs=-23.4,
    ),
    "work.wav": Motif(
        hits=(Hit(("C5",), 0.064), Hit(("D5",), 0.064), Hit(("E5",), 0.092, 0.0, 0.86)),
        voice=SOFT,
        target_peak_dbfs=-23.0,
    ),
    "review_opened.wav": Motif(
        hits=(Hit(("D5",), 0.070), Hit(("G5",), 0.080), Hit(("A5",), 0.095, 0.0, 0.84)),
        voice=ROUND,
        target_peak_dbfs=-22.8,
    ),
    "task_success.wav": Motif(
        hits=(Hit(("C5",), 0.068), Hit(("E5",), 0.070), Hit(("G5",), 0.074), Hit(("C6",), 0.135, 0.0, 0.66)),
        voice=GLOW,
        target_peak_dbfs=-22.2,
        echo_delay=0.050,
        echo_mix=0.08,
    ),
    "task_failure.wav": Motif(
        hits=(Hit(("E5",), 0.078), Hit(("D5",), 0.082), Hit(("C5",), 0.086), Hit(("A4",), 0.130, 0.0, 0.78)),
        voice=SOFT,
        target_peak_dbfs=-22.4,
    ),
    "recovery.wav": Motif(
        hits=(Hit(("C5",), 0.066), Hit(("D5",), 0.066), Hit(("E5",), 0.072), Hit(("G5",), 0.122, 0.0, 0.86)),
        voice=ROUND,
        target_peak_dbfs=-22.0,
        echo_delay=0.050,
        echo_mix=0.08,
    ),
    "rest.wav": Motif(
        hits=(Hit(("G5",), 0.092), Hit(("E5",), 0.105), Hit(("C5",), 0.155, 0.0, 0.72)),
        voice=Voice(triangle=0.80, square=0.04, sine=0.16, attack=0.028, release=0.076, lowpass_hz=2800.0, quantize_steps=112),
        target_peak_dbfs=-24.0,
    ),
    "hatch.wav": Motif(
        hits=(Hit(("C5",), 0.072), Hit(("G5",), 0.074), Hit(("A5",), 0.082), Hit(("C6",), 0.145, 0.0, 0.86)),
        voice=CHIP,
        target_peak_dbfs=-21.3,
        echo_delay=0.045,
        echo_mix=0.10,
    ),
    "evolve.wav": Motif(
        hits=(
            Hit(("C5",), 0.062),
            Hit(("E5",), 0.064),
            Hit(("G5",), 0.068),
            Hit(("A5",), 0.074),
            Hit(("C6",), 0.078, velocity=0.68),
            Hit(("G5",), 0.132, 0.0, 0.78),
        ),
        voice=GLOW,
        target_peak_dbfs=-22.4,
        echo_delay=0.050,
        echo_mix=0.07,
    ),
}


def note_frequency(note: str) -> float:
    pitch = note[:-1].upper()
    octave = int(note[-1])
    midi = 12 * (octave + 1) + SEMITONES[pitch] + RENDER_TRANSPOSE_SEMITONES
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def oscillator(phase: float, voice: Voice) -> float:
    cycle = phase % 1.0
    triangle = 4.0 * abs(cycle - 0.5) - 1.0
    square = 1.0 if cycle < voice.duty else -1.0
    sine = math.sin(2.0 * math.pi * phase)
    return voice.triangle * triangle + voice.square * square + voice.sine * sine


def envelope(t: float, duration: float, voice: Voice) -> float:
    attack = min(voice.attack, duration * 0.35)
    release = min(voice.release, duration * 0.48)
    value = 1.0
    if attack > 0.0 and t < attack:
        value *= 0.5 - 0.5 * math.cos(math.pi * t / attack)
    if release > 0.0 and t > duration - release:
        remaining = max(duration - t, 0.0)
        value *= 0.5 - 0.5 * math.cos(math.pi * remaining / release)
    return value


def lowpass(samples: list[float], cutoff_hz: float) -> list[float]:
    if not samples:
        return samples
    dt = 1.0 / SAMPLE_RATE
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    alpha = dt / (rc + dt)
    out: list[float] = []
    y = 0.0
    for sample in samples:
        y += alpha * (sample - y)
        out.append(y)
    return out


def quantize(samples: list[float], steps: int) -> list[float]:
    if steps <= 0:
        return samples
    return [round(sample * steps) / steps for sample in samples]


def normalize(samples: list[float], target_peak_dbfs: float) -> list[float]:
    peak = max((abs(sample) for sample in samples), default=0.0)
    if peak == 0.0:
        return samples
    target = 10.0 ** (target_peak_dbfs / 20.0)
    gain = target / peak
    return [max(-1.0, min(1.0, sample * gain)) for sample in samples]


def add_echo(samples: list[float], delay: float, mix: float) -> list[float]:
    if delay <= 0.0 or mix <= 0.0:
        return samples
    delay_samples = int(delay * SAMPLE_RATE)
    out = samples[:]
    out.extend([0.0] * delay_samples)
    for index, sample in enumerate(samples):
        echo_index = index + delay_samples
        out[echo_index] += sample * mix
    return out


def render_hit(hit: Hit, voice: Voice) -> list[float]:
    frame_count = int(hit.duration * SAMPLE_RATE)
    if hit.notes is None:
        return [0.0] * frame_count
    freqs = [note_frequency(note) for note in hit.notes]
    samples: list[float] = []
    scale = 1.0 / math.sqrt(len(freqs))
    for frame in range(frame_count):
        t = frame / SAMPLE_RATE
        tone = sum(oscillator(freq * t, voice) for freq in freqs) * scale
        samples.append(tone * envelope(t, hit.duration, voice) * hit.velocity)
    return samples


def render_motif(motif: Motif) -> list[float]:
    samples: list[float] = []
    for hit in motif.hits:
        samples.extend(render_hit(hit, motif.voice))
        if hit.gap > 0.0:
            samples.extend([0.0] * int(hit.gap * SAMPLE_RATE))
    samples = add_echo(samples, motif.echo_delay, motif.echo_mix)
    samples = lowpass(samples, motif.voice.lowpass_hz)
    samples = quantize(samples, motif.voice.quantize_steps)
    return normalize(samples, motif.target_peak_dbfs)


def write_wav(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        data = bytearray()
        for sample in samples:
            value = int(max(-1.0, min(1.0, sample)) * 32767.0)
            data.extend(value.to_bytes(2, "little", signed=True))
        handle.writeframes(bytes(data))


def main() -> None:
    for filename, motif in MOTIFS.items():
        write_wav(OUTPUT_DIR / filename, render_motif(motif))


if __name__ == "__main__":
    main()
