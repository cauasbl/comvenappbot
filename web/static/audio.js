/* EV CANTOS - audio. Musicas synthwave geradas na hora com WebAudio (osciladores e ruido: nenhuma gravacao de
   terceiros), efeitos sonoros de navegacao e reproducao das musicas que o usuario colocar na pasta "music".
   Navegadores so liberam som depois de um gesto do usuario; por isso unlock() e chamado no PRESS START. */
const EVA = (() => {
  const TRACKS = {
    // key = nota MIDI da tonica; prog = [semitons a partir da tonica, "m"/"M"]; lead = motivo (semitons) a cada 2 compassos
    "neon-drive":    { name: "NEON DRIVE",    bpm: 108, key: 57, prog: [[0, "m"], [-4, "M"], [-9, "M"], [-2, "M"]], lead: [0, null, 3, null, 7, null, 12, 10] },
    "midnight-grid": { name: "MIDNIGHT GRID", bpm: 96,  key: 52, prog: [[0, "m"], [-4, "M"], [3, "M"], [-2, "M"]],  lead: [7, null, null, 10, 12, null, 10, null] },
    "arcade-sunset": { name: "ARCADE SUNSET", bpm: 118, key: 50, prog: [[0, "m"], [-4, "M"], [3, "M"], [-2, "M"]],  lead: [12, null, 10, null, 7, 10, 12, null] },
  };
  const cfg = { music: true, music_volume: 0.5, track: "neon-drive", sfx: true, sfx_volume: 0.7 };
  let ctx = null, musicBus, sfxBus, leadBus, delayIn, noiseBuf;
  let unlocked = false, current = null, timer = null, step = 0, nextTime = 0, el = null, srcNode = null;
  const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12);

  function ensure() {
    if (ctx) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ctx = new AC();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18; comp.ratio.value = 6;
    const master = ctx.createGain();
    master.gain.value = 0.9;
    master.connect(comp); comp.connect(ctx.destination);
    musicBus = ctx.createGain(); sfxBus = ctx.createGain(); leadBus = ctx.createGain();
    musicBus.connect(master); sfxBus.connect(master); leadBus.connect(musicBus);
    // eco no arpejo e no lead: o "espaco" tipico do estilo
    delayIn = ctx.createGain();
    const dl = ctx.createDelay(1), fb = ctx.createGain(), lp = ctx.createBiquadFilter();
    dl.delayTime.value = 0.33; fb.gain.value = 0.32; lp.type = "lowpass"; lp.frequency.value = 2200;
    delayIn.connect(dl); dl.connect(lp); lp.connect(fb); fb.connect(dl); lp.connect(musicBus);
    leadBus.connect(delayIn);
    noiseBuf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    applyGains();
    return true;
  }
  function applyGains() {
    if (!ctx) return;
    const t = ctx.currentTime;
    musicBus.gain.setTargetAtTime(cfg.music ? cfg.music_volume * 0.6 : 0, t, 0.05);
    sfxBus.gain.setTargetAtTime(cfg.sfx ? cfg.sfx_volume * 0.8 : 0, t, 0.02);
  }

  // ---------- vozes ----------
  function osc(type, freq, t, len, dest, o = {}) {
    const { peak = 0.2, a = 0.005, d = 0.05, s = 0.6, r = 0.05, detune = 0, lp = null } = o;
    const os = ctx.createOscillator(), g = ctx.createGain();
    os.type = type; os.frequency.setValueAtTime(freq, t); os.detune.value = detune;
    const hold = t + Math.max(len, a + d);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(peak, t + a);
    g.gain.linearRampToValueAtTime(peak * s, t + a + d);
    g.gain.setValueAtTime(peak * s, hold);
    g.gain.linearRampToValueAtTime(0.0001, hold + r);
    let n = os;
    if (lp) { const f = ctx.createBiquadFilter(); f.type = "lowpass"; f.frequency.value = lp; os.connect(f); n = f; }
    n.connect(g); g.connect(dest);
    os.start(t); os.stop(hold + r + 0.05);
    return os;
  }
  function noise(t, len, dest, { type = "highpass", freq = 7000, peak = 0.2, q = 0.7 } = {}) {
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = noiseBuf; f.type = type; f.frequency.value = freq; f.Q.value = q;
    g.gain.setValueAtTime(peak, t); g.gain.exponentialRampToValueAtTime(0.001, t + len);
    s.connect(f); f.connect(g); g.connect(dest);
    s.start(t, Math.random() * 0.5); s.stop(t + len + 0.02);
  }
  const kick = (t) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.setValueAtTime(150, t); o.frequency.exponentialRampToValueAtTime(42, t + 0.12);
    g.gain.setValueAtTime(0.9, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.22);
    o.connect(g); g.connect(musicBus); o.start(t); o.stop(t + 0.25);
  };
  const snare = (t) => { // "gated reverb": cauda de ruido que corta de repente, marca dos anos 80
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = noiseBuf; f.type = "bandpass"; f.frequency.value = 1800; f.Q.value = 0.8;
    g.gain.setValueAtTime(0.5, t); g.gain.setValueAtTime(0.38, t + 0.03); g.gain.setValueAtTime(0.3, t + 0.17);
    g.gain.linearRampToValueAtTime(0.0001, t + 0.19);
    s.connect(f); f.connect(g); g.connect(musicBus); s.start(t, Math.random() * 0.5); s.stop(t + 0.22);
    osc("triangle", 190, t, 0.1, musicBus, { peak: 0.3, a: 0.001, d: 0.05, s: 0.2, r: 0.04 });
  };
  const hat = (t, v) => noise(t, 0.045, musicBus, { freq: 7500, peak: v });
  const bass = (t, m, len) => osc("sawtooth", mtof(m), t, len, musicBus, { peak: 0.28, a: 0.004, d: 0.08, s: 0.5, r: 0.05, lp: 520 });
  const arp = (t, m, len) => osc("square", mtof(m), t, len, leadBus, { peak: 0.05, a: 0.003, d: 0.04, s: 0.3, r: 0.04, lp: 2600 });
  const pad = (t, notes, len) => notes.forEach((m) => [-7, 7].forEach((dt) =>
    osc("sawtooth", mtof(m), t, len, musicBus, { peak: 0.045, a: 0.4, d: 0.3, s: 0.8, r: 0.5, lp: 900, detune: dt })));
  const lead = (t, m, len) => { // lead com vibrato, lembra um sax sintetizado
    const o = osc("sawtooth", mtof(m), t, len, leadBus, { peak: 0.07, a: 0.03, d: 0.1, s: 0.7, r: 0.12, lp: 2800 });
    const l = ctx.createOscillator(), lg = ctx.createGain();
    l.frequency.value = 5.2; lg.gain.value = 8; l.connect(lg); lg.connect(o.detune);
    l.start(t); l.stop(t + len + 0.3);
  };

  // ---------- sequenciador ----------
  function stepAt(T, n, t, spb) {
    const bar = Math.floor(n / 16), s = n % 16, [off, q] = T.prog[bar % T.prog.length], root = T.key + off;
    const tri = q === "m" ? [0, 3, 7] : [0, 4, 7];
    if (s % 4 === 0) kick(t);
    if (s === 4 || s === 12) snare(t);
    if (s % 2 === 0) hat(t, s % 4 === 2 ? 0.2 : 0.1);
    if (s % 2 === 0) bass(t, root - 12, spb * 1.7);
    arp(t, root + 12 + [tri[0], tri[1], tri[2], 12][s % 4], spb * 0.8);
    if (s === 0) pad(t, tri.map((i) => root + i), spb * 16);
    if (bar % 2 === 1 && s % 2 === 0) { const m = T.lead[s / 2]; if (m != null) lead(t, root + 24 + m, spb * 1.8); }
  }
  function startSynth(id) {
    const T = TRACKS[id];
    if (!T) return;
    stopMusic();
    const spb = 60 / T.bpm / 4;
    step = 0; nextTime = ctx.currentTime + 0.12; current = id;
    timer = setInterval(() => {
      if (nextTime < ctx.currentTime - 0.2) nextTime = ctx.currentTime + 0.05; // aba em segundo plano atrasou: nao despeja notas de uma vez
      while (nextTime < ctx.currentTime + 0.3) { stepAt(T, step, nextTime, spb); nextTime += spb; step++; }
    }, 60);
  }
  function startFile(name) {
    stopMusic();
    el = new Audio("/music/" + encodeURIComponent(name));
    el.loop = true;
    srcNode = ctx.createMediaElementSource(el);
    srcNode.connect(musicBus);
    el.play().catch(() => {});
    current = "file:" + name;
  }
  function stopMusic() {
    if (timer) { clearInterval(timer); timer = null; }
    if (el) { el.pause(); el.removeAttribute("src"); try { srcNode.disconnect(); } catch (e) {} el = null; srcNode = null; }
    current = null;
  }
  function playTrack(id) {
    if (!ensure()) return;
    if (ctx.state === "suspended") ctx.resume();
    if (String(id).startsWith("file:")) startFile(id.slice(5)); else startSynth(id);
  }

  // ---------- efeitos sonoros ----------
  function tone(f, len, o = {}) {
    const { type = "square", peak = 0.25, to = null, at = 0, a = 0.003 } = o;
    const t = ctx.currentTime + at, os = ctx.createOscillator(), g = ctx.createGain();
    os.type = type; os.frequency.setValueAtTime(f, t);
    if (to) os.frequency.exponentialRampToValueAtTime(to, t + len);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + a); g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    os.connect(g); g.connect(sfxBus); os.start(t); os.stop(t + len + 0.02);
  }
  const SFX = {
    move:   () => tone(620, 0.04, { peak: 0.12 }),
    select: () => { tone(440, 0.06); tone(880, 0.09, { at: 0.06 }); },
    back:   () => tone(520, 0.12, { to: 260, peak: 0.2 }),
    ok:     () => { tone(660, 0.07, { peak: 0.18 }); tone(990, 0.12, { at: 0.07, peak: 0.18 }); },
    err:    () => { tone(180, 0.18, { type: "sawtooth", peak: 0.22 }); tone(140, 0.2, { type: "sawtooth", at: 0.12, peak: 0.22 }); },
    start:  () => { [262, 330, 392, 523, 659, 784].forEach((f, i) => tone(f, 0.12, { at: i * 0.07, peak: 0.2 })); tone(1046, 0.4, { at: 0.45, peak: 0.18, type: "triangle" }); },
    coin:   () => { tone(988, 0.06, { peak: 0.2 }); tone(1319, 0.35, { at: 0.06, peak: 0.2 }); },
    scan:   () => tone(200, 0.5, { to: 1200, type: "sawtooth", peak: 0.18 }),
    done:   () => [523, 659, 784, 1046].forEach((f, i) => tone(f, 0.14, { at: i * 0.09, peak: 0.2 })),
    alert:  () => [784, 988, 1175, 1568].forEach((f, i) => tone(f, 0.1, { at: i * 0.07, peak: 0.18, type: "triangle" })),
  };

  // ---------- API ----------
  function configure(c) {
    const wasMusic = cfg.music;
    Object.assign(cfg, c);
    applyGains();
    if (!unlocked) return;
    if (!cfg.music) stopMusic();
    else if (!current || cfg.track !== current || !wasMusic) playTrack(cfg.track);
  }
  function unlock() {
    if (!ensure()) return false;
    ctx.resume();
    if (!unlocked) { unlocked = true; if (cfg.music) playTrack(cfg.track); }
    return true;
  }
  function sfx(name) { if (ctx && unlocked && cfg.sfx && SFX[name]) SFX[name](); }
  return { TRACKS, SFX_NAMES: Object.keys(SFX), configure, unlock, sfx, playTrack, stop: stopMusic,
           get unlocked() { return unlocked; }, get current() { return current; },
           get state() { return ctx ? ctx.state : "none"; }, get clock() { return ctx ? ctx.currentTime : 0; } };
})();
