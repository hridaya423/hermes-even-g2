import asyncio
import os
import struct
import tempfile
import wave
from pathlib import Path


class SpeechError(ValueError):
    pass


def validate_pcm(pcm: bytes) -> float:
    if not pcm or len(pcm) % 2 or len(pcm) > 4 * 1024 * 1024:
        raise SpeechError("audio must be non-empty signed 16-bit PCM under 4 MB")
    duration = len(pcm) / (16_000 * 2)
    if duration > 45:
        raise SpeechError("audio exceeds 45 seconds")
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
    if rms < 80:
        raise SpeechError("NO SPEECH")
    return duration


async def transcribe(pcm: bytes, binary: Path, model: Path) -> dict:
    duration = validate_pcm(pcm)
    if not binary.exists() or not model.exists():
        raise SpeechError("local STT is not ready")
    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="hermes-g2-")
    os.close(fd)
    try:
        with wave.open(wav_path, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16_000)
            target.writeframes(pcm)
        process = await asyncio.create_subprocess_exec(str(binary), "-m", str(model), "-f", wav_path, "--no-timestamps", "--output-txt", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode:
            raise SpeechError(f"transcription failed: {stderr.decode(errors='replace')[-200:]}")
        output_file = Path(f"{wav_path}.txt")
        text = output_file.read_text().strip() if output_file.exists() else stdout.decode().strip()
        if not text:
            raise SpeechError("NO SPEECH")
        return {"transcript": text, "duration": duration, "confidence": None}
    finally:
        for suffix in ("", ".txt"):
            Path(f"{wav_path}{suffix}").unlink(missing_ok=True)

