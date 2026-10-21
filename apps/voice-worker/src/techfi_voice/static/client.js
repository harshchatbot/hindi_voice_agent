"use strict";
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
const llmSelect = document.querySelector("#llm-provider");
const providerSelect = document.querySelector("#voice-provider");
const agentSelect = document.querySelector("#agent-type");
const personaSelect = document.querySelector("#persona");
const collectionFields = document.querySelector("#collection-fields");
const resultView = document.querySelector("#collection-result");
let activeAgentType = "general";
const controls = [providerSelect, llmSelect, agentSelect, personaSelect, collectionFields];
function lockConfiguration(locked) { controls.forEach(control => { control.disabled = locked; }); }
function updateOptions() {
  collectionFields.hidden = agentSelect.value !== "soft-collection";
  const female = personaSelect.value === "female";
  const labels = female ? ["Sarvam Bulbul V3 — Ritu", "Sarvam Bulbul V4 Flash — Ritu Hindi (Experimental)", "Google Chirp 3 HD — Kore"] : ["Sarvam Bulbul V3 — Shubh", "Sarvam Bulbul V4 Flash — Shubh Hindi (Experimental)", "Google Chirp 3 HD — Achird"];
  if (providerSelect.options) Array.from(providerSelect.options).forEach((option, index) => { option.textContent = labels[index]; });
}
agentSelect.addEventListener("change", updateOptions);
personaSelect.addEventListener("change", updateOptions);
updateOptions();

function demoConfiguration() {
  const ids = ["bank-name", "customer-name", "emi-amount", "due-date", "loan-type", "payment-status", "payment-methods"];
  const fields = ids.map(id => document.querySelector(`#${id}`));
  if (fields.some(input => input.reportValidity && !input.reportValidity())) throw new Error("invalid_demo");
  return {fictional: true, bank_name: fields[0].value, customer_name: fields[1].value,
    emi_amount: fields[2].value, due_date: fields[3].value, loan_type: fields[4].value,
    payment_status: fields[5].value, payment_methods: Array.from(fields[6].selectedOptions).map(option => option.value)};
}

const status = document.querySelector("#status");
const statusBadge = document.querySelector("#status-badge");
const statusLabel = document.querySelector("#status-label");
const stageTitle = document.querySelector("#stage-title");
const voiceOrb = document.querySelector("#voice-orb");
const activityHelp = document.querySelector("#activity-help");
const playbackHelp = document.querySelector("#playback-help");
const speaker = document.querySelector("#speaker");
let peer = null, stream = null, pcId = null, heartbeat = null;
let controller = null, generation = 0, connectTimer = null;
let audioContext = null, animationFrame = null;
const meterNodes = [];
const meters = new Map();
const headers = {"Content-Type": "application/json", "X-TechFi-Client": "local-browser"};

function setStatus(state, message) {
  statusBadge.dataset.state = state;
  statusLabel.textContent = state[0].toUpperCase() + state.slice(1);
  status.textContent = message;
  stageTitle.textContent = {
    ready: "Your next conversation starts here", connecting: "Getting your conversation ready",
    connected: "You’re connected. Say namaste.", error: "Let’s try that again",
    disconnected: "Conversation ended"
  }[state];
  voiceOrb.dataset.active = String(state === "connected");
}

function resetMeters() {
  cancelAnimationFrame(animationFrame); animationFrame = null;
  for (const node of meterNodes.splice(0)) { try { node.disconnect(); } catch { /* Already closed. */ } }
  meters.clear();
  const context = audioContext; audioContext = null;
  if (context) void context.close().catch(() => {});
  document.querySelectorAll(".meter i").forEach(bar => { bar.dataset.lit = "false"; });
  activityHelp.textContent = "Audio activity appears here when connected.";
}

function prepareMeters() {
  const Context = window.AudioContext || window.webkitAudioContext;
  try {
    if (!Context) throw new Error("visualizer_unavailable");
    // Created in the click gesture. This graph never plays microphone audio.
    audioContext = new Context();
    void audioContext.resume().catch(() => {
      activityHelp.textContent = "Activity meters unavailable. Conversation audio is unaffected.";
    });
    const draw = () => {
      if (!audioContext) return;
      for (const {analyser, samples, bars} of meters.values()) {
        analyser.getByteTimeDomainData(samples);
        let power = 0;
        for (const sample of samples) power += ((sample - 128) / 128) ** 2;
        const level = Math.min(1, Math.sqrt(power / samples.length) * 6);
        bars.forEach((bar, index) => { bar.dataset.lit = String(level > (index + 0.5) / bars.length); });
      }
      animationFrame = requestAnimationFrame(draw);
    };
    animationFrame = requestAnimationFrame(draw);
  } catch {
    resetMeters();
    activityHelp.textContent = "Activity meters unavailable. Conversation audio is unaffected.";
  }
}

function attachMeter(mediaStream, elementId) {
  if (!audioContext) return;
  try {
    const source = audioContext.createMediaStreamSource(mediaStream);
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    const silent = audioContext.createGain();
    silent.gain.value = 0;
    source.connect(analyser); analyser.connect(silent); silent.connect(audioContext.destination);
    meterNodes.push(source, analyser, silent);
    meters.set(elementId, {
      analyser, samples: new Uint8Array(analyser.fftSize),
      bars: [...document.querySelectorAll(`#${elementId} i`)]
    });
    activityHelp.textContent = "Local activity only. Audio is not recorded by the playground.";
  } catch {
    activityHelp.textContent = "Some activity meters are unavailable. Conversation audio is unaffected.";
  }
}

async function stop(message = "Conversation ended. You can start a new session.", state = "disconnected") {
  generation += 1;
  const id = pcId;
  const wasCollection = activeAgentType === "soft-collection";
  activeAgentType = "general";
  const endedGeneration = generation;
  pcId = null;
  controller?.abort(); controller = null;
  clearTimeout(connectTimer); clearInterval(heartbeat);
  const oldPeer = peer; peer = null;
  oldPeer?.close();
  stream?.getTracks().forEach(track => track.stop()); stream = null;
  speaker.srcObject = null;
  resetMeters();
  playbackHelp.textContent = "Use headphones. If playback is blocked, press Play above.";
  setStatus(state, message);
  startButton.disabled = false; stopButton.disabled = true; lockConfiguration(false);
  // Finish local cleanup before this bounded request. A new start cannot be unlocked by an old reply.
  if (id) {
    try {
      await fetch(`/api/sessions/${encodeURIComponent(id)}/stop`, {
        method: "POST", headers, keepalive: true, signal: AbortSignal.timeout(3000)
      });
    } catch { /* Server also handles peer disconnect and maximum session duration. */ }
  }
  if (id && wasCollection) {
    resultView.textContent = "Waiting for the local structured outcome…";
    const resultDeadline = Date.now() + 15000;
    for (let attempt = 0; attempt < 30 && Date.now() < resultDeadline && generation === endedGeneration; attempt += 1) {
      try {
        const response = await fetch(`/api/sessions/${encodeURIComponent(id)}/result`, {method: "POST", headers, signal: AbortSignal.timeout(2000)});
        if (generation !== endedGeneration) return;
        if (response.status === 200) {
          const data = await response.json();
          if (generation === endedGeneration) resultView.textContent = JSON.stringify(data, null, 2);
          return;
        }
      } catch { /* No error bodies or user data are logged. */ }
      await new Promise(resolve => setTimeout(resolve, 400));
    }
    if (generation === endedGeneration) resultView.textContent = "Outcome not yet available; server cleanup may still be running. Local results are temporary, not a durable bank record.";
  }
}

function waitForIce(connection, signal) {
  if (connection.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const finish = error => {
      clearTimeout(timer);
      connection.removeEventListener("icegatheringstatechange", changed);
      signal.removeEventListener("abort", aborted);
      if (error) reject(error); else resolve();
    };
    const changed = () => { if (connection.iceGatheringState === "complete") finish(); };
    const aborted = () => finish(new Error("cancelled"));
    const timer = setTimeout(() => finish(new Error("ice_timeout")), 10000);
    connection.addEventListener("icegatheringstatechange", changed);
    signal.addEventListener("abort", aborted, {once: true});
    if (signal.aborted) aborted(); else changed();
  });
}

startButton.addEventListener("click", async () => {
  if (startButton.disabled) return;
  const voiceProvider = providerSelect.value;
  const llmProvider = llmSelect.value;
  const agentType = agentSelect.value;
  const persona = personaSelect.value;
  let demo = null;
  try { if (agentType === "soft-collection") demo = demoConfiguration(); }
  catch { setStatus("error", "Use valid fictional demo fields; do not enter real customer data."); return; }
  activeAgentType = agentType;
  resultView.textContent = "No outcome yet. This session has its own fictional report.";
  startButton.disabled = true; stopButton.disabled = false; lockConfiguration(true);
  const current = ++generation;
  setStatus("connecting", "Requesting microphone access…");
  controller = new AbortController();
  const signal = controller.signal;
  prepareMeters();
  try {
    const microphone = await navigator.mediaDevices.getUserMedia({
      audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false
    });
    if (current !== generation) { microphone.getTracks().forEach(t => t.stop()); return; }
    stream = microphone;
    attachMeter(stream, "mic-meter");
    const connection = new RTCPeerConnection({iceServers: []});
    peer = connection;
    connection.addTransceiver(stream.getAudioTracks()[0], {direction: "sendrecv"});
    connection.ontrack = event => {
      if (current !== generation) return;
      const remoteStream = event.streams[0] || new MediaStream([event.track]);
      speaker.srcObject = remoteStream;
      attachMeter(remoteStream, "speaker-meter");
      speaker.play().catch(() => {
        if (current === generation) playbackHelp.textContent = "Playback is blocked. Press Play above to hear the assistant.";
      });
    };
    const channel = connection.createDataChannel("chat");
    channel.onopen = () => {
      if (current !== generation) return;
      heartbeat = setInterval(() => { if (channel.readyState === "open") channel.send("ping"); }, 1000);
      channel.send("ping");
    };
    channel.onmessage = event => {
      if (current !== generation) return;
      // Lifecycle signals only. Never display arbitrary provider content or transcripts.
      try {
        const data = JSON.parse(event.data);
        if (data.type === "signalling" && data.message?.type === "peerLeft") void stop();
      } catch { /* No transcripts or payloads are logged. */ }
    };
    connection.onconnectionstatechange = () => {
      if (current !== generation) return;
      if (connection.connectionState === "connected") {
        clearTimeout(connectTimer);
        setStatus("connected", "Speak Hindi or Hinglish. Say ‘band karo’ or press End Conversation to stop.");
      }
      if (connection.connectionState === "failed") {
        void stop("Connection failed. Check your local worker and try again.", "error");
      } else if (["closed", "disconnected"].includes(connection.connectionState)) {
        void stop("Connection ended. You can start a new conversation.");
      }
    };
    await connection.setLocalDescription(await connection.createOffer());
    // Non-trickle ICE: complete SDP is exchanged once.
    await waitForIce(connection, signal);
    if (current !== generation) return;
    setStatus("connecting", "Connecting to your selected voice…");
    const response = await fetch("/api/offer", {
      method: "POST", headers,
      body: JSON.stringify({sdp: connection.localDescription.sdp, type: connection.localDescription.type, voice_provider: voiceProvider, llm_provider: llmProvider, persona, agent_type: agentType, ...(demo ? {collection: demo} : {})}),
      signal: AbortSignal.any([signal, AbortSignal.timeout(60000)])
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error === "collection_demo_not_authorized" ? "collection_policy" : "session_unavailable");
    }
    const answer = await response.json();
    if (current !== generation) {
      await fetch(`/api/sessions/${encodeURIComponent(answer.pc_id)}/stop`, {
        method: "POST", headers, keepalive: true, signal: AbortSignal.timeout(3000)
      });
      return;
    }
    pcId = answer.pc_id;
    await connection.setRemoteDescription({sdp: answer.sdp, type: answer.type});
    if (current !== generation) return;
    if (connection.connectionState !== "connected") connectTimer = setTimeout(() => {
      if (current === generation) void stop("Connection timed out. Please try again.", "error");
    }, 30000);
  } catch (error) {
    if (current === generation) await stop(error.message === "collection_policy"
      ? "Collection demo blocked: check server enablement, India calling hours, retry limits or prior opt-out."
      : "Unable to connect. Check microphone permission and the local worker configuration.", "error");
  }
});
stopButton.addEventListener("click", () => stop("You ended this conversation. Your microphone is off."));
window.addEventListener("pagehide", () => { void stop(); });
