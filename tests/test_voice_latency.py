import asyncio
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from techfi_shared.logging import LatencyName, LatencySample, SafeFormatter
from techfi_voice.latency import LatencyObserver
from techfi_voice.prompt import conversational_prompt
from test_foundations import voice_settings


def capture(monkeypatch):
    stream = io.StringIO()
    logger = logging.Logger("latency-test")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeFormatter())
    logger.addHandler(handler)
    monkeypatch.setattr(
        "techfi_voice.latency.logging", SimpleNamespace(getLogger=lambda name: logger)
    )
    return stream


def test_monotonic_stages_last_transcript_deduplication_and_no_payloads(monkeypatch):
    stream = capture(monkeypatch)
    stt, llm, tts, output = object(), object(), object(), object()
    session_id = uuid4()
    observer = LatencyObserver(session_id, stt, llm, tts, output, lambda: 11_000_000_000)

    async def push(frame, source, seconds):
        await observer.on_push_frame(
            SimpleNamespace(
                frame=frame,
                source=source,
                timestamp=int(seconds * 1_000_000_000),
            )
        )

    async def run():
        await push(VADUserStartedSpeakingFrame(), stt, 9)
        stopped = VADUserStoppedSpeakingFrame(stop_secs=0.4, timestamp=-99999999)
        await push(stopped, stt, 10)
        await push(stopped, stt, 10)  # duplicate must not create another turn
        await push(
            TranscriptionFrame(
                "private-transcript", "customer-id", "", result={"key": "private-credential"}
            ),
            stt,
            10.2,
        )
        await push(TranscriptionFrame("private-transcript", "", ""), stt, 10.3)
        await push(LLMFullResponseStartFrame(), llm, 10.5)
        await push(LLMTextFrame("private-answer"), llm, 10.7)
        await push(LLMTextFrame("private-answer"), llm, 10.8)
        observer.tts_request("private-context")
        audio = TTSAudioRawFrame(b"private-audio", 24000, 1, context_id="private-context")
        await push(audio, tts, 11.4)
        await push(audio, tts, 11.5)
        await push(BotStartedSpeakingFrame(), output, 11.45)
        await push(BotStartedSpeakingFrame(), output, 11.5)
        await observer.cleanup()

    asyncio.run(run())
    records = [
        row
        for line in stream.getvalue().splitlines()
        if (row := json.loads(line))["event"] == "voice_latency"
    ]
    assert {row["metric"]: row["duration_ms"] for row in records} == {
        "stt_completion_ms": 700,
        "llm_first_token_ms": 200,
        "tts_first_audio_ms": 400,
        "speech_end_to_first_audio_ms": 1800,
        "speech_end_to_output_start_ms": 1850,
    }
    assert all(row["session_id"] == str(session_id) and row["turn_index"] == 1 for row in records)
    assert "private" not in stream.getvalue()
    assert "customer-id" not in stream.getvalue()


def test_interruption_and_next_session_do_not_inherit_old_timings(monkeypatch):
    stream = capture(monkeypatch)
    stt, llm, tts, output = object(), object(), object(), object()
    one = LatencyObserver(uuid4(), stt, llm, tts, output, lambda: 2_000_000_000)
    two = LatencyObserver(uuid4(), stt, llm, tts, output, lambda: 2_000_000_000)

    async def run():
        await one.on_push_frame(
            SimpleNamespace(
                frame=VADUserStoppedSpeakingFrame(), source=stt, timestamp=1_000_000_000
            )
        )
        one.tts_request("old-context")
        await one.on_push_frame(
            SimpleNamespace(frame=InterruptionFrame(), source=stt, timestamp=2_000_000_000)
        )
        for observer in (one, two):
            await observer.on_push_frame(
                SimpleNamespace(
                    frame=TTSAudioRawFrame(b"not-logged", 24000, 1, context_id="old-context"),
                    source=tts,
                    timestamp=3_000_000_000,
                )
            )
            await observer.on_push_frame(
                SimpleNamespace(
                    frame=BotStartedSpeakingFrame(), source=output, timestamp=3_000_000_000
                )
            )
            await observer.cleanup()
        assert one.turn_index == 1
        assert two.turn_index == 0

    asyncio.run(run())
    assert all(
        json.loads(line)["event"] != "voice_latency" for line in stream.getvalue().splitlines()
    )


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), "private"])
def test_formatter_rejects_invalid_samples_and_arbitrary_extras(duration):
    record = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        1,
        LatencySample(uuid4(), 1, LatencyName.STT_COMPLETION, duration),
        (),
        None,
    )
    record.transcript = "private-transcript"
    output = SafeFormatter().format(record)
    assert json.loads(output)["event"] == "suppressed"
    assert "private" not in output


@pytest.mark.parametrize("provider", ["openai", "groq"])
def test_original_prompt_is_passed_as_supported_system_instruction(monkeypatch, provider):
    from pipecat.services.groq.llm import GroqLLMService
    from techfi_voice.integrations.openai import ConfiguredOpenAILLMService
    from techfi_voice.integrations.providers import _llm

    cls = ConfiguredOpenAILLMService if provider == "openai" else GroqLLMService
    constructor = MagicMock()
    constructor.Settings = cls.Settings
    target = (
        "techfi_voice.integrations.openai.ConfiguredOpenAILLMService"
        if provider == "openai"
        else "pipecat.services.groq.llm.GroqLLMService"
    )
    monkeypatch.setattr(target, constructor)
    settings = voice_settings(llm_provider=provider)
    _llm(settings)
    assert constructor.call_args.kwargs["settings"].system_instruction == conversational_prompt(
        settings.system_prompt
    )


def test_system_instruction_produces_same_request_prompt_without_context_warning():
    import warnings

    from pipecat.adapters.services.open_ai_adapter import OpenAILLMAdapter
    from pipecat.processors.aggregators.llm_context import LLMContext

    prompt = conversational_prompt("Use short Hinglish sentences.")
    context = LLMContext([{"role": "user", "content": "synthetic input"}])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        request = OpenAILLMAdapter().get_llm_invocation_params(
            context,
            system_instruction=prompt,
            convert_developer_to_user=False,
        )
    assert request["messages"] == [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "synthetic input"},
    ]
    assert all("system" not in str(warning.message).lower() for warning in caught)
    assert context.messages == [{"role": "user", "content": "synthetic input"}]
