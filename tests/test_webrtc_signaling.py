"""Optional socket-level check: synthetic SDP/data channel only; no microphone or provider calls."""

import asyncio

import pytest
from loguru import logger


@pytest.mark.integration
def test_smallwebrtc_negotiates_with_a_synthetic_audio_transceiver():
    logger.remove()
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    async def run():
        client = RTCPeerConnection()
        server = SmallWebRTCConnection(ice_servers=[])
        client.addTransceiver("audio", direction="sendrecv")
        channel = client.createDataChannel("chat")
        opened = asyncio.Event()

        @channel.on("open")
        def on_open():
            channel.send("ping")
            opened.set()

        try:
            async with asyncio.timeout(10):
                await client.setLocalDescription(await client.createOffer())
                await server.initialize(sdp=client.localDescription.sdp, type="offer")
                answer = server.get_answer()
                assert answer["type"] == "answer"
                await client.setRemoteDescription(
                    RTCSessionDescription(
                        sdp=answer["sdp"],
                        type=answer["type"],
                    )
                )
                await server.connect()
                await opened.wait()
                assert client.connectionState == "connected"
        finally:
            await client.close()
            await server.disconnect()

    asyncio.run(run())
