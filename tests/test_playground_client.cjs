// Offline browser-state tests: fake DOM/media/WebRTC/fetch; no sockets or microphone.
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const vm = require("node:vm");
const source = readFileSync("apps/voice-worker/src/techfi_voice/static/client.js", "utf8");

function harness(getMedia) {
  const elements = new Map();
  const bars = new Map(["mic-meter", "speaker-meter"].map(id => [id,
    Array.from({length: 10}, () => ({dataset: {}}))]));
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      disabled: id === "stop", value: id === "voice-provider" ? "sarvam" : id === "llm-provider" ? "openai" : id === "agent-type" ? "general" : id === "persona" ? "male" : "",
      dataset: {}, textContent: "", listeners: {}, srcObject: null,
      addEventListener(name, callback) { this.listeners[name] = callback; },
      play: async () => {}
    });
    return elements.get(id);
  };
  const track = {stops: 0, stop() { this.stops += 1; }};
  const media = {getTracks: () => [track], getAudioTracks: () => [track]};
  const requests = [], peers = [], contexts = [], nodes = [];
  let mediaRequests = 0;
  class Peer {
    constructor() { this.iceGatheringState = "complete"; this.connectionState = "new"; peers.push(this); }
    addTransceiver() {}
    createDataChannel() { return {}; }
    async createOffer() { return {sdp: "synthetic-sdp", type: "offer"}; }
    async setLocalDescription(offer) { this.localDescription = offer; }
    async setRemoteDescription() {
      this.ontrack?.({streams: [media]});
      this.connectionState = "connected";
      this.onconnectionstatechange?.();
    }
    close() { this.connectionState = "closed"; this.onconnectionstatechange?.(); }
  }
  function node() {
    const value = {connect() {}, disconnects: 0, disconnect() { this.disconnects += 1; }, gain: {value: 1}};
    nodes.push(value); return value;
  }
  class AudioContext {
    constructor() { this.destination = {}; this.closes = 0; contexts.push(this); }
    async resume() {}
    async close() { this.closes += 1; }
    createMediaStreamSource() { return node(); }
    createGain() { return node(); }
    createAnalyser() { return Object.assign(node(), {getByteTimeDomainData(samples) { samples.fill(128); }}); }
  }
  const window = {AudioContext, addEventListener() {}};
  const sandbox = {
    document: {
      querySelector: selector => element(selector.slice(1)),
      querySelectorAll: selector => selector === ".meter i" ? [...bars.values()].flat() : bars.get(selector.split(" ")[0].slice(1))
    },
    window,
    navigator: {mediaDevices: {getUserMedia: () => { mediaRequests += 1; return getMedia ? getMedia(media) : Promise.resolve(media); }}},
    RTCPeerConnection: Peer, MediaStream: class {},
    AbortController, AbortSignal, setTimeout, clearTimeout, setInterval, clearInterval,
    requestAnimationFrame: () => 1, cancelAnimationFrame() {}, Uint8Array,
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url.endsWith("/result")) return {ok: true, status: 200, json: async () => ({collection: {demo_only: true, call_outcome: "incomplete"}})};
      return {ok: true, status: 200, json: async () => ({sdp: "synthetic-answer", type: "answer", pc_id: "opaque-peer"})};
    }
  };
  vm.runInNewContext(source, sandbox);
  return {element, requests, track, peers, contexts, nodes, media, mediaRequests: () => mediaRequests};
}

for (const provider of ["sarvam", "google", "sarvam-v4-flash"]) {
 for (const llm of ["openai", "sarvam"]) {
  test(`${provider}/${llm}: choice sent once, locked while active, media and meters cleaned`, async () => {
    const h = harness();
    assert.equal(h.mediaRequests(), 0);
    h.element("voice-provider").value = provider;
    h.element("llm-provider").value = llm;
    const starting = h.element("start").listeners.click();
    assert.equal(h.element("voice-provider").disabled, true);
    assert.equal(h.element("llm-provider").disabled, true);
    assert.equal(h.element("status-label").textContent, "Connecting");
    await starting;
    const offer = JSON.parse(h.requests[0].options.body);
    assert.deepEqual(offer, {sdp: "synthetic-sdp", type: "offer", voice_provider: provider, llm_provider: llm, persona: "male", agent_type: "general"});
    assert.equal(h.element("status-label").textContent, "Connected");
    assert.equal(h.element("voice-provider").disabled, true);
    assert.equal(h.element("llm-provider").disabled, true);
    assert.equal(h.element("start").disabled, true);
    await h.element("start").listeners.click();
    assert.equal(h.requests.length, 1);
    h.element("stop").listeners.click();
    assert.equal(h.element("status-label").textContent, "Disconnected");
    assert.equal(h.element("voice-provider").disabled, false);
    assert.equal(h.element("llm-provider").disabled, false);
    assert.equal(h.track.stops, 1);
    assert.equal(h.contexts[0].closes, 1);
    assert.ok(h.nodes.every(n => n.disconnects === 1));
    assert.equal(h.element("speaker").srcObject, null);
    assert.equal(h.requests[1].url, "/api/sessions/opaque-peer/stop");
  });
 }
}

test("permission failure shows Error and unlocks controls without an offer", async () => {
  const h = harness(() => Promise.reject(new Error("synthetic-permission-denied")));
  await h.element("start").listeners.click();
  assert.equal(h.element("status-label").textContent, "Error");
  assert.equal(h.element("voice-provider").disabled, false);
  assert.equal(h.requests.length, 0);
  assert.equal(h.contexts[0].closes, 1);
  assert.ok(!h.element("status").textContent.includes("synthetic-permission-denied"));
});

test("ending during permission prompt stops late media and never establishes a peer", async () => {
  let resolveMedia;
  const h = harness(() => new Promise(resolve => { resolveMedia = resolve; }));
  const starting = h.element("start").listeners.click();
  h.element("stop").listeners.click();
  resolveMedia(h.media);
  await starting;
  assert.equal(h.track.stops, 1);
  assert.equal(h.peers.length, 0);
  assert.equal(h.requests.length, 0);
  assert.equal(h.element("status-label").textContent, "Disconnected");
});

test("collection snapshot locks every setting and displays only the local structured outcome", async () => {
  const h = harness();
  h.element("agent-type").value = "soft-collection";
  h.element("agent-type").listeners.change();
  assert.equal(h.element("collection-fields").hidden, false);
  h.element("persona").value = "female";
  const fixture = {
    "bank-name": "Demo Prerna Bank", "customer-name": "Demo Asha Verma",
    "emi-amount": "4500.00", "due-date": "2026-10-25", "loan-type": "personal",
    "payment-status": "due"
  };
  for (const [id, value] of Object.entries(fixture)) h.element(id).value = value;
  h.element("payment-methods").selectedOptions = [{value: "official_app"}];
  const starting = h.element("start").listeners.click();
  for (const id of ["agent-type", "persona", "collection-fields", "voice-provider", "llm-provider"]) {
    assert.equal(h.element(id).disabled, true);
  }
  h.element("bank-name").value = "Demo Changed After Start";
  await starting;
  const offer = JSON.parse(h.requests[0].options.body);
  assert.equal(offer.agent_type, "soft-collection");
  assert.equal(offer.persona, "female");
  assert.equal(offer.collection.bank_name, "Demo Prerna Bank");
  assert.equal(offer.collection.fictional, true);
  assert.deepEqual(offer.collection.payment_methods, ["official_app"]);
  await h.element("stop").listeners.click();
  assert.equal(h.requests[2].url, "/api/sessions/opaque-peer/result");
  assert.ok(h.element("collection-result").textContent.includes("demo_only"));
  assert.equal(h.element("collection-fields").disabled, false);
  assert.equal(h.track.stops, 1);
});

test("invalid collection fields fail locally before microphone access", async () => {
  const h = harness();
  h.element("agent-type").value = "soft-collection";
  h.element("bank-name").reportValidity = () => false;
  await h.element("start").listeners.click();
  assert.equal(h.mediaRequests(), 0);
  assert.equal(h.requests.length, 0);
  assert.equal(h.element("status-label").textContent, "Error");
});
