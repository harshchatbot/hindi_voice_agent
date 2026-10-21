import asyncio
import logging
from contextlib import suppress

from techfi_shared.contracts import CallOutcome, CallResult
from techfi_shared.logging import Event

from techfi_voice.config import VoiceSettings
from techfi_voice.session import VoiceSession


async def run_session(session: VoiceSession, settings: VoiceSettings, runner=None) -> CallResult:
    """Own every task and bound teardown even when a provider stops responding."""
    if runner is None:
        from pipecat.workers.runner import WorkerRunner

        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    logger = logging.getLogger("techfi")
    tasks: list[asyncio.Task] = []
    run_task = None
    try:
        await runner.add_workers(session.task)
        run_task = asyncio.create_task(runner.run())
        stop_task = asyncio.create_task(session.stop.wait())
        opt_out_task = asyncio.create_task(session.opt_out_requested.wait())
        tasks.extend([run_task, stop_task, opt_out_task])
        logger.info(Event.WORKER_STARTED, extra={"session_id": session.scope.session_id})
        duration = settings.max_session_seconds
        if session.calling_window_remaining:
            # Re-evaluate after connection/setup: an earlier admission deadline can be stale.
            duration = min(duration, session.calling_window_remaining())
        deadline = asyncio.get_running_loop().time() + duration
        opt_out_deadline_set = False
        while True:
            if session.calling_window_remaining:
                deadline = min(
                    deadline, asyncio.get_running_loop().time() + session.calling_window_remaining()
                )
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            watched = [run_task, stop_task]
            if not opt_out_deadline_set:
                watched.append(opt_out_task)
            done, _ = await asyncio.wait(
                watched,
                timeout=min(remaining, 1) if session.calling_window_remaining else remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                if (
                    session.calling_window_remaining
                    and asyncio.get_running_loop().time() < deadline
                ):
                    continue
                if not (session.opted_out or session.closing):
                    session.outcome = CallOutcome.TIMED_OUT
                break
            if run_task in done:
                await run_task
                break
            if stop_task in done:
                break
            if opt_out_task in done:
                opt_out_deadline_set = True
                deadline = min(
                    deadline, asyncio.get_running_loop().time() + settings.opt_out_grace_seconds
                )
        if not run_task.done():
            from pipecat.frames.frames import EndFrame

            async with asyncio.timeout(settings.cleanup_timeout_seconds):
                if session.failed or session.outcome == CallOutcome.TIMED_OUT:
                    await session.task.cancel()
                else:
                    await session.task.queue_frame(EndFrame())
                await asyncio.shield(run_task)
    except asyncio.CancelledError:
        session.outcome = CallOutcome.CANCELLED
        raise
    except Exception:
        session.failed = True
        session.outcome = CallOutcome.FAILED
    finally:
        # Cancellation can occur during startup or teardown; always stop owned tasks.
        if run_task and not run_task.done():
            with suppress(Exception):
                async with asyncio.timeout(settings.cleanup_timeout_seconds):
                    await session.task.cancel()
            run_task.cancel()
        for task in tasks:
            if not task.done():
                task.cancel()
        # Pipecat's runner may absorb cancellation while tearing down, so bound this too.
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=settings.cleanup_timeout_seconds)
            for task in done:
                with suppress(asyncio.CancelledError, Exception):
                    task.result()
            for task in pending:
                task.cancel()
        if session.monitor:
            await session.monitor.cleanup()
        session.context.set_messages([])
        if session.collection:
            session.collection.cancel_pending()
        logger.info(
            Event.WORKER_FAILED if session.failed else Event.WORKER_COMPLETED,
            extra={"session_id": session.scope.session_id},
        )
    return session.result()
