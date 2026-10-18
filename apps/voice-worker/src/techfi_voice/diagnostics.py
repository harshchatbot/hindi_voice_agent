"""Startup-only diagnostics: no provider payloads, settings values, locals or source lines."""

import sys
import traceback
from typing import TextIO

from pydantic import ValidationError
from techfi_shared.errors import ApplicationError

from techfi_voice.config import VoiceSettings


def development_traceback(exc: Exception, stream: TextIO | None = None) -> None:
    stream = stream if stream is not None else sys.stderr
    stream.write("Startup traceback (configuration values and frame locals omitted):\n")
    for frame, lineno in traceback.walk_tb(exc.__traceback__):
        stream.write(
            f'  File "{frame.f_code.co_filename}", line {lineno}, in {frame.f_code.co_name}\n'
        )
    stream.write(f"{type(exc).__name__}\n")
    if isinstance(exc, ValidationError):
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            field = error["loc"][0] if error["loc"] else None
            if field in VoiceSettings.model_fields:
                description = (
                    "required setting missing" if error["type"] == "missing" else "invalid setting"
                )
                stream.write(f"  TECHFI_{field.upper()}: {description} ({error['type']})\n")
    elif isinstance(exc, ApplicationError):
        stream.write(f"  {exc.code}; inspect the indicated configuration/integration boundary.\n")
    else:
        stream.write(
            "  Exception message omitted because it may contain credentials or payloads.\n"
        )
