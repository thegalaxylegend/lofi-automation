"""
Audio Event Map — Beat & Instrument Detection Engine.

Uses librosa to analyze audio and produce a frame-accurate event map:
  - Kick drums (low-frequency onsets)
  - Snare hits (mid-frequency onsets)
  - Bass drops (sudden RMS energy spikes)
  - Silence/breathing zones (low RMS energy)
  - Building tension (rising RMS over time)
  - Section-level energy for dynamic cutting rhythm

This event map drives the VFX engine — every visual effect traces
back to a specific audio event, never hardcoded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AudioEvent:
    """A single audio event at a specific timestamp."""
    timestamp: float  # seconds
    event_type: str   # kick, snare, bass_drop, silence_start, silence_end, build_start, build_peak
    intensity: float = 1.0  # 0.0 to 1.0 (how strong the event is)


@dataclass
class AudioEventMap:
    """Complete audio analysis result for the VFX engine."""
    duration: float = 0.0
    bpm: float = 120.0
    beat_timestamps: list[float] = field(default_factory=list)  # All detected beats
    kick_timestamps: list[float] = field(default_factory=list)  # Low-freq onsets
    snare_timestamps: list[float] = field(default_factory=list) # Mid-freq onsets
    bass_drops: list[float] = field(default_factory=list)       # Major energy spikes
    breathing_zones: list[tuple[float, float]] = field(default_factory=list)  # (start, end) quiet zones
    events: list[AudioEvent] = field(default_factory=list)      # All events sorted by time
    rms_curve: list[float] = field(default_factory=list)        # RMS energy per second
    peak_rms: float = 0.0

    def get_events_in_range(self, start: float, end: float) -> list[AudioEvent]:
        """Get all events within a time range."""
        return [e for e in self.events if start <= e.timestamp <= end]

    def is_breathing_zone(self, timestamp: float) -> bool:
        """Check if a timestamp falls within a breathing zone."""
        return any(start <= timestamp <= end for start, end in self.breathing_zones)

    def get_cutting_interval(self, energy: str, bpm: float | None = None) -> float:
        """Calculate the cutting interval based on section energy and BPM."""
        effective_bpm = bpm or self.bpm
        beat_duration = 60.0 / max(effective_bpm, 60)  # seconds per beat

        intervals = {
            "very_low": beat_duration * 16,  # Hold for 16 beats (very slow cuts)
            "fading": beat_duration * 16,
            "low": beat_duration * 8,         # Cut every 8 beats
            "medium": beat_duration * 4,      # Cut every 4 beats
            "high": beat_duration * 2,         # Cut every 2 beats
            "very_high": max(beat_duration, 1.5),  # Cut every beat (min 1.5s cooldown)
        }
        return intervals.get(energy, beat_duration * 4)


class AudioAnalyzer:
    """
    Analyzes audio to produce an AudioEventMap for the VFX engine.

    Uses librosa for spectral analysis with downsampling to minimize
    memory usage in CI/CD environments.
    """

    SAMPLE_RATE = 11025  # Downsampled for 75% RAM savings
    RMS_WINDOW = 1.0     # 1-second RMS windows
    BREATHING_THRESHOLD = 0.30  # Below 30% of peak RMS = breathing zone
    BREATHING_MIN_DURATION = 2.0  # Minimum 2 seconds to qualify
    BUILD_MIN_DURATION = 4.0  # Minimum 4 seconds of rising RMS = build

    def analyze(self, audio_path: str | Path) -> AudioEventMap:
        """
        Analyze an audio file and produce a complete AudioEventMap.

        Args:
            audio_path: Path to the MP3/audio file.

        Returns:
            AudioEventMap with all detected events.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("AudioAnalyzer: analyzing %s", audio_path.name)

        try:
            import librosa
            import numpy as np
        except ImportError:
            logger.error(
                "librosa not installed. Install with: pip install librosa numpy"
            )
            return self._fallback_event_map(audio_path)

        # Load audio at reduced sample rate
        y, sr = librosa.load(str(audio_path), sr=self.SAMPLE_RATE, mono=True)
        duration = librosa.get_duration(y=y, sr=sr)
        logger.info("Loaded: %.1fs at %dHz", duration, sr)

        event_map = AudioEventMap(duration=duration)

        # 1. Global beat detection
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        # Handle both scalar and array tempo returns
        if hasattr(tempo, '__len__'):
            event_map.bpm = float(tempo[0]) if len(tempo) > 0 else 120.0
        else:
            event_map.bpm = float(tempo)
        event_map.beat_timestamps = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        logger.info("BPM: %.1f, Beats: %d", event_map.bpm, len(event_map.beat_timestamps))

        # 2. Spectral band onset detection (kick vs snare)
        event_map.kick_timestamps = self._detect_band_onsets(y, sr, 20, 150)
        event_map.snare_timestamps = self._detect_band_onsets(y, sr, 200, 2000)
        logger.info(
            "Kicks: %d, Snares: %d",
            len(event_map.kick_timestamps), len(event_map.snare_timestamps),
        )

        # 3. RMS energy curve (per-second)
        event_map.rms_curve = self._compute_rms_curve(y, sr)
        event_map.peak_rms = max(event_map.rms_curve) if event_map.rms_curve else 0.001

        # 4. Bass drops (sudden energy spikes)
        event_map.bass_drops = self._detect_bass_drops(event_map.rms_curve, event_map.peak_rms)
        logger.info("Bass drops: %d", len(event_map.bass_drops))

        # 5. Breathing zones (quiet moments)
        event_map.breathing_zones = self._detect_breathing_zones(
            event_map.rms_curve, event_map.peak_rms
        )
        logger.info("Breathing zones: %d", len(event_map.breathing_zones))

        # 6. Build all events into a unified timeline
        event_map.events = self._build_event_timeline(event_map)
        event_map.events.sort(key=lambda e: e.timestamp)
        logger.info("Total audio events: %d", len(event_map.events))

        # Free memory
        del y
        logger.info("✅ AudioAnalyzer complete.")

        return event_map

    def _detect_band_onsets(
        self, y, sr: int, freq_low: int, freq_high: int
    ) -> list[float]:
        """Detect onsets in a specific frequency band."""
        import librosa
        import numpy as np

        # Compute STFT
        S = np.abs(librosa.stft(y))
        freqs = librosa.fft_frequencies(sr=sr)

        # Create band mask
        band_mask = (freqs >= freq_low) & (freqs <= freq_high)
        if not band_mask.any():
            return []

        # Extract band energy
        band_energy = S[band_mask, :].sum(axis=0)

        # Onset detection on band energy
        onset_env = librosa.onset.onset_strength(
            S=librosa.power_to_db(band_energy[np.newaxis, :] ** 2),
            sr=sr,
        )
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env, sr=sr, units="time"
        )

        return onsets.tolist()

    def _compute_rms_curve(self, y, sr: int) -> list[float]:
        """Compute per-second RMS energy."""
        import numpy as np

        samples_per_window = int(sr * self.RMS_WINDOW)
        rms_values = []
        for i in range(0, len(y), samples_per_window):
            chunk = y[i : i + samples_per_window]
            if len(chunk) > 0:
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                rms_values.append(rms)

        return rms_values

    def _detect_bass_drops(
        self, rms_curve: list[float], peak_rms: float
    ) -> list[float]:
        """Detect bass drops: sudden RMS spikes after a dip."""
        drops = []
        if len(rms_curve) < 3:
            return drops

        for i in range(2, len(rms_curve)):
            prev_avg = (rms_curve[i - 2] + rms_curve[i - 1]) / 2
            current = rms_curve[i]

            # A drop is when current is >2x the previous average
            # and above 40% of peak RMS
            if prev_avg > 0 and current > prev_avg * 2.0 and current > peak_rms * 0.4:
                drops.append(float(i))  # timestamp in seconds

        return drops

    def _detect_breathing_zones(
        self, rms_curve: list[float], peak_rms: float
    ) -> list[tuple[float, float]]:
        """Detect quiet sections where VFX should be disabled."""
        threshold = peak_rms * self.BREATHING_THRESHOLD
        zones = []
        zone_start = None

        for i, rms in enumerate(rms_curve):
            if rms < threshold:
                if zone_start is None:
                    zone_start = float(i)
            else:
                if zone_start is not None:
                    zone_end = float(i)
                    if (zone_end - zone_start) >= self.BREATHING_MIN_DURATION:
                        zones.append((zone_start, zone_end))
                    zone_start = None

        # Handle trailing zone
        if zone_start is not None:
            zone_end = float(len(rms_curve))
            if (zone_end - zone_start) >= self.BREATHING_MIN_DURATION:
                zones.append((zone_start, zone_end))

        return zones

    def _build_event_timeline(self, event_map: AudioEventMap) -> list[AudioEvent]:
        """Combine all detected events into a unified sorted timeline."""
        events = []

        # Kick events
        for t in event_map.kick_timestamps:
            events.append(AudioEvent(timestamp=t, event_type="kick", intensity=0.7))

        # Snare events
        for t in event_map.snare_timestamps:
            events.append(AudioEvent(timestamp=t, event_type="snare", intensity=0.5))

        # Bass drops (high intensity)
        for t in event_map.bass_drops:
            events.append(AudioEvent(timestamp=t, event_type="bass_drop", intensity=1.0))

        # Breathing zone boundaries
        for start, end in event_map.breathing_zones:
            events.append(AudioEvent(timestamp=start, event_type="silence_start", intensity=0.0))
            events.append(AudioEvent(timestamp=end, event_type="silence_end", intensity=0.0))

        # Detect building tension (rising RMS over 4+ seconds)
        rms = event_map.rms_curve
        if len(rms) >= int(self.BUILD_MIN_DURATION):
            rising_start = None
            for i in range(1, len(rms)):
                if rms[i] > rms[i - 1]:
                    if rising_start is None:
                        rising_start = i - 1
                else:
                    if rising_start is not None:
                        duration = i - rising_start
                        if duration >= self.BUILD_MIN_DURATION:
                            events.append(AudioEvent(
                                timestamp=float(rising_start),
                                event_type="build_start",
                                intensity=0.6,
                            ))
                            events.append(AudioEvent(
                                timestamp=float(i),
                                event_type="build_peak",
                                intensity=0.9,
                            ))
                        rising_start = None

        return events

    def _fallback_event_map(self, audio_path: Path) -> AudioEventMap:
        """Minimal fallback if librosa is not installed."""
        import subprocess

        # Try to get duration via ffprobe
        duration = 180.0
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(audio_path)],
                capture_output=True, text=True, timeout=15,
            )
            duration = float(result.stdout.strip())
        except Exception:
            pass

        # Generate evenly-spaced fake beats
        bpm = 100.0
        beat_interval = 60.0 / bpm
        beats = [i * beat_interval for i in range(int(duration / beat_interval))]

        logger.warning(
            "Using fallback AudioEventMap (no librosa). Duration=%.1fs, fake BPM=%.0f",
            duration, bpm,
        )

        return AudioEventMap(
            duration=duration,
            bpm=bpm,
            beat_timestamps=beats,
        )
