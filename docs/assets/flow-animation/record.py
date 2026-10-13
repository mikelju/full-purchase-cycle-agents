"""Deterministic frame capture of a flow page (virtual rAF, setTimeout and CSS animation clock).

Usage: record.py [page.html]; the default page is purchase-cycle-flow.html.
"""

import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
NAME = sys.argv[1] if len(sys.argv) > 1 else "purchase-cycle-flow.html"
PAGE = (HERE / NAME).as_uri() + "#clean"
FPS = 15
SUB = 4  # virtual 60 Hz rAF, capture every 4th tick
STEP = 1000 / FPS / SUB
FRAMES_DIR = HERE / "frames" / pathlib.Path(NAME).stem
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
for old in FRAMES_DIR.glob("f*.png"):
    old.unlink()

INIT = """
(() => {
  let vnow = 0, rafQ = [], timers = [], tid = 1;
  const realSetTimeout = window.setTimeout;
  window.requestAnimationFrame = cb => { rafQ.push(cb); return rafQ.length; };
  window.cancelAnimationFrame = () => {};
  window.setTimeout = (cb, ms, ...a) => { const id = tid++; timers.push({id, due: vnow + (ms || 0), cb, a}); return id; };
  window.clearTimeout = id => { timers = timers.filter(t => t.id !== id); };
  performance.now = () => vnow;
  const anims = new Map();
  function syncAnims() {
    for (const an of document.getAnimations()) {
      if (!anims.has(an)) { anims.set(an, vnow); an.pause(); }
      an.currentTime = vnow - anims.get(an);
    }
  }
  window.__step = async (dt) => {
    vnow += dt;
    const due = timers.filter(t => t.due <= vnow); timers = timers.filter(t => t.due > vnow);
    for (const t of due) t.cb(...t.a);
    const q = rafQ; rafQ = [];
    for (const cb of q) cb(vnow);
    // let promise continuations run (they schedule the next rAF)
    await new Promise(r => realSetTimeout(r, 0));
    syncAnims();
    return {story: window.lastStoryMs ?? null};
  };
})();
"""

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
    pg.add_init_script(INIT)
    pg.goto(PAGE, wait_until="networkidle")
    pg.evaluate("document.fonts.ready.then(() => document.fonts.size)")
    pg.add_style_tag(
        content="body{margin:0} #svg{width:1280px!important;height:720px!important;max-width:none!important}"
    )
    svg = pg.locator("#svg")
    print("svg box", svg.bounding_box())
    n, end_frame = 0, None
    while True:
        for _ in range(SUB):
            r = pg.evaluate(f"window.__step({STEP})")
        svg.screenshot(path=str(FRAMES_DIR / f"f{n:04d}.png"), animations="allow")
        n += 1
        if r["story"] is not None and end_frame is None:
            end_frame = n + 2 * FPS
            print("story ms", r["story"], "at frame", n)
        if end_frame is not None and n >= end_frame:
            break
        if n > 45 * FPS:
            raise SystemExit("story did not finish")
    print("frames", n)
    b.close()
