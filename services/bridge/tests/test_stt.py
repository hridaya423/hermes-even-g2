import os
import struct
import sys
from pathlib import Path

import pytest

from hermes_g2_bridge import stt
from hermes_g2_bridge.stt import SpeechError, transcribe, validate_pcm


def pcm(*, amplitude: int = 1_000, samples: int = 160) -> bytes:
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


def fake_binary(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o755)
    return path


def test_validate_pcm_rejects_empty_odd_and_oversized_payloads():
    with pytest.raises(SpeechError, match="non-empty"):
        validate_pcm(b"")
    with pytest.raises(SpeechError, match="non-empty"):
        validate_pcm(b"\x01")
    with pytest.raises(SpeechError, match="non-empty"):
        validate_pcm(b"\x01\x00" * ((stt.MAX_PCM_BYTES // 2) + 1))


def test_validate_pcm_accepts_even_signed_16_bit_audio_and_returns_seconds():
    value = validate_pcm(pcm(samples=16_000))
    assert value == pytest.approx(1.0)


def test_validate_pcm_rejects_silence():
    with pytest.raises(SpeechError, match="NO SPEECH"):
        validate_pcm(pcm(amplitude=0))


@pytest.mark.asyncio
async def test_transcribe_writes_wav_and_removes_intermediate_files(tmp_path, monkeypatch):
    wav_path = tmp_path / "capture.wav"

    def mkstemp(*, suffix, prefix):
        assert suffix == ".wav"
        descriptor = os.open(wav_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        return descriptor, str(wav_path)

    monkeypatch.setattr(stt.tempfile, "mkstemp", mkstemp)
    binary = fake_binary(
        tmp_path / "whisper-success",
        "from pathlib import Path\n"
        "import sys\n"
        "audio = Path(sys.argv[sys.argv.index('-f') + 1])\n"
        "assert audio.read_bytes()[:4] == b'RIFF'\n"
        "Path(str(audio) + '.txt').write_text('  hello from whisper  ')\n",
    )
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")

    result = await transcribe(pcm(), binary, model)

    assert result == {"transcript": "hello from whisper", "duration": pytest.approx(0.01), "confidence": None}
    assert not wav_path.exists()
    assert not Path(f"{wav_path}.txt").exists()


@pytest.mark.asyncio
async def test_transcribe_surfaces_nonzero_exit_and_still_cleans_up(tmp_path, monkeypatch):
    wav_path = tmp_path / "failed.wav"

    def mkstemp(*, suffix, prefix):
        descriptor = os.open(wav_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        return descriptor, str(wav_path)

    monkeypatch.setattr(stt.tempfile, "mkstemp", mkstemp)
    binary = fake_binary(
        tmp_path / "whisper-failure",
        "import sys\n"
        "sys.stderr.write('decoder failed\\n')\n"
        "raise SystemExit(7)\n",
    )
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")

    with pytest.raises(SpeechError, match="transcription failed:.*decoder failed"):
        await transcribe(pcm(), binary, model)
    assert not wav_path.exists()


@pytest.mark.asyncio
async def test_transcribe_kills_hung_subprocess_and_cleans_up(tmp_path, monkeypatch):
    wav_path = tmp_path / "timeout.wav"

    def mkstemp(*, suffix, prefix):
        descriptor = os.open(wav_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        return descriptor, str(wav_path)

    monkeypatch.setattr(stt.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(stt, "STT_TIMEOUT_SECONDS", 0.05)
    binary = fake_binary(
        tmp_path / "whisper-hang",
        "import time\n"
        "time.sleep(10)\n",
    )
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")

    with pytest.raises(SpeechError, match="timed out"):
        await transcribe(pcm(), binary, model)
    assert not wav_path.exists()


@pytest.mark.asyncio
async def test_optional_real_model_smoke_is_opt_in(tmp_path):
    binary = Path(os.environ.get("HERMES_G2_STT_BINARY", ""))
    model = Path(os.environ.get("HERMES_G2_STT_MODEL", ""))
    if os.environ.get("HERMES_G2_STT_SMOKE") != "1" or not binary.is_file() or not model.is_file():
        pytest.skip("set HERMES_G2_STT_SMOKE=1, HERMES_G2_STT_BINARY and HERMES_G2_STT_MODEL")
    result = await transcribe(pcm(samples=16_000), binary, model)
    assert result["transcript"]
