# Preserved POC

All six original scripts and `requirements.txt` were relocated without content changes. Run from this directory so existing relative imports and audio paths continue to resolve, using a separate virtual environment. Supply `GROQ_API_KEY` through the environment or a local ignored `.env`.

Historical entry points: `python app.py`, `python semi_realtime_agent.py`, `python realtime_free_agent.py`. `agent.py` also exposes lead summary extraction. The original requirements omit `faster-whisper` and `python-dotenv` and rely on transitive NumPy; reproducing these scripts may require installing those separately. Playback may require ffmpeg and PortAudio. These historical dependencies are not added to the new workspace lock.

Known POC behavior is preserved: global memory, fixed filenames, console transcripts, synchronous provider calls and unpinned dependencies. This directory is excluded from Ruff and the production runtime. Do not use it for customer or banking data. No legacy audio/provider execution was performed during migration.
