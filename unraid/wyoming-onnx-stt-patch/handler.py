import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from onnx_asr.adapters import SegmentResultsAsrAdapter, TextResultsAsrAdapter
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler

_LOGGER = logging.getLogger(__name__)
_INT16_MAX = 32768.0

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes per sample (int16)
CHANNELS = 1

class OnnxModel:
    def __init__(
        self,
        model_name: str,
        device: str,
        quantization: Literal["int8", "fp16"] | None,
        *,
        pnc: bool,
        tensorrt: bool,
        trt_workspace_gb: int,
        trt_fp16: bool,
        vad_enabled: bool,
        vad_threshold: float,
        vad_min_speech_ms: float,
        vad_min_silence_ms: float,
        vad_speech_pad_ms: float,
    ) -> None:
        import onnx_asr

        self.pnc = pnc

        providers: list[str | tuple[str, dict[str, int | bool | str]]] = []
        if device == "cuda":
            if tensorrt:
                providers.append((
                    "TensorrtExecutionProvider",
                    {
                        "trt_max_workspace_size": trt_workspace_gb * 1024**3,
                        "trt_fp16_enable": trt_fp16,
                    },
                ))
                _LOGGER.info(
                    "TensorRT enabled: workspace=%dGB, fp16=%s",
                    trt_workspace_gb,
                    trt_fp16,
                )
            # Patched: keep ONNX Runtime's CUDA memory arena from over-allocating.
            # kSameAsRequested stops power-of-two arena growth; HEURISTIC conv search
            # avoids large cuDNN benchmark workspaces.
            cuda_options: dict[str, int | bool | str] = {
                "arena_extend_strategy": "kSameAsRequested",
                "cudnn_conv_algo_search": "HEURISTIC",
                "cudnn_conv_use_max_workspace": "0",
            }
            mem_limit_mb = int(os.environ.get("STT_CUDA_MEM_LIMIT_MB", "0") or "0")
            if mem_limit_mb > 0:
                cuda_options["gpu_mem_limit"] = mem_limit_mb * 1024**2
            _LOGGER.info("CUDA provider options: %s", cuda_options)
            providers.append(("CUDAExecutionProvider", cuda_options))
        providers.append("CPUExecutionProvider")

        _LOGGER.info(
            "Loading model: %s (device=%s, quantization=%s)",
            model_name,
            device,
            quantization,
        )

        model: TextResultsAsrAdapter = onnx_asr.load_model(
            model_name,
            quantization=quantization,
            providers=providers,
        )

        self._model_with_vad: SegmentResultsAsrAdapter | None = None
        self._model_text: TextResultsAsrAdapter | None = None

        if vad_enabled:
            _LOGGER.info(
                "Loading Silero VAD: threshold=%.2f, min_speech=%dms, min_silence=%dms, pad=%dms",
                vad_threshold,
                int(vad_min_speech_ms),
                int(vad_min_silence_ms),
                int(vad_speech_pad_ms),
            )
            vad = onnx_asr.load_vad("silero", providers=providers)
            self._model_with_vad = model.with_vad(
                vad,
                threshold=vad_threshold,
                min_speech_duration_ms=vad_min_speech_ms,
                min_silence_duration_ms=vad_min_silence_ms,
                speech_pad_ms=vad_speech_pad_ms,
            )
        else:
            self._model_text = model

    def transcribe(self, audio: np.ndarray, language: str) -> str:
        if self._model_with_vad is not None:
            segments = self._model_with_vad.recognize(audio, language=language, pnc=self.pnc)
            return " ".join(seg.text for seg in segments)
        assert self._model_text is not None
        return self._model_text.recognize(audio, language=language, pnc=self.pnc)


class OnnxEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        default_language: str,
        model: OnnxModel,
        model_lock: asyncio.Lock,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        super().__init__(reader, writer)

        self.wyoming_info_event = wyoming_info.event()
        self.model = model
        self.model_lock = model_lock
        self._default_language = default_language
        self._language: str | None = None
        self._audio_buffer: bytes = b""
        self._audio_converter = AudioChunkConverter(
            rate=SAMPLE_RATE, width=SAMPLE_WIDTH, channels=CHANNELS
        )

    async def handle_event(self, event: Event) -> bool:
        if AudioChunk.is_type(event.type):
            chunk = AudioChunk.from_event(event)
            if chunk.rate != SAMPLE_RATE or chunk.width != SAMPLE_WIDTH or chunk.channels != CHANNELS:
                chunk = self._audio_converter.convert(chunk)
            self._audio_buffer += chunk.audio
            return True

        if AudioStop.is_type(event.type):
            await self._handle_transcribe()
            return False

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            if transcribe.language:
                self._language = transcribe.language
                _LOGGER.debug("Language set: %s", transcribe.language)
            return True

        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent service info")
            return True

        _LOGGER.warning("Unhandled event type: %s", event.type)
        return True

    async def _handle_transcribe(self) -> None:
        start_time = time.perf_counter()
        language = self._language or self._default_language

        audio_bytes = len(self._audio_buffer)
        if audio_bytes == 0:
            _LOGGER.warning("Empty audio buffer, skipping transcription")
            await self.write_event(Transcript(text="").event())
            return

        audio_duration = audio_bytes / (SAMPLE_WIDTH * SAMPLE_RATE)

        audio_array = (
            np.frombuffer(self._audio_buffer, dtype=np.int16).astype(np.float32)
            / _INT16_MAX
        )
        self._audio_buffer = b""
        self._language = None

        _LOGGER.debug("Transcribing: %.2fs audio, lang=%s", audio_duration, language)

        try:
            async with self.model_lock:
                text = self.model.transcribe(audio_array, language=language)

            await self.write_event(Transcript(text=text).event())

            elapsed = time.perf_counter() - start_time
            rtf = elapsed / audio_duration if audio_duration > 0 else 0
            _LOGGER.info(
                "Transcription complete: lang=%s, audio=%.2fs, elapsed=%.2fs, rtf=%.2f",
                language,
                audio_duration,
                elapsed,
                rtf,
            )
        except Exception as err:
            _LOGGER.exception("Transcription failed")
            await self.write_event(Error(text=str(err), code=err.__class__.__name__).event())
            await self.write_event(Transcript(text="").event())
