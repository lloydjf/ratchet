"""Generate the Ratchet logo mark: a sawtooth ratchet wheel held by a pawl.

The teeth are asymmetric (a radial face, a short land, a long slope), and the pawl rests
against a radial face, so the wheel can only turn one way. A mask cuts a hairline gap
around the pawl so the two parts read separately at favicon size.

Run: python3 docs/assets/src/make_logo.py
Writes docs/assets/logo.svg (currentColor) and dashboard/public/favicon.svg (on #0E1116).
"""

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def f(v: float) -> str:
    s = f"{v:.1f}"
    s = s[:-2] if s.endswith(".0") else s
    return "0" if s == "-0" else s


def pol(c, r, deg):
    t = math.radians(deg)
    return (c[0] + r * math.cos(t), c[1] + r * math.sin(t))


def poly(pts):
    return "M" + " ".join(f"{f(x)} {f(y)}" for x, y in pts) + "Z"


def circle(c, r):
    x, y = c
    return f"M{f(x + r)} {f(y)}a{f(r)} {f(r)} 0 1 0 {f(-2 * r)} 0a{f(r)} {f(r)} 0 1 0 {f(2 * r)} 0Z"


N = 10  # teeth
R_ROOT, R_TIP, LAND = 17.5, 24.0, 6.0  # root radius, tip radius, tip land (degrees)
TIP_DEG = -78.0  # angle of the radial face the pawl rests against (screen degrees, y down)
W_N, ARM, PR = 0.1, 29.0, 6.0  # pawl: radial weight of its direction, arm length, pivot radius
HUB, FIT = 7.5, 60.0  # hub hole radius, size the mark is fitted to inside the 64 box
FAV_GAP = 5.0  # wider cut around the pawl for the favicon


def geometry(c, k=1.0):
    step = 360 / N
    teeth = []
    for i in range(N):
        a = TIP_DEG + i * step
        teeth += [pol(c, k * R_ROOT, a), pol(c, k * R_TIP, a), pol(c, k * R_TIP, a + LAND)]
    n_out = (math.cos(math.radians(TIP_DEG)), math.sin(math.radians(TIP_DEG)))
    # pawl tip: just inside the gap, a hair counter-clockwise of the radial face
    t = pol(c, k * (R_ROOT + 0.6), TIP_DEG - 2.5)
    e1 = pol(c, k * (R_TIP - 0.2), TIP_DEG - 1.5)  # flush with the radial face
    q = pol(c, k * R_TIP, TIP_DEG - step + LAND)  # tip of the previous tooth (slope top)
    us = ((q[0] - t[0]), (q[1] - t[1]))
    ls = math.hypot(*us)
    us = (us[0] / ls, us[1] / ls)
    d = (W_N * n_out[0] + (1 - W_N) * us[0], W_N * n_out[1] + (1 - W_N) * us[1])
    ld = math.hypot(*d)
    d = (d[0] / ld, d[1] / ld)
    p = (t[0] + k * ARM * d[0], t[1] + k * ARM * d[1])  # pivot
    pr = k * PR
    nx, ny = -d[1], d[0]
    # arm: from the tip wedge to the boss, tangent-ish to the boss
    bx, by = p[0] - d[0] * pr * 0.55, p[1] - d[1] * pr * 0.55  # stay clear of the pivot hole
    s1 = (bx - nx * pr * 0.82, by - ny * pr * 0.82)
    s2 = (bx + nx * pr * 0.82, by + ny * pr * 0.82)
    # choose the side order so the polygon does not self-cross: e1 is on the clockwise side
    side = (e1[0] - t[0]) * nx + (e1[1] - t[1]) * ny
    a_cw, a_ccw = (s2, s1) if side > 0 else (s1, s2)
    arm = [t, e1, a_cw, a_ccw]
    return teeth, arm, p, pr


def bbox(c, k):
    teeth, arm, p, pr = geometry(c, k)
    xs = [x for x, _ in arm] + [p[0] - pr, p[0] + pr, c[0] - k * R_TIP, c[0] + k * R_TIP]
    ys = [y for _, y in arm] + [p[1] - pr, p[1] + pr, c[1] - k * R_TIP, c[1] + k * R_TIP]
    return min(xs), min(ys), max(xs), max(ys)


def build(box=FIT):
    x0, y0, x1, y1 = bbox((0.0, 0.0), 1.0)
    k = min(box / (x1 - x0), box / (y1 - y0))
    x0, y0, x1, y1 = bbox((0.0, 0.0), k)
    c = (32 - (x0 + x1) / 2, 32 - (y0 + y1) / 2)
    teeth, arm, p, pr = geometry(c, k)
    wheel = poly(teeth) + circle(c, k * HUB)
    boss = circle(p, pr) + circle(p, pr * 0.4)
    return wheel, poly(arm), boss, k


def body(gap=2.6):
    wheel, arm, boss, _ = build()
    return (
        f'<mask id="rk"><rect width="64" height="64" fill="#fff"/>'
        f'<path d="{arm}{circle_only(boss)}" stroke="#000" stroke-width="{gap}"'
        ' stroke-linejoin="round"/></mask>'
        f'<path mask="url(#rk)" fill-rule="evenodd" d="{wheel}"/>'
        f'<path d="{arm}"/><path fill-rule="evenodd" d="{boss}"/>'
    )


def circle_only(boss):
    return boss.split("Z")[0] + "Z"


def svg() -> str:
    head = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="currentColor">'
    return f"{head}{body()}</svg>\n"


FAVICON = {"N": 8, "LAND": 8.0, "HUB": 7.0}  # fewer, chunkier teeth survive 32 px


def small_body(gap=FAV_GAP) -> str:
    g = globals()
    saved = {k: g[k] for k in FAVICON}
    g.update(FAVICON)
    try:
        return body(gap)
    finally:
        g.update(saved)


def favicon() -> str:
    inner = small_body()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#0E1116"/>'
        f'<g fill="#79C0FF" transform="translate(3.5 3.5) scale(.890625)">{inner}</g></svg>\n'
    )


if __name__ == "__main__":
    (ROOT / "docs/assets/logo.svg").write_text(svg())
    (ROOT / "dashboard/public/favicon.svg").write_text(favicon())
    print("logo.svg bytes:", len(svg().encode()), "scale:", round(build()[3], 3))
    print("small mark (for the dashboard header, AppShell.tsx):")
    jsx = small_body(4.2)
    for attr, prop in (
        ("fill-rule", "fillRule"),
        ("stroke-width", "strokeWidth"),
        ("stroke-linejoin", "strokeLinejoin"),
    ):
        jsx = jsx.replace(attr, prop)
    print(jsx)
