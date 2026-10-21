import unicodedata
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(
        "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text).split()
    )


def requests_opt_out(text: str, phrases: tuple[str, ...]) -> bool:
    words = f" {normalize(text)} "
    return any(f" {normalize(phrase)} " in words for phrase in phrases)


class OptOutProcessor(FrameProcessor):
    """Consume explicit opt-outs before user input reaches the LLM."""

    def __init__(self, phrases: tuple[str, ...], on_opt_out: Callable[[str], Awaitable[None]]):
        super().__init__()
        self._phrases = phrases
        self._on_opt_out = on_opt_out
        self._ended = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._ended:
                return
            if requests_opt_out(frame.text, self._phrases):
                self._ended = True
                await self.broadcast_interruption()
                await self._on_opt_out(frame.text)
                return
        await self.push_frame(frame, direction)


def requests_do_not_call(text: str) -> bool:
    # Ending this conversation alone is not a request to suppress future contact.
    return requests_opt_out(
        text, ("do not call", "don't call", "call mat karo", "कॉल मत करो", "opt out")
    )
