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
      disabled: id === "stop", value: id === "voice-provider" ? "sarvam" : "",
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
      return {ok: true, json: async () => ({sdp: "synthetic-answer", type: "answer", pc_id: "opaque-peer"})};
    }
  };
  vm.runInNewContext(source, sandbox);
  return {element, requests, track, peers, contexts, nodes, media, mediaRequests: () => mediaRequests};
}

for (const provider of ["sarvam", "google"]) {
  test(`${provider}: choice sent once, locked while active, media and meters cleaned`, async () => {
    const h = harness();
    assert.equal(h.mediaRequests(), 0);
    h.element("voice-provider").value = provider;
    const starting = h.element("start").listeners.click();
    assert.equal(h.element("voice-provider").disabled, true);
    assert.equal(h.element("status-label").textContent, "Connecting");
    await starting;
    const offer = JSON.parse(h.requests[0].options.body);
    assert.deepEqual(offer, {sdp: "synthetic-sdp", type: "offer", voice_provider: provider});
    assert.equal(h.element("status-label").textContent, "Connected");
    assert.equal(h.element("voice-provider").disabled, true);
    assert.equal(h.element("start").disabled, true);
    await h.element("start").listeners.click();
    assert.equal(h.requests.length, 1);
    h.element("stop").listeners.click();
    assert.equal(h.element("status-label").textContent, "Disconnected");
    assert.equal(h.element("voice-provider").disabled, false);
    assert.equal(h.track.stops, 1);
    assert.equal(h.contexts[0].closes, 1);
    assert.ok(h.nodes.every(n => n.disconnects === 1));
    assert.equal(h.element("speaker").srcObject, null);
    assert.equal(h.requests[1].url, "/api/sessions/opaque-peer/stop");
  });
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
