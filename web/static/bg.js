/* EV CANTOS - fundo synthwave animado (canvas). Elementos da estetica retrofuturista dos anos 80: ceu em degrade do roxo
   ao amarelo (anoitecer), estrelas, sol listrado, montanhas com contorno neon, palmeiras de Miami, chao em grade com
   perspectiva, faixa de estrada e um conversivel visto de tras (como nos jogos de corrida da epoca). */
const BG = (() => {
  const cv = document.getElementById("bg"), g = cv.getContext("2d");
  const THEME = {
    sunset: { sky: ["#07021a", "#1a0536", "#4a0d5e", "#c2185b", "#ff7a45"], sun: ["#ffe14a", "#ff5fa2"], floor: ["#12042b", "#05010f"],
              grid: "#ff2bd6", grid2: "#00f0ff", mount: "#12042b", rim: "#00f0ff", sunOn: true, palms: true, car: "#ff2bd6" },
    tron:   { sky: ["#000000", "#00070d", "#001a26", "#00364d", "#00566f"], sun: ["#7df9ff", "#00a6c8"], floor: ["#001018", "#000000"],
              grid: "#00e5ff", grid2: "#ff8a00", mount: "#00080d", rim: "#00e5ff", sunOn: false, palms: false, car: "#ff8a00" },
    pacman: { sky: ["#000000", "#00001f", "#02023d", "#05055c", "#0a0a85"], sun: ["#ffe600", "#ff9d00"], floor: ["#00002a", "#000000"],
              grid: "#2f5bff", grid2: "#ffe600", mount: "#000018", rim: "#2f5bff", sunOn: true, palms: false, car: "#ffe600" },
    vapor:  { sky: ["#2b1055", "#5b2a8c", "#b565c9", "#ff9ad5", "#ffd6a5"], sun: ["#fff3a8", "#ff8fd8"], floor: ["#3a1a6b", "#1a0a3a"],
              grid: "#7ffbe8", grid2: "#ff9ee5", mount: "#3a1a6b", rim: "#7ffbe8", sunOn: true, palms: true, car: "#7ffbe8" },
  };
  let W = 0, H = 0, dpr = 1, mode = "synthwave", motion = true, showCar = true, theme = "sunset";
  let lastT = 0, t = 0, stars = [], ridges = [], started = false;

  function ridge(amp, seed) {
    const pts = [];
    for (let i = 0; i <= 48; i++) { const x = i / 48; pts.push(Math.abs(Math.sin(x * seed * 3.1 + seed)) * 0.6 + Math.abs(Math.sin(x * seed * 7.7)) * 0.4); }
    return { pts, amp };
  }
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = cv.width = Math.floor(innerWidth * dpr); H = cv.height = Math.floor(innerHeight * dpr);
    stars = Array.from({ length: 140 }, () => ({ x: Math.random(), y: Math.random() * 0.58, r: Math.random() * 1.3 + 0.3, p: Math.random() * 6.28 }));
    ridges = [ridge(0.09, 5), ridge(0.14, 9)];
    draw();
  }

  function sun(P, hz) {
    const r = Math.min(W, H) * 0.22, cx = W * 0.5, cy = hz - r * 0.3;
    const off = document.createElement("canvas"); off.width = off.height = Math.ceil(r * 2); const o = off.getContext("2d");
    const gr = o.createLinearGradient(0, 0, 0, r * 2); gr.addColorStop(0.15, P.sun[0]); gr.addColorStop(0.85, P.sun[1]);
    o.fillStyle = gr; o.beginPath(); o.arc(r, r, r, 0, 7); o.fill();
    o.globalCompositeOperation = "destination-out"; // listras que crescem para baixo e "correm" devagar
    const slide = (t * 0.03) % 0.1;
    for (let i = 0; i < 8; i++) { const y = r * (0.95 + i * 0.1 + slide), h = r * (0.015 + i * 0.012); o.fillRect(0, y, r * 2, h); }
    g.shadowColor = P.sun[1]; g.shadowBlur = 40 * dpr;
    g.drawImage(off, cx - r, cy - r);
    g.shadowBlur = 0;
  }
  function palm(x, hz, h, dir, sway, P) {
    g.save(); g.translate(x, hz); g.strokeStyle = g.fillStyle = "#07000f"; g.lineCap = "round";
    g.shadowColor = P.rim; g.shadowBlur = 6 * dpr;
    const tx = dir * h * 0.12 + sway, ty = -h;
    g.lineWidth = h * 0.03; g.beginPath(); g.moveTo(0, 0); g.quadraticCurveTo(dir * h * 0.02, -h * 0.5, tx, ty); g.stroke();
    g.lineWidth = h * 0.022;
    for (let i = 0; i < 7; i++) {
      const ang = -Math.PI / 2 + (i - 3) * 0.42 + sway * 0.002, len = h * 0.34;
      g.beginPath(); g.moveTo(tx, ty);
      g.quadraticCurveTo(tx + Math.cos(ang) * len * 0.6, ty + Math.sin(ang) * len * 0.9, tx + Math.cos(ang) * len, ty + Math.sin(ang) * len * 0.7 + len * 0.35);
      g.stroke();
    }
    g.restore();
  }
  function convertible(P, cx, cy, w) { // conversivel visto de tras, lanternas neon
    const h = w * 0.42;
    g.save(); g.translate(cx, cy);
    g.fillStyle = "#06000d";
    g.beginPath(); g.moveTo(-w / 2, h * 0.2); g.lineTo(-w * 0.46, -h * 0.15); g.lineTo(-w * 0.32, -h * 0.25); g.lineTo(w * 0.32, -h * 0.25);
    g.lineTo(w * 0.46, -h * 0.15); g.lineTo(w / 2, h * 0.2); g.lineTo(w * 0.48, h * 0.5); g.lineTo(-w * 0.48, h * 0.5); g.closePath(); g.fill();
    for (const dx of [-0.12, 0.12]) { g.beginPath(); g.arc(w * dx, -h * 0.36, h * 0.15, 0, 7); g.fill(); }
    g.shadowColor = P.car; g.shadowBlur = 18 * dpr; g.fillStyle = P.car;
    g.fillRect(-w * 0.44, h * 0.05, w * 0.2, h * 0.12); g.fillRect(w * 0.24, h * 0.05, w * 0.2, h * 0.12); g.fillRect(-w * 0.2, h * 0.08, w * 0.4, h * 0.035);
    g.restore();
  }

  function draw() {
    if (!W || !H) return; // janela ainda sem tamanho (ao abrir): o evento resize redesenha depois
    g.clearRect(0, 0, W, H);
    if (mode === "none") return;
    const P = THEME[theme] || THEME.sunset, hz = H * 0.62, full = mode === "synthwave";
    // ceu
    const sk = g.createLinearGradient(0, 0, 0, hz);
    P.sky.forEach((c, i) => sk.addColorStop(i / (P.sky.length - 1), c));
    g.fillStyle = sk; g.fillRect(0, 0, W, hz + 1);
    for (const s of stars) { g.fillStyle = `rgba(255,255,255,${0.35 + 0.65 * Math.abs(Math.sin(t * 1.5 + s.p))})`; g.fillRect(s.x * W, s.y * H, s.r * dpr, s.r * dpr); }
    if (full && P.sunOn) sun(P, hz);
    if (full) { // montanhas com contorno neon
      ridges.forEach((rg, i) => {
        g.beginPath(); g.moveTo(0, hz);
        rg.pts.forEach((v, k) => g.lineTo((k / 48) * W, hz - v * rg.amp * H));
        g.lineTo(W, hz); g.closePath();
        g.fillStyle = P.mount; g.globalAlpha = i ? 1 : 0.7; g.fill(); g.globalAlpha = 1;
        if (i) { g.strokeStyle = P.rim; g.lineWidth = 1.2 * dpr; g.shadowColor = P.rim; g.shadowBlur = 10 * dpr; g.stroke(); g.shadowBlur = 0; }
      });
    }
    // chao
    const fl = g.createLinearGradient(0, hz, 0, H); fl.addColorStop(0, P.floor[0]); fl.addColorStop(1, P.floor[1]);
    g.fillStyle = fl; g.fillRect(0, hz, W, H - hz);
    g.shadowColor = P.grid; g.shadowBlur = 8 * dpr; g.strokeStyle = P.grid; g.lineWidth = 1.2 * dpr;
    const N = 14, off = motion ? (t * 0.7) % 1 : 0;
    for (let i = 0; i < N; i++) {
      const z = (i + off) / N, y = hz + (H - hz) * Math.pow(z, 2.3);
      g.globalAlpha = 0.15 + 0.75 * z; g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
    }
    g.globalAlpha = 0.55;
    for (let k = -18; k <= 18; k++) { g.beginPath(); g.moveTo(W / 2 + k * W * 0.006, hz); g.lineTo(W / 2 + k * W * 0.11, H); g.stroke(); }
    g.globalAlpha = 1; g.shadowBlur = 0;
    g.fillStyle = P.grid2; g.globalAlpha = 0.9; g.shadowColor = P.grid2; g.shadowBlur = 14 * dpr; g.fillRect(0, hz - 1, W, 2 * dpr); g.shadowBlur = 0; g.globalAlpha = 1;
    if (full) { // faixa central da estrada correndo
      g.fillStyle = P.grid2; g.globalAlpha = 0.8;
      for (let i = 0; i < N; i++) {
        const z0 = (i + off) / N, z1 = z0 + 0.4 / N, y0 = hz + (H - hz) * Math.pow(z0, 2.3), y1 = hz + (H - hz) * Math.pow(z1, 2.3), w0 = z0 * W * 0.012, w1 = z1 * W * 0.012;
        g.beginPath(); g.moveTo(W / 2 - w0, y0); g.lineTo(W / 2 + w0, y0); g.lineTo(W / 2 + w1, y1); g.lineTo(W / 2 - w1, y1); g.closePath(); g.fill();
      }
      g.globalAlpha = 1;
      if (P.palms) { const sw = motion ? Math.sin(t * 0.8) * 3 * dpr : 0; palm(W * 0.07, hz + 4 * dpr, H * 0.36, 1, sw, P); palm(W * 0.93, hz + 4 * dpr, H * 0.4, -1, -sw, P); }
      if (showCar) convertible(P, W / 2, H * 0.86 + (motion ? Math.sin(t * 3) * 1.5 * dpr : 0), W * 0.085);
    }
  }
  function frame(ts) {
    requestAnimationFrame(frame);
    if (document.hidden || !motion || mode === "none" || ts - lastT < 33) return; // ~30 quadros/s
    t += Math.min(0.1, (ts - lastT) / 1000); lastT = ts; draw();
  }
  function configure(c) {
    if (c.mode != null) mode = c.mode;
    if (c.theme != null) theme = c.theme;
    if (c.motion != null) motion = c.motion;
    if (c.car != null) showCar = c.car;
    cv.style.display = mode === "none" ? "none" : "block";
    if (!W) resize(); else draw();
    if (!started) { started = true; requestAnimationFrame(frame); }
  }
  addEventListener("resize", resize);
  return { configure, resize };
})();
