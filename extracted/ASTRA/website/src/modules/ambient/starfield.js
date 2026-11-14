// Time-based star movement stays consistent on both fast and slow displays.
// No animation work runs while paused or while the browser tab is hidden.
export function createStarfield(canvas) {
  const context = canvas.getContext('2d');
  let stars = [];
  let width = 0;
  let height = 0;
  let frame = 0;
  let running = false;
  let lastTime = 0;
  let elapsed = 0;

  function draw() {
    context.clearRect(0, 0, width, height);
    // A drifting monochrome glow adds depth without flashing behind the text.
    const glowX = width * (.55 + Math.sin(elapsed * .045) * .2);
    const glowY = height * (.35 + Math.cos(elapsed * .035) * .18);
    const glow = context.createRadialGradient(glowX, glowY, 0, glowX, glowY, width * .65);
    glow.addColorStop(0, 'rgba(170,175,190,0.085)');
    glow.addColorStop(1, 'rgba(170,175,190,0)');
    context.fillStyle = glow;
    context.fillRect(0, 0, width, height);

    for (const star of stars) {
      const alpha = .28 + (Math.sin(elapsed * .6 + star.phase) + 1) * .15;
      context.fillStyle = `rgba(235,236,242,${alpha})`;
      context.beginPath();
      context.arc(star.x, star.y, star.radius, 0, Math.PI * 2);
      context.fill();
      if (star.radius > 1.2) {
        context.strokeStyle = `rgba(235,236,242,${alpha * .2})`;
        context.beginPath();
        context.moveTo(star.x - 4, star.y);
        context.lineTo(star.x + 4, star.y);
        context.moveTo(star.x, star.y - 4);
        context.lineTo(star.x, star.y + 4);
        context.stroke();
      }
    }
  }

  function animate(time) {
    // Render at approximately 30 fps to keep the ambient layer inexpensive.
    if (time - lastTime >= 32) {
      const delta = lastTime ? Math.min((time - lastTime) / 1000, .1) : 0;
      lastTime = time;
      elapsed += delta;
      for (const star of stars) {
        star.x = (star.x + star.speed * delta + width) % width;
        star.y = (star.y + star.speed * .28 * delta + height) % height;
      }
      draw();
    }
    frame = requestAnimationFrame(animate);
  }

  function resize() {
    const ratio = Math.min(devicePixelRatio || 1, 2);
    width = innerWidth;
    height = innerHeight;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    stars = Array.from({ length: Math.min(170, Math.round(width * height / 6500)) }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      radius: .45 + Math.random(),
      speed: 4 + Math.random() * 9,
      phase: Math.random() * Math.PI * 2
    }));
    draw();
  }

  addEventListener('resize', resize);
  resize();
  return {
    setRunning(value) {
      if (running === value) return;
      running = value;
      cancelAnimationFrame(frame);
      lastTime = 0;
      if (running) frame = requestAnimationFrame(animate);
    }
  };
}
