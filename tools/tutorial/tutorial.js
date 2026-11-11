/* The tutorial's timeline.

   Every movement is a Web Animations API animation with an absolute start
   time, so the whole video is a function of one number: seek(t) shows the
   exact frame at t seconds. The renderer calls seek() once per frame; opened
   in a browser, the page simply plays.
*/
(() => {
  "use strict";

  const TOTAL = 38.5; // seconds
  const $ = (id) => document.getElementById(id);
  const EASE = "cubic-bezier(.2,.7,.2,1)";
  const SPRING = "cubic-bezier(.2,1.5,.4,1)";

  // Visible from `at` to `out` (or to the end), with a short entrance and exit.
  function span(el, at, out, options) {
    const o = options || {};
    const inDur = o.inDur === undefined ? 0.45 : o.inDur;
    const outDur = o.outDur === undefined ? 0.35 : o.outDur;
    const base = o.base || "";
    const from = `${base} ${o.from === undefined ? "translateY(14px)" : o.from}`.trim();
    const rest = base || "none";
    const end = out === undefined || out === null ? TOTAL : out + outDur;
    const total = end - at;
    const frames = [
      { opacity: 0, transform: from, offset: 0, easing: o.ease || EASE },
      { opacity: 1, transform: rest, offset: Math.min(1, inDur / total) },
    ];
    if (out === undefined || out === null) {
      frames.push({ opacity: 1, transform: rest, offset: 1 });
    } else {
      frames.push({ opacity: 1, transform: rest, offset: (out - at) / total, easing: "ease-in" });
      frames.push({ opacity: 0, transform: `${base} ${o.to || ""}`.trim() || "none", offset: 1 });
    }
    el.animate(frames, { delay: at * 1000, duration: total * 1000, fill: "both" });
  }

  // Keyframes at absolute times: track(el, [[t, {props}, easing?], ...]).
  function track(el, points) {
    const frames = points.map(([t, props, easing]) => Object.assign({ offset: t / TOTAL }, props, easing ? { easing } : {}));
    if (frames[0].offset > 0) frames.unshift(Object.assign({}, frames[0], { offset: 0 }));
    if (frames[frames.length - 1].offset < 1) frames.push(Object.assign({}, frames[frames.length - 1], { offset: 1, easing: undefined }));
    el.animate(frames, { duration: TOTAL * 1000, fill: "both" });
  }

  // Measured before any animation exists: an element that is about to slide
  // in is, at time zero, still sitting at its starting offset.
  const stage = $("stage").getBoundingClientRect();
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left - stage.left, y: r.top - stage.top, w: r.width, h: r.height };
  };
  const flyer = box($("flyer"));
  const add = box($("add-btn"));

  // ---- scenes ----------------------------------------------------------

  span($("s-title"), 0, 2.5, { from: "none", inDur: 0.01, outDur: 0.5 });
  span(document.querySelector(".title-mark"), 0.15, null, { from: "translateY(18px) scale(.9)", ease: SPRING, inDur: 0.6 });
  span(document.querySelector(".title-line"), 0.4, null, { inDur: 0.55 });
  span(document.querySelector(".title-sub"), 0.8, null, { inDur: 0.55 });

  span($("s-desktop"), 2.5, 25.4, { from: "none", inDur: 0.6, outDur: 0.5 });
  span(document.querySelector(".browser"), 2.6, null, { from: "translateY(22px) scale(.985)", inDur: 0.7 });

  // The shortcut: three keys appear, then go down together.
  span($("bigkeys"), 4.2, 6.5, { from: "translateY(26px)", outDur: 0.3 });
  ["key-cmd", "key-shift", "key-2"].forEach((id, index) => {
    const down = 5.2 + index * 0.05;
    track($(id), [
      [down - 0.01, { transform: "translateY(0)", boxShadow: "0 9px 0 #b9b4c0, 0 22px 34px -10px rgba(20,20,50,.5)", background: "linear-gradient(#ffffff, #ebe8ee)" }, "ease-out"],
      [down + 0.12, { transform: "translateY(7px)", boxShadow: "0 2px 0 #b9b4c0, 0 8px 14px -8px rgba(20,20,50,.5)", background: "linear-gradient(#fff1ee, #f6d5cf)" }],
      [5.85, { transform: "translateY(7px)", boxShadow: "0 2px 0 #b9b4c0, 0 8px 14px -8px rgba(20,20,50,.5)", background: "linear-gradient(#fff1ee, #f6d5cf)" }, "ease-out"],
      [6.05, { transform: "translateY(0)", boxShadow: "0 9px 0 #b9b4c0, 0 22px 34px -10px rgba(20,20,50,.5)", background: "linear-gradient(#ffffff, #ebe8ee)" }],
    ]);
  });

  // The crosshair drags a box around the flyer.
  const pad = 6;
  const sel = { x: flyer.x - pad, y: flyer.y - pad, w: flyer.w + pad * 2, h: flyer.h + pad * 2 };
  const startX = sel.x, startY = sel.y, endX = sel.x + sel.w, endY = sel.y + sel.h;
  const DRAG_FROM = 7.0, DRAG_TO = 8.9, RELEASE = 9.25;

  track($("shade"), [[5.45, { opacity: 0 }], [5.75, { opacity: 1 }], [DRAG_FROM, { opacity: 1 }], [DRAG_FROM + 0.01, { opacity: 0 }]]);
  track($("crosshair"), [
    [5.5, { opacity: 0, transform: `translate(${startX + 330}px, ${startY + 250}px)` }],
    [5.8, { opacity: 1, transform: `translate(${startX + 330}px, ${startY + 250}px)` }, "cubic-bezier(.5,0,.2,1)"],
    [6.75, { opacity: 1, transform: `translate(${startX}px, ${startY}px)` }],
    [DRAG_FROM, { opacity: 1, transform: `translate(${startX}px, ${startY}px)` }, "cubic-bezier(.45,0,.25,1)"],
    [DRAG_TO, { opacity: 1, transform: `translate(${endX}px, ${endY}px)` }],
    [RELEASE, { opacity: 1, transform: `translate(${endX}px, ${endY}px)` }],
    [RELEASE + 0.05, { opacity: 0, transform: `translate(${endX}px, ${endY}px)` }],
  ]);
  // While dragging, the selection's own huge shadow does the dimming, so the
  // part being selected stays bright.
  const dimShadow = "0 0 0 1px rgba(0,0,0,.35), 0 0 0 2400px rgba(10,10,20,.32)";
  track($("selection"), [
    [DRAG_FROM - 0.01, { opacity: 0, left: `${startX}px`, top: `${startY}px`, width: "0px", height: "0px", boxShadow: dimShadow }],
    [DRAG_FROM, { opacity: 1, left: `${startX}px`, top: `${startY}px`, width: "0px", height: "0px", boxShadow: dimShadow }, "cubic-bezier(.45,0,.25,1)"],
    [DRAG_TO, { opacity: 1, left: `${startX}px`, top: `${startY}px`, width: `${sel.w}px`, height: `${sel.h}px`, boxShadow: dimShadow }],
    [RELEASE, { opacity: 1, left: `${startX}px`, top: `${startY}px`, width: `${sel.w}px`, height: `${sel.h}px`, boxShadow: dimShadow }],
    [RELEASE + 0.05, { opacity: 0, left: `${startX}px`, top: `${startY}px`, width: `${sel.w}px`, height: `${sel.h}px`, boxShadow: dimShadow }],
  ]);
  track($("flash"), [[RELEASE, { opacity: 0 }], [RELEASE + 0.06, { opacity: 0.7 }, "ease-out"], [RELEASE + 0.5, { opacity: 0 }]]);

  // ---- Schedru comes forward -------------------------------------------

  const APP_IN = 9.7, REVIEW = 13.3, DONE = 20.5, APP_OUT = 25.3;
  span($("s-app"), APP_IN, APP_OUT, { from: "none", inDur: 0.45, outDur: 0.4 });
  span($("appwin"), APP_IN, null, { from: "translateY(34px) scale(.94)", ease: SPRING, inDur: 0.65 });

  span($("pane-reading"), APP_IN + 0.25, REVIEW - 0.3, { from: "none", outDur: 0.25 });
  span($("read-1"), APP_IN + 0.3, 11.5, { from: "translateY(6px)", outDur: 0.2 });
  span($("read-2"), 11.75, null, { from: "translateY(6px)" });

  span($("pane-review"), REVIEW, DONE - 0.3, { from: "translateY(10px)", outDur: 0.25 });
  // The card fills in piece by piece: this is the "it works it out" moment.
  span($("f-title"), REVIEW + 0.35, null, { from: "translateY(8px)" });
  span($("f-when"), REVIEW + 0.95, null, { from: "translateX(-10px)" });
  span($("f-where"), REVIEW + 1.6, null, { from: "translateX(-10px)" });
  span($("f-notes"), REVIEW + 2.25, null, { from: "translateX(-10px)" });
  span($("c-1"), 16.35, null, { from: "scale(.6)", ease: SPRING, inDur: 0.4 });
  span($("c-2"), 16.75, null, { from: "scale(.6)", ease: SPRING, inDur: 0.4 });
  span($("c-3"), 17.15, null, { from: "scale(.6)", ease: SPRING, inDur: 0.4 });

  // The pointer goes to Add and presses it.
  const target = { x: add.x + add.w * 0.62, y: add.y + add.h * 0.5 };
  const CLICK = 19.85;
  track($("pointer"), [
    [18.3, { opacity: 0, transform: `translate(${target.x + 330}px, ${target.y - 210}px)` }],
    [18.5, { opacity: 1, transform: `translate(${target.x + 330}px, ${target.y - 210}px)` }, "cubic-bezier(.4,0,.2,1)"],
    [19.55, { opacity: 1, transform: `translate(${target.x}px, ${target.y}px)` }],
    [CLICK, { opacity: 1, transform: `translate(${target.x}px, ${target.y}px) scale(1)` }, "ease-out"],
    [CLICK + 0.1, { opacity: 1, transform: `translate(${target.x}px, ${target.y}px) scale(.86)` }, "ease-out"],
    [CLICK + 0.25, { opacity: 1, transform: `translate(${target.x}px, ${target.y}px) scale(1)` }],
    [20.45, { opacity: 1, transform: `translate(${target.x}px, ${target.y}px)` }, "ease-in"],
    [20.8, { opacity: 0, transform: `translate(${target.x + 40}px, ${target.y + 26}px)` }],
  ]);
  track($("add-btn"), [
    [CLICK, { transform: "scale(1)", filter: "brightness(1)" }, "ease-out"],
    [CLICK + 0.1, { transform: "scale(.965)", filter: "brightness(.9)" }, "ease-out"],
    [CLICK + 0.3, { transform: "scale(1)", filter: "brightness(1)" }],
  ]);

  span($("pane-done"), DONE, null, { from: "translateY(10px)" });
  span($("tick"), DONE + 0.15, null, { from: "scale(.3)", ease: SPRING, inDur: 0.5 });
  track($("tick-path"), [[DONE + 0.4, { strokeDashoffset: 30 }, "ease-out"], [DONE + 0.85, { strokeDashoffset: 0 }]]);
  span($("done-card"), DONE + 0.7, null);
  span($("done-actions"), DONE + 1.0, null);

  // ---- the calendar -----------------------------------------------------

  const CAL = 25.2, END = 33.3;
  span($("s-cal"), CAL, END, { from: "none", inDur: 0.5, outDur: 0.5 });
  span(document.querySelector(".cal"), CAL, null, { from: "translateY(20px) scale(.985)", inDur: 0.6 });
  span($("new-ev"), 26.5, null, { from: "translateY(-46px) scale(.9)", ease: SPRING, inDur: 0.6 });
  track($("ring"), [
    [27.0, { opacity: 0, boxShadow: "0 0 0 0 rgba(213,64,43,.55)" }],
    [27.05, { opacity: 1, boxShadow: "0 0 0 0 rgba(213,64,43,.55)" }, "ease-out"],
    [27.9, { opacity: 1, boxShadow: "0 0 0 22px rgba(213,64,43,0)" }],
    [27.95, { opacity: 1, boxShadow: "0 0 0 0 rgba(213,64,43,.45)" }, "ease-out"],
    [28.8, { opacity: 0, boxShadow: "0 0 0 22px rgba(213,64,43,0)" }],
  ]);
  span($("popover"), 28.3, null, { from: "translateX(18px) scale(.96)", ease: SPRING, inDur: 0.5 });

  // ---- the end ----------------------------------------------------------

  span($("s-end"), END, null, { from: "none", inDur: 0.5 });
  span(document.querySelector(".end-title"), END + 0.2, null);
  span($("way-1"), END + 0.6, null, { from: "translateY(18px)" });
  span($("way-2"), END + 0.85, null, { from: "translateY(18px)" });
  span($("way-3"), END + 1.1, null, { from: "translateY(18px)" });
  span($("end-promise"), END + 1.9, null);
  span($("end-brand"), END + 2.5, null);

  // ---- captions ---------------------------------------------------------

  const caption = (id, at, out) => span($(id), at, out, { base: "translateX(-50%)", from: "translateY(16px)", to: "translateY(8px)", outDur: 0.25 });
  caption("cap-1", 3.3, 6.45);
  caption("cap-2", 6.75, 9.45);
  caption("cap-3", 10.0, 13.1);
  caption("cap-4", 13.5, 16.1);
  caption("cap-5", 16.4, 18.45);
  caption("cap-6", 18.75, 20.35);
  caption("cap-7", 20.9, 24.95);
  caption("cap-8", 26.0, 32.9);

  // ---- for the renderer -------------------------------------------------

  window.tutorial = {
    duration: TOTAL,
    seek(seconds) {
      for (const animation of document.getAnimations()) {
        animation.pause();
        animation.currentTime = seconds * 1000;
      }
      // The size readout follows the box, as the Mac's own does.
      const style = getComputedStyle($("selection"));
      $("selection-size").textContent = `${Math.round(parseFloat(style.width))} × ${Math.round(parseFloat(style.height))}`;
    },
  };
})();
