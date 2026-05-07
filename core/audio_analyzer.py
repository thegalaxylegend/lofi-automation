import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioAnalyzer:
    """
    Extracts mathematical data from the MP3 to drive the video effects.
    This ensures no "blind edits"—everything is synced to the math.
    """

    @staticmethod
    def analyze_audio(audio_path: str | Path) -> dict:
        """
        Analyze an MP3 file and return BPM, beat timestamps, and energy.
        Returns safe defaults if librosa fails (e.g., corrupt audio, missing deps).
        """
        audio_path = str(audio_path)
        logger.info("Analyzing audio mathematics for %s...", audio_path)

        try:
            import librosa
            import numpy as np

            # Load audio (preserve native sample rate for accurate BPM)
            y, sr = librosa.load(audio_path, sr=None)

            # Calculate BPM (Tempo) and Beat Frames
            tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)

            # Handle numpy array return type for tempo
            if hasattr(tempo, '__len__'):
                tempo = float(tempo[0]) if len(tempo) > 0 else 90.0
            else:
                tempo = float(tempo)

            # Guard against ambient tracks with no transients
            if len(beat_frames) == 0:
                beat_frames = np.array([0])
                if tempo == 0.0:
                    tempo = 80.0

            # Convert frames to exact timestamps (seconds)
            beat_times = librosa.frames_to_time(beat_frames, sr=sr)

            # Calculate overall energy (RMS amplitude)
            y_trimmed, _ = librosa.effects.trim(y)
            rms = librosa.feature.rms(y=y_trimmed)[0]
            avg_energy = float(rms.mean())

            logger.info(
                "Math extracted: BPM=%.1f, Beats=%d, Energy=%.4f",
                tempo, len(beat_times), avg_energy,
            )

            return {
                "bpm": tempo,
                "beat_times": [float(t) for t in beat_times],
                "energy": avg_energy,
            }

        except ImportError:
            logger.warning(
                "librosa not installed. Using safe defaults (BPM=90, energy=0.1). "
                "Install with: pip install librosa"
            )
            return {"bpm": 90.0, "beat_times": [0.0], "energy": 0.1}

        except Exception as exc:
            logger.error(
                "Audio analysis failed for %s: %s. Using safe defaults.",
                audio_path, exc,
            )
            return {"bpm": 90.0, "beat_times": [0.0], "energy": 0.1}
