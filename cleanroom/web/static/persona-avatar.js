// Clean-room reimplementation of a procedural persona-avatar technique seen in the grokBot repo. Generalized; not affiliated with or derived from any shipped binary.
//
// persona-avatar.js — animated procedural persona avatar as a dependency-free
// plain script (no build step, no ES modules). Port of PersonaAvatar.tsx +
// geometry.ts (TypeScript) and motion.py + tk.py (Python).
//
// Rendering model (canvas 2D, mirroring tk.py):
//   - the procedural path is flattened once into polygon samples (samplePath)
//     and redrawn each frame as a filled polygon, with a real diagonal
//     linear gradient (Canvas 2D has gradients; tkinter did not)
//   - two oval eyes at (±29, -8) viewbox units, squashed vertically per
//     motion state and translated a few px toward the pointer ("gaze")
//   - a quadratic smile polyline from (-20,24) via (0,38) to (20,24) for
//     the happy family of states
//
// Animation model: a single rAF loop computes bob/tilt from the MOTION
// table and layers one-shot actions (spin / bounce / burst) on top.
//
// Usage:
//   <script src="persona-avatar.js"></script>
//   const av = new PersonaAvatar(containerEl, "agent-7", {
//     state: "working", sizePx: 64, followPointer: true, paused: false,
//     color: null, shape: null,
//     followTarget: null,   // {x, y} in viewport coords: gaze at a fixed point
//                           // instead of the pointer (setFollowTarget(pOrNull))
//     spinSignal: 0,        // bump via setSpinSignal(n) to fire a declarative spin
//     emphasis: false,      // true -> drop-shadow halo + data-emphasis attr
//   });
//   av.setState("happy"); av.spin(); av.bounce(); av.burst(); av.destroy();
//   av.setFollowTarget({ x: 400, y: 120 }); av.setSpinSignal(1);
//   av.setEmphasis(true);
//
// The pure pieces (hashString, resolveIdentity, MOTION, ...) are exposed on
// the PersonaAvatar namespace. All DOM-touching code lives inside the class,
// so this file can be loaded/eval'd in Node without a DOM for the pure
// functions (in that case only the pure functions are exported).

(function (global) {
  "use strict";

  var TAU = Math.PI * 2;

  /** Round to 2 decimals so generated path data stays compact. */
  function snap(value) {
    return Math.round(value * 100) / 100;
  }

  function clamp(value, min, max) {
    return value < min ? min : value > max ? max : value;
  }

  /* ---------------------------------------------------------------------- */
  /* Viewbox                                                                 */
  /* ---------------------------------------------------------------------- */

  var VIEW_BOX_SIZE = 240;
  var CENTER = VIEW_BOX_SIZE / 2;
  /** Nominal diameter a normalized shape is scaled toward. */
  var TARGET_DIAMETER = 204;

  /* ---------------------------------------------------------------------- */
  /* Deterministic identity: string -> shape + color                         */
  /* ---------------------------------------------------------------------- */

  /** FNV-1a 32-bit hash: fast, stable, good enough for identity bucketing. */
  function hashString(value) {
    var hash = 0x811c9dc5;
    for (var i = 0; i < value.length; i += 1) {
      hash = Math.imul(hash ^ value.charCodeAt(i), 0x01000193);
    }
    return hash >>> 0;
  }

  /** mulberry32: tiny seeded PRNG, deterministic across runs and platforms. */
  function mulberry32(seed) {
    var state = seed >>> 0;
    return function () {
      state = (state + 0x6d2b79f5) | 0;
      var next = Math.imul(state ^ (state >>> 15), 1 | state);
      next = (next + Math.imul(next ^ (next >>> 7), 61 | next)) ^ next;
      return ((next ^ (next >>> 14)) >>> 0) / 4294967296;
    };
  }

  /** Extra avalanche so the shape pick is decorrelated from the color pick. */
  function mix32(value) {
    var hash = value >>> 0;
    hash = Math.imul(hash ^ (hash >>> 16), 0x45d9f3b);
    hash = Math.imul(hash ^ (hash >>> 13), 0x9e3779b1);
    return (hash ^ (hash >>> 16)) >>> 0;
  }

  /* ---------------------------------------------------------------------- */
  /* Color palette (user-extensible)                                         */
  /* ---------------------------------------------------------------------- */

  var PALETTE = {
    red: { from: "#ff5a6a", to: "#d92338" },
    orange: { from: "#ff8a3d", to: "#f96a00" },
    amber: { from: "#ffb84d", to: "#f59c00" },
    green: { from: "#22d486", to: "#00a35e" },
    teal: { from: "#2fd0bd", to: "#00a896" },
    blue: { from: "#4da3ff", to: "#1573e6" },
    violet: { from: "#b18aff", to: "#7d4ce0" },
    magenta: { from: "#ff74c2", to: "#e0309a" },
    brown: { from: "#b08a63", to: "#8a5f3a" },
    slate: { from: "#a8a8a8", to: "#6f6f6f" },
  };

  function registerColor(name, stops) {
    PALETTE[name] = stops;
  }

  function colorStops(name) {
    return PALETTE[name] || PALETTE.slate || { from: "#a8a8a8", to: "#6f6f6f" };
  }

  /** '#rrggbb' + alpha -> 'rgba(r, g, b, a)' for canvas shadow colors. */
  function hexToRgba(hex, alpha) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
  }

  /* ---------------------------------------------------------------------- */
  /* Path builder                                                            */
  /* ---------------------------------------------------------------------- */

  /**
   * Minimal absolute-coordinate path builder. Deliberately emits only M/L/C/Q/Z
   * so `samplePath` below has exactly four command shapes to understand.
   */
  function PathBuilder() {
    this.d = "";
    this.x = 0;
    this.y = 0;
  }

  PathBuilder.prototype.move = function (x, y) {
    this.d += "M" + snap(x) + " " + snap(y);
    this.x = x;
    this.y = y;
    return this;
  };

  PathBuilder.prototype.line = function (x, y) {
    this.d += "L" + snap(x) + " " + snap(y);
    this.x = x;
    this.y = y;
    return this;
  };

  PathBuilder.prototype.curve = function (cx1, cy1, cx2, cy2, x, y) {
    this.d += "C" + snap(cx1) + " " + snap(cy1) + " " + snap(cx2) + " " + snap(cy2) + " " + snap(x) + " " + snap(y);
    this.x = x;
    this.y = y;
    return this;
  };

  /**
   * Rounded corner at `current` between segments prev->current->next.
   * Trims `radius` off both sides of the vertex and joins with a quadratic
   * whose control point is the vertex itself.
   */
  PathBuilder.prototype.corner = function (previous, current, next, radius) {
    function unit(from, to) {
      var dx = from[0] - to[0];
      var dy = from[1] - to[1];
      var length = Math.hypot(dx, dy) || 1;
      return [dx / length, dy / length];
    }
    var inward = unit(previous, current);
    var outward = unit(next, current);
    var start = [current[0] + inward[0] * radius, current[1] + inward[1] * radius];
    var end = [current[0] + outward[0] * radius, current[1] + outward[1] * radius];
    if (this.d) this.line(start[0], start[1]);
    else this.move(start[0], start[1]);
    this.d += "Q" + snap(current[0]) + " " + snap(current[1]) + " " + snap(end[0]) + " " + snap(end[1]);
    this.x = end[0];
    this.y = end[1];
    return this;
  };

  /**
   * Elliptical arc approximated with cubic segments (one per quarter-turn or
   * less), using the classic 4/3*tan(theta/4) control-point length.
   */
  PathBuilder.prototype.ellipseArc = function (cx, cy, rx, ry, from, to) {
    var segments = Math.max(1, Math.ceil(Math.abs(to - from) / (Math.PI / 2)));
    var step = (to - from) / segments;
    var k = (4 / 3) * Math.tan(step / 4);
    var angle = from;
    for (var i = 0; i < segments; i += 1) {
      var next = angle + step;
      var p0 = [cx + rx * Math.cos(angle), cy + ry * Math.sin(angle)];
      var p1 = [cx + rx * Math.cos(next), cy + ry * Math.sin(next)];
      this.curve(
        p0[0] - k * rx * Math.sin(angle), p0[1] + k * ry * Math.cos(angle),
        p1[0] + k * rx * Math.sin(next), p1[1] - k * ry * Math.cos(next),
        p1[0], p1[1]
      );
      angle = next;
    }
    return this;
  };

  PathBuilder.prototype.close = function () {
    return this.d + "Z";
  };

  /* ---------------------------------------------------------------------- */
  /* Smooth closed curves from sampled points                                */
  /* ---------------------------------------------------------------------- */

  /**
   * Catmull-Rom-style closed smoothing: each segment p[i] -> p[i+1] becomes a
   * cubic whose control points lean 1/6 of the way toward the neighbours.
   */
  function smoothClosedPath(points) {
    if (points.length === 0) return "";
    var parts = ["M" + snap(points[0][0]) + " " + snap(points[0][1])];
    var n = points.length;
    for (var i = 0; i < n; i += 1) {
      var prev = points[(i - 1 + n) % n];
      var curr = points[i];
      var next = points[(i + 1) % n];
      var after = points[(i + 2) % n];
      parts.push(
        "C" + snap(curr[0] + (next[0] - prev[0]) / 6) + " " + snap(curr[1] + (next[1] - prev[1]) / 6) + " " +
        snap(next[0] - (after[0] - curr[0]) / 6) + " " + snap(next[1] - (after[1] - curr[1]) / 6) + " " +
        snap(next[0]) + " " + snap(next[1])
      );
    }
    return parts.join("") + "Z";
  }

  /** Sample a polar generator (angle -> point) into a smooth closed path. */
  function sampledShape(generator, samples) {
    if (samples === undefined) samples = 128;
    var points = [];
    for (var i = 0; i < samples; i += 1) {
      points.push(generator((i / samples) * TAU));
    }
    return smoothClosedPath(points);
  }

  /* ---------------------------------------------------------------------- */
  /* Shape constructors                                                      */
  /* ---------------------------------------------------------------------- */

  /** Regular polygon with rounded vertices (triangle, hexagon, ...). */
  function roundedPolygon(radius, sides, cornerRadius, startAngle) {
    if (startAngle === undefined) startAngle = -Math.PI / 2;
    var vertices = [];
    for (var i = 0; i < sides; i += 1) {
      var angle = startAngle + (i / sides) * TAU;
      vertices.push([CENTER + Math.cos(angle) * radius, CENTER + Math.sin(angle) * radius]);
    }
    var path = new PathBuilder();
    for (i = 0; i < sides; i += 1) {
      path.corner(vertices[(i - 1 + sides) % sides], vertices[i], vertices[(i + 1) % sides], cornerRadius);
    }
    return path.close();
  }

  /**
   * Superellipse ("squircle") family. exponent = 2 gives an ellipse; higher
   * values approach a rectangle with soft corners.
   */
  function superellipse(halfWidth, halfHeight, exponent) {
    return sampledShape(function (angle) {
      var cos = Math.cos(angle);
      var sin = Math.sin(angle);
      return [
        CENTER + Math.sign(cos) * Math.pow(Math.abs(cos), 2 / exponent) * halfWidth,
        CENTER + Math.sign(sin) * Math.pow(Math.abs(sin), 2 / exponent) * halfHeight,
      ];
    });
  }

  /** Stadium / pill: two straight runs joined by semicircular caps. */
  function stadium(halfWidth, halfHeight) {
    return new PathBuilder()
      .move(CENTER - halfWidth + halfHeight, CENTER - halfHeight)
      .line(CENTER + halfWidth - halfHeight, CENTER - halfHeight)
      .ellipseArc(CENTER + halfWidth - halfHeight, CENTER, halfHeight, halfHeight, -Math.PI / 2, Math.PI / 2)
      .line(CENTER - halfWidth + halfHeight, CENTER + halfHeight)
      .ellipseArc(CENTER - halfWidth + halfHeight, CENTER, halfHeight, halfHeight, Math.PI / 2, (Math.PI * 3) / 2)
      .close();
  }

  /**
   * Wobbly circle: base radius perturbed by a couple of low harmonics.
   */
  function wobblyCircle(radius, harmonics, ySquash) {
    if (ySquash === undefined) ySquash = 1;
    return sampledShape(function (angle) {
      var r = radius;
      for (var i = 0; i < harmonics.length; i += 1) {
        var h = harmonics[i];
        r += radius * h.weight * Math.sin(angle * h.frequency + h.phase);
      }
      return [CENTER + Math.cos(angle) * r, CENTER + Math.sin(angle) * r * ySquash];
    });
  }

  /**
   * Cloud silhouette from a circle envelope: for each direction, cast a ray
   * from the center and take the farthest intersection with any constituent
   * circle.
   */
  function circleEnvelope(circles, samples) {
    if (samples === undefined) samples = 160;
    return sampledShape(function (angle) {
      var cos = Math.cos(angle);
      var sin = Math.sin(angle);
      var radius = 0;
      for (var i = 0; i < circles.length; i += 1) {
        var x = circles[i][0];
        var y = circles[i][1];
        var r = circles[i][2];
        var dx = x - CENTER;
        var dy = y - CENTER;
        var projection = cos * dx + sin * dy;
        var discriminant = projection * projection - (dx * dx + dy * dy) + r * r;
        if (discriminant <= 0) continue;
        radius = Math.max(radius, projection + Math.sqrt(discriminant));
      }
      return [CENTER + cos * radius, CENTER + sin * radius];
    }, samples);
  }

  /**
   * Teardrop: a circle of radius `width` centered on the vertical axis at
   * `bottom`, joined by tangent lines to a rounded tip at `top`.
   */
  function teardrop(width, top, bottom, tipRadius) {
    var ratio = clamp(width / (bottom - top), -1, 1);
    var height = Math.sqrt(1 - ratio * ratio);
    var right = [CENTER + width * height, bottom - width * ratio];
    var left = [CENTER - width * height, bottom - width * ratio];
    var tipAngle = Math.atan2(right[1] - bottom, right[0] - CENTER);
    return new PathBuilder()
      .corner(right, [CENTER, top], left, tipRadius)
      .line(left[0], left[1])
      .ellipseArc(CENTER, bottom, width, width, Math.PI - tipAngle, tipAngle)
      .close();
  }

  /* ---------------------------------------------------------------------- */
  /* Path sampling + viewbox normalization                                   */
  /* ---------------------------------------------------------------------- */

  var PATH_TOKEN = /[MLCQZmlcqz]|-?\d*\.?\d+(?:e[-+]?\d+)?/gi;

  /**
   * Flatten a path (absolute M/L/Q/C/Z) into polyline samples. Curves are
   * subdivided by control-polygon length, ~4 units per step.
   */
  function samplePath(d) {
    var tokens = d.match(PATH_TOKEN) || [];
    var samples = [];
    var i = 0;
    var command = "";
    var x = 0;
    var y = 0;
    var startX = 0;
    var startY = 0;
    function num() {
      return Number(tokens[i++]);
    }
    function pushLine(toX, toY) {
      var length = Math.hypot(toX - x, toY - y);
      var steps = Math.max(2, Math.ceil(length / 4));
      for (var s = 1; s <= steps; s += 1) {
        samples.push([x + ((toX - x) * s) / steps, y + ((toY - y) * s) / steps]);
      }
      x = toX;
      y = toY;
    }
    while (i < tokens.length) {
      var token = tokens[i];
      if (/^[a-z]$/i.test(token)) {
        command = token.toUpperCase();
        i += 1;
      }
      if (command === "Z") {
        pushLine(startX, startY);
      } else if (command === "M") {
        x = num();
        y = num();
        startX = x;
        startY = y;
        samples.push([x, y]);
        command = "L";
      } else if (command === "L") {
        pushLine(num(), num());
      } else if (command === "Q") {
        var cx = num();
        var cy = num();
        var ex = num();
        var ey = num();
        var fromX = x;
        var fromY = y;
        var qSteps = Math.max(2, Math.ceil((Math.hypot(cx - x, cy - y) + Math.hypot(ex - cx, ey - cy)) / 4));
        for (var qs = 1; qs <= qSteps; qs += 1) {
          var qt = qs / qSteps;
          var qu = 1 - qt;
          samples.push([qu * qu * fromX + 2 * qu * qt * cx + qt * qt * ex, qu * qu * fromY + 2 * qu * qt * cy + qt * qt * ey]);
        }
        x = ex;
        y = ey;
      } else if (command === "C") {
        var c1x = num();
        var c1y = num();
        var c2x = num();
        var c2y = num();
        var cex = num();
        var cey = num();
        var cFromX = x;
        var cFromY = y;
        var cSteps = Math.max(
          2,
          Math.ceil((Math.hypot(c1x - x, c1y - y) + Math.hypot(c2x - c1x, c2y - c1y) + Math.hypot(cex - c2x, cey - c2y)) / 4)
        );
        for (var cs = 1; cs <= cSteps; cs += 1) {
          var ct = cs / cSteps;
          var cu = 1 - ct;
          samples.push([
            Math.pow(cu, 3) * cFromX + 3 * cu * cu * ct * c1x + 3 * cu * ct * ct * c2x + Math.pow(ct, 3) * cex,
            Math.pow(cu, 3) * cFromY + 3 * cu * cu * ct * c1y + 3 * cu * ct * ct * c2y + Math.pow(ct, 3) * cey,
          ]);
        }
        x = cex;
        y = cey;
      } else {
        i += 1;
      }
    }
    return samples;
  }

  /**
   * Recenter + uniformly scale a path so its sampled bounding box is centered
   * in the viewbox and its largest dimension matches `diameter`.
   */
  function fitToViewBox(d, options) {
    options = options || {};
    var size = options.size !== undefined ? options.size : VIEW_BOX_SIZE;
    var diameter = options.diameter !== undefined ? options.diameter : TARGET_DIAMETER;
    var minScale = options.minScale !== undefined ? options.minScale : 0.85;
    var maxScale = options.maxScale !== undefined ? options.maxScale : 1.4;
    var center = size / 2;
    var samples = samplePath(d);
    if (samples.length === 0) return d;
    var minX = Infinity;
    var maxX = -Infinity;
    var minY = Infinity;
    var maxY = -Infinity;
    for (var i = 0; i < samples.length; i += 1) {
      var px = samples[i][0];
      var py = samples[i][1];
      if (px < minX) minX = px;
      if (px > maxX) maxX = px;
      if (py < minY) minY = py;
      if (py > maxY) maxY = py;
    }
    var offsetX = center - (minX + maxX) / 2;
    var offsetY = center - (minY + maxY) / 2;
    var scale = clamp(diameter / Math.max(maxX - minX, maxY - minY), minScale, maxScale);
    if (Math.abs(scale - 1) < 0.005 && Math.abs(offsetX) < 0.5 && Math.abs(offsetY) < 0.5) return d;
    var index = 0;
    return d.replace(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi, function (raw) {
      var coordinate = Number(raw) + (index % 2 === 0 ? offsetX : offsetY);
      index += 1;
      return String(snap(center + (coordinate - center) * scale));
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Shape registry                                                          */
  /* ---------------------------------------------------------------------- */

  var GENERATORS = {
    blob: function () {
      return wobblyCircle(104, [
        { frequency: 2, phase: 1.1, weight: 0.045 },
        { frequency: 3, phase: -1.1, weight: 0.03 },
      ]);
    },
    pebble: function () {
      return wobblyCircle(
        102,
        [
          { frequency: 2, phase: 1.1, weight: 0.05 },
          { frequency: 3, phase: -1.1, weight: 0.035 },
        ],
        0.94
      );
    },
    squircle: function () {
      return superellipse(100, 100, 4.2);
    },
    tablet: function () {
      return stadium(106, 68);
    },
    wedge: function () {
      return roundedPolygon(122, 3, 56);
    },
    hex: function () {
      return roundedPolygon(108, 6, 18, Math.PI / 6);
    },
    cloud: function () {
      return circleEnvelope([
        [CENTER - 58, CENTER + 24, 52],
        [CENTER + 58, CENTER + 24, 50],
        [CENTER, CENTER + 32, 58],
        [CENTER - 22, CENTER - 28, 58],
        [CENTER + 36, CENTER - 24, 50],
      ]);
    },
    teardrop: function () {
      return teardrop(82, CENTER - 106, CENTER + 24, 16);
    },
  };

  var pathCache = {};

  /** Add (or replace) a shape generator. Cached paths are invalidated. */
  function registerShape(name, generator) {
    GENERATORS[name] = generator;
    delete pathCache[name];
  }

  function listShapes() {
    return Object.keys(GENERATORS);
  }

  /** Normalized path data for a registered shape; falls back to "blob". */
  function shapePath(name) {
    var key = GENERATORS[name] ? name : "blob";
    var cached = pathCache[key];
    if (cached == null) {
      cached = fitToViewBox(GENERATORS[key]());
      pathCache[key] = cached;
    }
    return cached;
  }

  /* ---------------------------------------------------------------------- */
  /* resolveIdentity                                                         */
  /* ---------------------------------------------------------------------- */

  /**
   * Deterministic id -> { color, shape }. Same id, same avatar, forever.
   * Explicit overrides win when they name a registered color/shape.
   */
  function resolveIdentity(id, overrides) {
    overrides = overrides || {};
    var seed = hashString(id);
    var colorNames = Object.keys(PALETTE);
    var shapeNames = listShapes();
    var hashedColor = colorNames[Math.floor(mulberry32((seed ^ 0x9e3779b9) >>> 0)() * colorNames.length)] || "slate";
    var hashedShape = shapeNames[mix32(seed) % shapeNames.length] || "blob";
    return {
      color: overrides.color != null && PALETTE[overrides.color] != null ? overrides.color : hashedColor,
      shape: overrides.shape != null && GENERATORS[overrides.shape] != null ? overrides.shape : hashedShape,
    };
  }

  /* ---------------------------------------------------------------------- */
  /* Motion-state table (user-extensible)                                    */
  /* ---------------------------------------------------------------------- */

  /**
   * Ambient motion per state. Values copied exactly from motion.py.
   * Unknown states fall back to `idle` at render time.
   */
  var MOTION = {
    idle: { amplitude: 1.5, periodMs: 9000, tiltDeg: 0, eyeScale: 1 },
    sleeping: { amplitude: 0, periodMs: 6000, tiltDeg: 0, eyeScale: 0.12 },
    waking: { amplitude: 2, periodMs: 800, tiltDeg: 0, eyeScale: 0.35 },
    drowsy: { amplitude: 0.5, periodMs: 4000, tiltDeg: 0, eyeScale: 0.25 },
    listening: { amplitude: 1.8, periodMs: 2800, tiltDeg: -2, eyeScale: 1 },
    thinking: { amplitude: 1, periodMs: 2000, tiltDeg: 3, eyeScale: 0.75 },
    working: { amplitude: 2, periodMs: 1800, tiltDeg: -3, eyeScale: 1 },
    searching: { amplitude: 2, periodMs: 1000, tiltDeg: -4, eyeScale: 0.9 },
    loading: { amplitude: 2, periodMs: 6000, tiltDeg: 3, eyeScale: 0.9 },
    excited: { amplitude: 5, periodMs: 1100, tiltDeg: 0, eyeScale: 1.08 },
    happy: { amplitude: 3, periodMs: 2500, tiltDeg: 0, eyeScale: 1.08 },
    celebrate: { amplitude: 7, periodMs: 1400, tiltDeg: 0, eyeScale: 1.12 },
    laughing: { amplitude: 4, periodMs: 1200, tiltDeg: 0, eyeScale: 0.8 },
    playful: { amplitude: 4, periodMs: 1500, tiltDeg: 8, eyeScale: 1.05 },
    surprised: { amplitude: 3, periodMs: 2500, tiltDeg: 0, eyeScale: 1.18 },
    curious: { amplitude: 2, periodMs: 1800, tiltDeg: 6, eyeScale: 1 },
    confused: { amplitude: 1, periodMs: 2200, tiltDeg: -5, eyeScale: 0.8 },
    suspicious: { amplitude: 1, periodMs: 2600, tiltDeg: 7, eyeScale: 0.75 },
    angry: { amplitude: 1, periodMs: 2200, tiltDeg: -7, eyeScale: 0.65 },
    sad: { amplitude: 1, periodMs: 4000, tiltDeg: -4, eyeScale: 0.6 },
    scared: { amplitude: 3, periodMs: 900, tiltDeg: 0, eyeScale: 1.1 },
    proud: { amplitude: 2, periodMs: 3500, tiltDeg: 4, eyeScale: 1 },
    shy: { amplitude: 1, periodMs: 3000, tiltDeg: -8, eyeScale: 0.55 },
    bored: { amplitude: 0.4, periodMs: 3500, tiltDeg: -8, eyeScale: 0.45 },
    writing: { amplitude: 2, periodMs: 4000, tiltDeg: -4, eyeScale: 1 },
    sending: { amplitude: 2, periodMs: 4000, tiltDeg: 0, eyeScale: 1 },
    receiving: { amplitude: 2, periodMs: 4000, tiltDeg: 0, eyeScale: 1 },
    notifying: { amplitude: 3, periodMs: 1500, tiltDeg: 0, eyeScale: 1.1 },
    orbit: { amplitude: 2, periodMs: 4000, tiltDeg: 12, eyeScale: 1 },
    spawning: { amplitude: 5, periodMs: 1200, tiltDeg: 0, eyeScale: 1 },
    // States present in the original implementation's table, ported later.
    radar: { amplitude: 2, periodMs: 4000, tiltDeg: -12, eyeScale: 1 },
    progress: { amplitude: 2, periodMs: 4000, tiltDeg: 0, eyeScale: 1 },
    humming: { amplitude: 1.5, periodMs: 5000, tiltDeg: 0, eyeScale: 0.9 },
    dictating: { amplitude: 2, periodMs: 4000, tiltDeg: 0, eyeScale: 1 },
    uploading: { amplitude: 2, periodMs: 4000, tiltDeg: 0, eyeScale: 1 },
    alerting: { amplitude: 2, periodMs: 2000, tiltDeg: 0, eyeScale: 1.1 },
    dragging: { amplitude: 3, periodMs: 1600, tiltDeg: 5, eyeScale: 1 },
    // An ambient state, distinct from the bounce() one-shot action.
    bouncing: { amplitude: 7, periodMs: 3000, tiltDeg: 0, eyeScale: 1 },
    "powering-down": { amplitude: 0, periodMs: 6000, tiltDeg: 0, eyeScale: 0.12 },
  };

  function motionParams(state) {
    return MOTION[state] || MOTION.idle;
  }

  /** States that render the smile. */
  var SMILING = { happy: true, excited: true, celebrate: true, laughing: true, playful: true };

  /** Duration of the imperative one-shot actions. */
  var ACTION_MS = { spin: 900, bounce: 700, burst: 600 };

  /* ---------------------------------------------------------------------- */
  /* Exports                                                                 */
  /* ---------------------------------------------------------------------- */

  var api = {
    VIEW_BOX_SIZE: VIEW_BOX_SIZE,
    CENTER: CENTER,
    TARGET_DIAMETER: TARGET_DIAMETER,
    hashString: hashString,
    mulberry32: mulberry32,
    PALETTE: PALETTE,
    registerColor: registerColor,
    colorStops: colorStops,
    hexToRgba: hexToRgba,
    PathBuilder: PathBuilder,
    smoothClosedPath: smoothClosedPath,
    sampledShape: sampledShape,
    roundedPolygon: roundedPolygon,
    superellipse: superellipse,
    stadium: stadium,
    wobblyCircle: wobblyCircle,
    circleEnvelope: circleEnvelope,
    teardrop: teardrop,
    samplePath: samplePath,
    fitToViewBox: fitToViewBox,
    registerShape: registerShape,
    listShapes: listShapes,
    shapePath: shapePath,
    resolveIdentity: resolveIdentity,
    MOTION: MOTION,
    motionParams: motionParams,
    SMILING: SMILING,
    ACTION_MS: ACTION_MS,
  };

  var HAS_DOM = typeof document !== "undefined" && typeof window !== "undefined";

  if (HAS_DOM) {
    /* -------------------------------------------------------------------- */
    /* Canvas renderer (mirrors tk.py)                                       */
    /* -------------------------------------------------------------------- */

    var GAZE_X = 4.0; // max eye drift toward pointer, viewbox units
    var GAZE_Y = 3.0;

    function rotatePoint(x, y, rad) {
      var cos = Math.cos(rad);
      var sin = Math.sin(rad);
      return [x * cos - y * sin, x * sin + y * cos];
    }

    /** Quadratic smile curve sampled into polyline points (center-relative). */
    function sampleSmile() {
      var p0 = [-20.0, 24.0];
      var p1 = [0.0, 38.0];
      var p2 = [20.0, 24.0];
      var points = [];
      for (var s = 0; s < 9; s += 1) {
        var t = s / 8;
        var u = 1 - t;
        points.push([
          u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
          u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ]);
      }
      return points;
    }

    function prefersReducedMotion() {
      return (
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches === true
      );
    }

    /**
     * Animated procedural persona avatar.
     *
     * new PersonaAvatar(containerEl, identity, {
     *   state, sizePx, followPointer, paused, color, shape, faceFill,
     *   followTarget, spinSignal, emphasis,
     * })
     *
     * Methods: setState(name), setPaused(bool), setFollowTarget(pointOrNull),
     * setSpinSignal(n), setEmphasis(bool), spin(), bounce(), burst(),
     * destroy().
     */
    function PersonaAvatar(container, identity, options) {
      options = options || {};

      this.identity = identity;
      this.sizePx = options.sizePx !== undefined ? options.sizePx : 64;
      this.faceFill = options.faceFill || "#ffffff";
      this.followPointer = options.followPointer === true;
      this.followTarget = options.followTarget != null ? options.followTarget : null;
      this.spinSignal = options.spinSignal !== undefined ? options.spinSignal : 0;
      this.emphasis = options.emphasis === true;
      this.paused = options.paused === true;
      this.reduced = prefersReducedMotion();

      var resolved = resolveIdentity(identity, { color: options.color, shape: options.shape });
      this.colorName = resolved.color;
      this.shapeName = resolved.shape;
      var stops = colorStops(this.colorName);

      // Base geometry in viewbox units, relative to the face center so each
      // frame only needs rotate -> scale -> translate.
      this._body = samplePath(shapePath(this.shapeName)).map(function (p) {
        return [p[0] - CENTER, p[1] - CENTER];
      });
      this._eyes = [
        [-29.0, -8.0],
        [29.0, -8.0],
      ];
      this._smile = sampleSmile();

      // Real diagonal gradient (top-left stop -> bottom-right stop) over the
      // body's bounding box, in center-relative viewbox coordinates.
      var minX = Infinity;
      var maxX = -Infinity;
      var minY = Infinity;
      var maxY = -Infinity;
      for (var i = 0; i < this._body.length; i += 1) {
        var bx = this._body[i][0];
        var by = this._body[i][1];
        if (bx < minX) minX = bx;
        if (bx > maxX) maxX = bx;
        if (by < minY) minY = by;
        if (by > maxY) maxY = by;
      }

      this.canvas = document.createElement("canvas");
      this.canvas.style.display = "block";
      this.canvas.style.width = this.sizePx + "px";
      this.canvas.style.height = this.sizePx + "px";
      this.canvas.style.userSelect = "none";
      this.canvas.setAttribute("aria-hidden", "true");
      this._dpr = window.devicePixelRatio || 1;
      this.canvas.width = Math.round(this.sizePx * this._dpr);
      this.canvas.height = Math.round(this.sizePx * this._dpr);
      this._ctx = this.canvas.getContext("2d");

      this._gradient = this._ctx.createLinearGradient(minX, minY, maxX, maxY);
      this._gradient.addColorStop(0, stops.from);
      this._gradient.addColorStop(1, stops.to);

      // Halo color for the emphasis drop shadow: the lighter gradient stop
      // at ~0.8 alpha.
      this._glowColor = hexToRgba(stops.from, 0.8);
      if (this.emphasis) this.canvas.setAttribute("data-emphasis", "true");

      this._gaze = { x: 0, y: 0 };
      this._action = null;
      this._actionStart = 0;
      this._raf = 0;
      this._started = 0;

      this._state = "";
      this._motion = motionParams("idle");
      this.setState(options.state !== undefined ? options.state : "idle");

      this._tick = this._tick.bind(this);
      this._onPointerMove = this._onPointerMove.bind(this);
      this._onPointerLeave = this._onPointerLeave.bind(this);

      container.appendChild(this.canvas);

      if (this.followPointer && !this.paused && !this.reduced) {
        window.addEventListener("pointermove", this._onPointerMove, { passive: true });
        document.documentElement.addEventListener("pointerleave", this._onPointerLeave);
      }
      this._applyFollowTarget();

      if (this.paused || this.reduced) {
        this._draw(0, 0, 1);
      } else {
        this._started = performance.now();
        this._raf = requestAnimationFrame(this._tick);
      }
    }

    /* ---------------------------------------------------------------- API */

    PersonaAvatar.prototype.setState = function (state) {
      /** Switch the ambient motion state (unknown names fall back to idle). */
      this._state = state;
      this._motion = motionParams(state);
      if (this.paused || this.reduced) this._draw(0, 0, 1);
    };

    PersonaAvatar.prototype.setPaused = function (paused) {
      if (paused === this.paused) return;
      this.paused = paused;
      if (paused || this.reduced) {
        cancelAnimationFrame(this._raf);
        this._draw(0, 0, 1);
      } else {
        this._started = performance.now();
        this._raf = requestAnimationFrame(this._tick);
      }
    };

    /**
     * Gaze at a fixed viewport point ({x, y} in client coordinates) instead of
     * the pointer; pass null to hand gaze back to the pointer.
     */
    PersonaAvatar.prototype.setFollowTarget = function (pointOrNull) {
      this.followTarget = pointOrNull != null ? pointOrNull : null;
      this._applyFollowTarget();
      if (this.paused || this.reduced) this._draw(0, 0, 1);
    };

    /**
     * Declarative spin trigger: when `n` differs from the stored signal,
     * store it and fire one spin (mirrors bumping a numeric prop).
     */
    PersonaAvatar.prototype.setSpinSignal = function (n) {
      if (n === this.spinSignal) return;
      this.spinSignal = n;
      this._trigger("spin");
    };

    /** Toggle the emphasis halo (body drop shadow + data-emphasis attr). */
    PersonaAvatar.prototype.setEmphasis = function (emphasis) {
      emphasis = emphasis === true;
      if (emphasis === this.emphasis) return;
      this.emphasis = emphasis;
      if (emphasis) this.canvas.setAttribute("data-emphasis", "true");
      else this.canvas.removeAttribute("data-emphasis");
      if (this.paused || this.reduced) this._draw(0, 0, 1);
    };

    /** One full 360-degree rotation (~0.9s). */
    PersonaAvatar.prototype.spin = function () {
      this._trigger("spin");
    };

    /** One decaying vertical hop (~0.7s). */
    PersonaAvatar.prototype.bounce = function () {
      this._trigger("bounce");
    };

    /** One quick scale pulse (~0.6s). */
    PersonaAvatar.prototype.burst = function () {
      this._trigger("burst");
    };

    /** Cancel the rAF loop, remove listeners, remove the canvas. */
    PersonaAvatar.prototype.destroy = function () {
      cancelAnimationFrame(this._raf);
      window.removeEventListener("pointermove", this._onPointerMove);
      document.documentElement.removeEventListener("pointerleave", this._onPointerLeave);
      if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    };

    /* ------------------------------------------------------------ internals */

    PersonaAvatar.prototype._trigger = function (action) {
      this._action = action;
      this._actionStart = performance.now();
    };

    PersonaAvatar.prototype._applyFollowTarget = function () {
      // A fixed target overrides pointer gaze; clearing it recenters the eyes.
      if (this.followTarget == null) {
        this._gaze = { x: 0, y: 0 };
        return;
      }
      var rect = this.canvas.getBoundingClientRect();
      if (rect == null || rect.width === 0 || rect.height === 0) return;
      this._gaze = {
        x: Math.max(-1, Math.min(1, (this.followTarget.x - (rect.left + rect.width / 2)) / (rect.width / 2))),
        y: Math.max(-1, Math.min(1, (this.followTarget.y - (rect.top + rect.height / 2)) / (rect.height / 2))),
      };
    };

    PersonaAvatar.prototype._onPointerMove = function (event) {
      if (this.followTarget != null) return;
      var rect = this.canvas.getBoundingClientRect();
      if (rect == null || rect.width === 0 || rect.height === 0) return;
      this._gaze = {
        x: Math.max(-1, Math.min(1, (event.clientX - (rect.left + rect.width / 2)) / (rect.width / 2))),
        y: Math.max(-1, Math.min(1, (event.clientY - (rect.top + rect.height / 2)) / (rect.height / 2))),
      };
    };

    PersonaAvatar.prototype._onPointerLeave = function () {
      if (this.followTarget != null) return;
      this._gaze = { x: 0, y: 0 };
    };

    PersonaAvatar.prototype._tick = function (now) {
      if (this.paused) return;
      var motion = this._motion;
      var elapsed = now - this._started;
      var phase = (elapsed / motion.periodMs) * Math.PI * 2;
      var bob = Math.sin(phase) * motion.amplitude;
      var tilt = motion.tiltDeg;

      // One-shot actions blend on top of the ambient motion.
      var lift = 0;
      var rotate = 0;
      var scale = 1;
      if (this._action != null) {
        var t = (now - this._actionStart) / ACTION_MS[this._action];
        if (t >= 1) {
          this._action = null;
        } else if (this._action === "bounce") {
          lift = (1 - t) * 8;
        } else if (this._action === "spin") {
          rotate = t * 360;
        } else {
          // burst
          scale = 1 + Math.sin(t * Math.PI) * 0.12;
        }
      }

      this._draw(bob + lift, tilt + rotate, scale);
      this._raf = requestAnimationFrame(this._tick);
    };

    PersonaAvatar.prototype._draw = function (lift, deg, scale) {
      var ctx = this._ctx;
      var zoom = (this.sizePx / VIEW_BOX_SIZE) * scale;
      var mid = this.sizePx / 2;
      var dy = -lift * (this.sizePx / VIEW_BOX_SIZE);
      var rad = (deg * Math.PI) / 180;

      ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
      ctx.clearRect(0, 0, this.sizePx, this.sizePx);

      // Body polygon: rotate -> scale -> translate about the center.
      ctx.save();
      ctx.translate(mid, mid + dy);
      ctx.rotate(rad);
      ctx.scale(zoom, zoom);
      ctx.beginPath();
      for (var i = 0; i < this._body.length; i += 1) {
        var p = this._body[i];
        if (i === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      }
      ctx.closePath();
      if (this.emphasis) {
        ctx.shadowColor = this._glowColor;
        ctx.shadowBlur = this.sizePx * 0.15;
        ctx.shadowOffsetX = 0;
        ctx.shadowOffsetY = 0;
      }
      ctx.fillStyle = this._gradient;
      ctx.fill();

      // Smile polyline (same transform, no gaze).
      if (SMILING[this._state] === true) {
        ctx.beginPath();
        for (var s = 0; s < this._smile.length; s += 1) {
          var sp = this._smile[s];
          if (s === 0) ctx.moveTo(sp[0], sp[1]);
          else ctx.lineTo(sp[0], sp[1]);
        }
        ctx.strokeStyle = this.faceFill;
        ctx.lineWidth = 5;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.stroke();
      }
      ctx.restore();

      // Eyes: centers rotate with the face; the gaze drift (like tk.py) and
      // the ovals themselves stay axis-aligned in screen space.
      var eyeScale = this._motion.eyeScale;
      var eyeRy = (this._state === "sleeping" ? 1.5 : 7) * eyeScale * zoom;
      var eyeRx = 10 * zoom;
      var gazeX = this._gaze.x * GAZE_X;
      var gazeY = this._gaze.y * GAZE_Y;
      ctx.fillStyle = this.faceFill;
      for (var e = 0; e < this._eyes.length; e += 1) {
        var rotated = rotatePoint(this._eyes[e][0], this._eyes[e][1], rad);
        var cx = mid + (rotated[0] + gazeX) * zoom;
        var cy = mid + dy + (rotated[1] + gazeY) * zoom;
        ctx.beginPath();
        ctx.ellipse(cx, cy, eyeRx, Math.max(eyeRy, 0.01), 0, 0, TAU);
        ctx.fill();
      }
    };

    // Expose the pure pieces on the class itself, so window.PersonaAvatar is
    // both the constructor and the namespace (PersonaAvatar.hashString, ...).
    var keys = Object.keys(api);
    for (var k = 0; k < keys.length; k += 1) {
      PersonaAvatar[keys[k]] = api[keys[k]];
    }
    api.PersonaAvatar = PersonaAvatar;
  }

  global.PersonaAvatar = api.PersonaAvatar || api;
})(typeof window !== "undefined" ? window : globalThis);
