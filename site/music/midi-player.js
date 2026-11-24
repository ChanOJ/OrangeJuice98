// Tiny Standard MIDI File player (Web Audio oscillators and noise, no soundfont, no dependency).
// Replaces the Internet Explorer <bgsound src="x.mid" loop="-1"> of hack.htm: browsers do not play it.
// Usage: <script src="/music/midi-player.js" data-midi="Techno78.mid"></script>; adds a play/stop button
// and starts on the first click or key press of the visitor (autoplay is blocked until then). Loops forever.
(function () {
  var script = document.currentScript;
  var url = script && script.getAttribute('data-midi');
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!url || !AC) return;

  function parse(buf) {
    var d = new DataView(buf), p = 0;
    function u32() { var v = d.getUint32(p); p += 4; return v; }
    function u16() { var v = d.getUint16(p); p += 2; return v; }
    function vlq() { var v = 0, b; do { b = d.getUint8(p++); v = (v << 7) | (b & 127); } while (b & 128); return v; }
    if (u32() !== 0x4d546864) throw new Error('not a MIDI file');
    u32(); u16();
    var ntracks = u16(), div = u16(), ev = [], tempos = [[0, 500000]];
    for (var t = 0; t < ntracks; t++) {
      if (u32() !== 0x4d54726b) break;
      var end = u32() + p, tick = 0, run = 0;
      while (p < end) {
        tick += vlq();
        var s = d.getUint8(p);
        if (s === 0xff) {
          var type = d.getUint8(p + 1); p += 2; var len = vlq();
          if (type === 0x51) tempos.push([tick, (d.getUint8(p) << 16) | (d.getUint8(p + 1) << 8) | d.getUint8(p + 2)]);
          p += len; continue;
        }
        if (s === 0xf0 || s === 0xf7) { p++; p += vlq(); continue; }
        if (s & 128) { run = s; p++; }
        var hi = run & 0xf0, ch = run & 15, a = d.getUint8(p++), b = 0;
        if (hi !== 0xc0 && hi !== 0xd0) b = d.getUint8(p++);
        if (hi === 0x90 && b === 0) hi = 0x80;
        if (hi === 0x90 || hi === 0x80 || hi === 0xc0) ev.push({ tick: tick, type: hi, ch: ch, a: a, b: b });
      }
      p = end;
    }
    tempos.sort(function (x, y) { return x[0] - y[0]; });
    function sec(tick) {
      var s = 0, last = 0, tp = 500000;
      for (var i = 0; i < tempos.length && tempos[i][0] < tick; i++) { s += (tempos[i][0] - last) * tp / 1e6 / div; last = tempos[i][0]; tp = tempos[i][1]; }
      return s + (tick - last) * tp / 1e6 / div;
    }
    ev.forEach(function (e) { e.t = sec(e.tick); });
    ev.sort(function (x, y) { return x.t - y.t || x.tick - y.tick; });
    return { events: ev, length: ev.length ? ev[ev.length - 1].t + 1 : 0 };
  }

  var song, ctx, master, noise, timer, playing = false, base = 0, idx = 0, programs = [], held = {};

  function wave(prog) {
    if (prog < 16) return 'triangle';
    if (prog < 24) return 'square';
    if (prog < 40) return 'sawtooth';
    if (prog < 56) return 'sawtooth';
    if (prog < 72) return 'sawtooth';
    if (prog >= 80 && prog < 88) return 'square';
    return prog < 104 ? 'triangle' : 'sine';
  }
  function hz(n) { return 440 * Math.pow(2, (n - 69) / 12); }

  function tone(n, vel, t, prog) {
    var o = ctx.createOscillator(), g = ctx.createGain(), peak = 0.22 * vel / 127;
    o.type = wave(prog); o.frequency.value = hz(n);
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(peak, t + 0.01);
    g.gain.setTargetAtTime(peak * 0.6, t + 0.02, 0.4);
    o.connect(g); g.connect(master); o.start(t);
    return { o: o, g: g };
  }
  function release(v, t) {
    v.g.gain.cancelScheduledValues(t); v.g.gain.setTargetAtTime(0, t, 0.05); v.o.stop(t + 0.4);
  }
  function drum(n, vel, t) {
    var g = ctx.createGain(), v = vel / 127;
    if (n === 35 || n === 36) {
      var o = ctx.createOscillator(); o.frequency.setValueAtTime(150, t); o.frequency.exponentialRampToValueAtTime(40, t + 0.12);
      g.gain.setValueAtTime(0.6 * v, t); g.gain.exponentialRampToValueAtTime(0.001, t + 0.18);
      o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.2); return;
    }
    var src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), hat = n === 42 || n === 44 || n === 46, len = hat ? 0.05 : 0.15;
    src.buffer = noise; f.type = 'highpass'; f.frequency.value = hat ? 7000 : 1500;
    g.gain.setValueAtTime(0.3 * v, t); g.gain.exponentialRampToValueAtTime(0.001, t + len);
    src.connect(f); f.connect(g); g.connect(master); src.start(t); src.stop(t + len + 0.02);
  }

  function handle(e, t) {
    var key = e.ch + ':' + e.a;
    if (e.type === 0xc0) { programs[e.ch] = e.a; return; }
    if (e.type === 0x90) {
      if (e.ch === 9) { drum(e.a, e.b, t); return; }
      if (held[key]) release(held[key], t);
      held[key] = tone(e.a, e.b, t, programs[e.ch] || 0);
    } else if (held[key]) { release(held[key], t); delete held[key]; }
  }

  function pump() {
    var now = ctx.currentTime;
    while (playing && song.events.length) {
      var e = song.events[idx], t = base + e.t;
      if (t > now + 0.6) break;
      handle(e, Math.max(t, now));
      if (++idx >= song.events.length) { idx = 0; base += song.length; }
    }
  }

  function start() {
    if (playing || !song) return;
    if (!ctx) {
      ctx = new AC();
      var comp = ctx.createDynamicsCompressor(); master = ctx.createGain(); master.gain.value = 0.5;
      master.connect(comp); comp.connect(ctx.destination);
      noise = ctx.createBuffer(1, ctx.sampleRate * 0.3, ctx.sampleRate);
      var data = noise.getChannelData(0); for (var i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    }
    ctx.resume(); playing = true; idx = 0; held = {}; programs = []; base = ctx.currentTime + 0.1;
    timer = setInterval(pump, 100); btn.textContent = '\u275A\u275A music'; btn.dataset.state = 'playing';
  }
  function stop() {
    playing = false; clearInterval(timer); btn.textContent = '\u25B6 music'; btn.dataset.state = 'stopped';
    if (ctx) { ctx.close(); ctx = null; }
  }

  var btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = '\u25B6 music';
  btn.style.cssText = 'position:fixed;right:8px;bottom:8px;background:#000;color:#FF8040;border:1px solid #FF8040;font:11px Verdana,sans-serif;padding:3px 8px;cursor:pointer;z-index:9';
  btn.addEventListener('click', function (ev) { ev.stopPropagation(); playing ? stop() : start(); });
  var first = function (ev) { if (ev && ev.target === btn) return; document.removeEventListener('pointerdown', first); document.removeEventListener('keydown', first); if (!playing && btn.dataset.state !== 'stopped') start(); };
  document.addEventListener('pointerdown', first); document.addEventListener('keydown', first);

  fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
    .then(function (b) { song = parse(b); document.body.appendChild(btn); })
    .catch(function () { /* no music: the page still works */ });
})();
