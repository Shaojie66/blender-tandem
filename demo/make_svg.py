"""Draw demo/run.json as demo/demo.svg, the animation at the top of the README:

    python3 demo/make_svg.py [run.json [demo.svg [freeze_seconds]]]

One strip of cells, a cell per frame, filled from the left by this Mac and from the right by the second
one at the moments run.json says each frame was finished; below it the lines the tool printed at those
moments. Everything shown comes from run.json (see record.py). freeze_seconds stops the loop at that
second, for looking at one state.

A plain SVG with CSS animation: GitHub shows it through an <img>, where no script runs. The card brings
its own background, so it reads the same on a light and on a dark page. Without animation (reduced
motion, or a viewer that drops the style) the finished state is what remains.
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, PAD = 860, 316, 28
LEAD, PLAY, HOLD = 0.6, 10.0, 2.8          # seconds of the loop: before the run, the run itself, the finished state
BG, EDGE, EMPTY, TEXT, MUTED, WARN = "#0d1117", "#30363d", "#21262d", "#c9d1d9", "#8b949e", "#d29922"
COLOR = {"local": ("#58a6ff", "#1d4a7c"), "remote": ("#f0883e", "#70431f")}      # finished, in progress
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
PROGRESS = re.compile(r"\d+/\d+ frames   local")


def fmt(seconds):
    m, s = divmod(int(seconds), 60)             # whole seconds elapsed, as the tool's own "done in" counts them
    return f"{m}m{s:02d}s" if m else f"{s}s"


def main():
    a = sys.argv[1:]
    src = a[0] if a else os.path.join(HERE, "run.json")
    dst = a[1] if len(a) > 1 else os.path.join(HERE, "demo.svg")
    with open(src) as fh:
        run = json.load(fh)
    total, n, loop = run["seconds"], run["frames"], LEAD + PLAY + HOLD
    css, body = [], []

    def at(t):
        """A second of the real run as a place in the loop."""
        return f"{(LEAD + min(max(t, 0), total) / total * PLAY) / loop * 100:.3f}%"

    def text(x, y, s, fill=TEXT, size=12, font=SANS, anchor="start", extra=""):
        return (f'<text x="{x:.1f}" y="{y}" fill="{fill}" font-size="{size}" font-family="{font}" '
                f'text-anchor="{anchor}" xml:space="preserve"{extra}>{html.escape(s)}</text>')

    def switch(name, variants, x, y, **kw):
        """variants [(second, string, colour)]: each shows from its second until the next one's."""
        steps = [(at(t) if i else "0%", s, c) for i, (t, s, c) in enumerate(variants)]
        steps = [v for i, v in enumerate(steps) if i + 1 == len(steps) or steps[i + 1][0] != v[0]]
        for i, (start, s, c) in enumerate(steps):
            last = i + 1 == len(steps)
            if len(steps) > 1:
                frames = ("" if not i else "0%{opacity:0}") + f"{start}{{opacity:1}}" + ("" if last else f"{steps[i + 1][0]}{{opacity:0}}")
                css.append(f"@keyframes {name}{i}{{{frames}}}#{name}{i}{{animation-name:{name}{i}}}")
            body.append(text(x, y, s, c, extra=f' id="{name}{i}" class="a" opacity="{int(last)}"', **kw))

    # the strip
    left, width = PAD, W - 2 * PAD
    pitch = width / n
    for f, who, done, took in run["cells"]:
        full, dim = COLOR[who]
        start = at(done - took)
        mid = "" if start == at(done) else f"{start}{{fill:{dim}}}"
        css.append(f"@keyframes c{f}{{0%{{fill:{EMPTY}}}{mid}{at(done)}{{fill:{full}}}}}#c{f}{{animation-name:c{f}}}")
        body.append(f'<rect id="c{f}" class="a" x="{left + (f - 1) * pitch:.2f}" y="56" width="{max(pitch - 1.2, 1.5):.2f}" '
                    f'height="40" rx="1.5" fill="{full}"/>')
    body.append(text(left, 44, f"main Mac ({run['local']})   frame 1 →", COLOR["local"][0], 13))
    body.append(text(W - PAD, 44, f"← frame {n}   second Mac ({run['remote']})", COLOR["remote"][0], 13, anchor="end"))

    mine = [c for c in run["cells"] if c[1] == "local"]
    theirs = [c for c in run["cells"] if c[1] == "remote"]
    if mine and theirs:
        last = max(c[0] for c in mine)
        edge = left + last * pitch - 0.6
        end = max(c[2] for c in run["cells"])
        # Both can claim the frame where they meet. The tool's last line counts it for each of them,
        # the strip shows it once, so say so.
        counted = re.search(r"local (\d+) frames.*remote (\d+) frames", run["lines"][-1][1])
        both = counted and int(counted.group(1)) + int(counted.group(2)) == n + 1
        where = f"met at frame {last}, which both rendered" if both else f"met after frame {last}"
        css.append(f"@keyframes met{{0%{{opacity:0}}{at(end)}{{opacity:1}}}}#met{{animation-name:met}}")
        body.append(f'<g id="met" class="a"><path d="M{edge:.1f} 100v6" stroke="{MUTED}"/>'
                    + text(edge, 119, where, MUTED, 11, anchor="middle") + "</g>")

    # what each Mac is doing, read from the lines the tool printed
    def when(needle):
        return next((t for t, s in run["lines"] if needle in s), None)
    gave, back, started = when("local gives way"), when("local resumes"), when("remote started")
    finished = max(c[2] for c in run["cells"])
    local = [(0, "rendering", MUTED)]
    if gave is not None and back is not None:
        local += [(gave, "paused: another render started on this Mac", WARN), (back, "rendering again", MUTED)]
    local.append((finished, f"{len(mine)} frames", MUTED))
    switch("l", local, left, 138, size=12)
    remote = [(0, "not needed yet", MUTED)]
    if started is not None and theirs:
        first = min(c[2] - c[3] for c in theirs)
        remote += [(started, "starting", MUTED), (first, "rendering", MUTED)]
    remote.append((finished, f"{len(theirs)} frames", MUTED))
    switch("r", remote, W - PAD, 138, size=12, anchor="end")

    # the terminal
    body.append(f'<rect x="{left}" y="152" width="{width}" height="84" rx="8" fill="#010409" stroke="{EDGE}"/>')
    body.append(text(left + 14, 173, "$ tandem render scene.blend", MUTED, 10.5, MONO))
    room = int((width - 28) / 6.33)             # characters of 10.5px monospace that fit
    def shown(s):
        return s if len(s) <= room else s[:room - 1] + "…"
    events = [(t, shown(s), WARN if "gives way" in s or "waiting" in s else TEXT) for t, s in run["lines"] if not PROGRESS.search(s)]
    progress = [(t, shown(s), TEXT) for t, s in run["lines"] if PROGRESS.search(s)]
    switch("e", events, left + 14, 195, size=10.5, font=MONO)
    switch("p", [(0, "", TEXT)] + progress, left + 14, 217, size=10.5, font=MONO)

    # the clock: a line that fills over the run, with the other render marked on it
    body.append(f'<rect x="{left}" y="256" width="{width}" height="3" rx="1.5" fill="{EMPTY}"/>')
    css.append(f"@keyframes bar{{0%{{transform:scaleX(0)}}{at(0)}{{transform:scaleX(0)}}{at(total)}{{transform:scaleX(1)}}}}"
               "#bar{animation-name:bar;animation-timing-function:linear;transform-box:fill-box;transform-origin:left}")
    body.append(f'<rect id="bar" class="a" x="{left}" y="256" width="{width}" height="3" rx="1.5" fill="{MUTED}" opacity="0.55"/>')
    if len(run.get("other", [])) == 2:
        o0, o1 = (left + t / total * width for t in run["other"])
        css.append(f"@keyframes other{{0%{{opacity:0}}{at(run['other'][0])}{{opacity:1}}}}#other{{animation-name:other}}")
        body.append(f'<g id="other" class="a"><rect x="{o0:.1f}" y="256" width="{o1 - o0:.1f}" height="3" fill="{WARN}"/>'
                    + text((o0 + o1) / 2, 274, "another render on the main Mac", WARN, 11, anchor="middle") + "</g>")
    w, h = run["resolution"]
    body.append(text(left, 296, f"Recorded {run['recorded']}: {n} frames at {w}×{h}, {run['samples']} samples, "
                                f"Blender {run['blender'].replace('Blender ', '')}", MUTED, 11))
    body.append(text(W - PAD, 296, f"{fmt(total)} of real time, shown {total / PLAY:.0f}× faster", MUTED, 11, anchor="end"))

    freeze = f".a{{animation-delay:-{float(a[2])}s;animation-play-state:paused}}" if len(a) > 2 else ""
    style = (f".a{{animation-duration:{loop}s;animation-timing-function:step-end;animation-iteration-count:infinite}}"
             + "".join(css) + freeze + "@media (prefers-reduced-motion:reduce){.a{animation:none}}")
    label = (f"blender-tandem rendering {n} frames on two Macs: the main Mac from frame 1 up, the second Mac from "
             f"frame {n} down, until they meet")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="{html.escape(label)}"><title>{html.escape(label)}</title><style>{style}</style>'
           f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{BG}" stroke="{EDGE}"/>'
           + "".join(body) + "</svg>\n")
    with open(dst, "w") as fh:
        fh.write(svg)
    print(f"[make_svg] {dst}: {len(svg) // 1024} KB, loop {loop:.1f}s")


if __name__ == "__main__":
    main()
