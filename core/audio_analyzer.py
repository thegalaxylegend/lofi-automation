import librosa
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
        """
        audio_path = str(audio_path)
        logger.info(f"Analyzing audio mathematics for {audio_path}...")
        
        # Load audio (preserve native sample rate for accurate BPM)
        y, sr = librosa.load(audio_path, sr=None)
        
        # Calculate BPM (Tempo) and Beat Frames
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(tempo[0]) if isinstance(tempo, (list, tuple, type(y))) else float(tempo)
        
        # Guard against ambient tracks with no transients
        if len(beat_frames) == 0:
            import numpy as np
            beat_frames = np.array([0])
            if tempo == 0.0:
                tempo = 80.0
        
        # Convert frames to exact timestamps (seconds)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        
        # Calculate overall energy (RMS amplitude) after trimming silent intros/outros
        y_trimmed, _ = librosa.effects.trim(y)
        rms = librosa.feature.rms(y=y_trimmed)[0]
        avg_energy = float(rms.mean())
        
        logger.info(f"Math extracted: BPM={tempo:.1f}, Beats={len(beat_times)}, Energy={avg_energy:.4f}")
        
        return {
            "bpm": tempo,
            "beat_times": list(beat_times),
            "energy": avg_energy
        }
