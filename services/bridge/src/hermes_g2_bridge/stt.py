import asyncio
import os
import tempfile
import wave
from pathlib import Path


class SpeechError(ValueError):
    pass


MAX_PCM_BYTES = 4 * 1024 * 1024
MAX_DURATION_SECONDS = 45
STT_TIMEOUT_SECONDS = 30


def validate_pcm(pcm: bytes) -> float:
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
        raise SpeechError("audio must be non-empty signed 16-bit PCM under 4 MB")
    duration = len(pcm) / (16_000 * 2)
    if duration > MAX_DURATION_SECONDS:
        raise SpeechError("audio exceeds 45 seconds")
    samples = memoryview(pcm).cast("h")
    # Iterate over the memory view instead of unpacking a tuple containing every
    # sample. A normal G2 utterance is small, but this keeps validation bounded at
    # the 4 MB ingress limit and avoids a second large allocation.
    energy = sum(sample * sample for sample in samples)
    rms = (energy / len(samples)) ** 0.5
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
        try:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "-m",
                str(model),
                "-f",
                wav_path,
                "--no-timestamps",
                "--output-txt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise SpeechError("local STT failed to start") from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=STT_TIMEOUT_SECONDS
            )
        except TimeoutError as error:
            # wait_for cancels communicate(), but it does not terminate the child.
            # Kill it before returning so a hung whisper process cannot accumulate
            # across repeated recordings or keep a microphone upload alive.
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.communicate(), timeout=2)
            except (TimeoutError, ProcessLookupError):
                pass
            raise SpeechError("transcription timed out") from error
        if process.returncode:
            raise SpeechError(f"transcription failed: {stderr.decode(errors='replace')[-200:]}")
        output_file = Path(f"{wav_path}.txt")
        text = output_file.read_text(errors="replace").strip() if output_file.exists() else stdout.decode(errors="replace").strip()
        if not text:
            raise SpeechError("NO SPEECH")
        return {"transcript": text[:12_000], "duration": duration, "confidence": None}
    finally:
        for suffix in ("", ".txt"):
            Path(f"{wav_path}{suffix}").unlink(missing_ok=True)
