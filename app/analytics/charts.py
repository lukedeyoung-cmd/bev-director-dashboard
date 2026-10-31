"""Server-rendered SVG charts for the dashboard.

Pure functions producing standalone `<svg>` markup — no external JS library
and no build step, matching the server-rendered template approach. Text and
labels are escaped here, so templates can insert the returned strings with
`| safe`. Colors reference the design-system CSS variables so charts follow
the light/dark theme.
"""

import html
from collections.abc import Callable
from decimal import Decimal

CHART_HEIGHT = 190
CHART_WIDTH = 640
PLOT_PAD_X = 46  # left gutter for y-axis labels
PLOT_PAD_TOP = 12
PLOT_PAD_BOTTOM = 22  # x-axis label strip


def _fmt_compact(value: Decimal) -> str:
    """Compact money for axis labels: $940, $4.2k, $18.5k."""
    v = float(value)
    if abs(v) >= 1000:
        return f"${v / 1000:.1f}k"
    return f"${v:,.0f}"


def line_chart(
    points: list,
    *,
    value_fmt: Callable[[Decimal], str] = _fmt_compact,
) -> str:
    """Daily net-sales line: current period solid, prior period dashed.

    Points are TrendPoint dataclasses (label, current, prior); days with no
    data break the line rather than dipping to zero.
    """
    values = [float(p.current) for p in points if p.current is not None] + [
        float(p.prior) for p in points if p.prior is not None
    ]
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if lo == hi:  # flat series: keep the line visible in the middle
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    lo -= span * 0.08  # headroom so the line doesn't kiss the frame
    hi += span * 0.08

    inner_w = CHART_WIDTH - PLOT_PAD_X - 8
    inner_h = CHART_HEIGHT - PLOT_PAD_TOP - PLOT_PAD_BOTTOM
    n = len(points)

    def _xy(i: int, v: float) -> tuple[float, float]:
        x = PLOT_PAD_X + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)
        y = PLOT_PAD_TOP + inner_h * (1 - (v - lo) / (hi - lo))
        return round(x, 1), round(y, 1)

    def _polyline(attr: str, pick) -> str:
        coords = [
            _xy(i, float(pick(p)))
            for i, p in enumerate(points)
            if pick(p) is not None
        ]
        if len(coords) < 2:
            return ""
        pts = " ".join(f"{x},{y}" for x, y in coords)
        return f'<polyline fill="none" {attr} points="{pts}"/>'

    grid_lines: list[str] = []
    y_labels: list[str] = []
    for t in range(4):  # 4 horizontal gridlines with compact money labels
        v = lo + (hi - lo) * t / 3
        _, y = _xy(0, v)
        grid_lines.append(
            f'<line x1="{PLOT_PAD_X}" y1="{y:.1f}" x2="{CHART_WIDTH - 8}" '
            f'y2="{y:.1f}" stroke="var(--border-subtle)" stroke-width="1"/>'
        )
        y_labels.append(
            f'<text x="{PLOT_PAD_X - 6}" y="{y + 3:.1f}" text-anchor="end" '
            f'class="chart-tick">{html.escape(value_fmt(Decimal(str(round(v)))))}</text>'
        )

    # Area fill under the current series for visual weight.
    area = ""
    coords = [
        _xy(i, float(p.current))
        for i, p in enumerate(points)
        if p.current is not None
    ]
    if len(coords) >= 2:
        pts = " ".join(f"{x},{y}" for x, y in coords)
        baseline = PLOT_PAD_TOP + inner_h
        first_x, last_x = coords[0][0], coords[-1][0]
        area = (
            f'<polygon fill="var(--blue-500)" opacity="0.08" '
            f'points="{first_x},{baseline} {pts} {last_x},{baseline}"/>'
        )

    step = max(1, n // 6)
    x_labels = [
        f'<text x="{_xy(i, lo + (hi - lo) * 0.5)[0]:.1f}" y="{CHART_HEIGHT - 6}" '
        f'text-anchor="middle" class="chart-tick">{html.escape(p.label)}</text>'
        for i, p in enumerate(points)
        if i % step == 0 or i == n - 1
    ]

    current_line = _polyline(
        'stroke="var(--blue-600)" stroke-width="2.5" stroke-linejoin="round"', lambda p: p.current
    )
    prior_line = _polyline(
        'stroke="var(--neutral-400)" stroke-width="1.5" stroke-dasharray="4 4"',
        lambda p: p.prior,
    )
    dots = "".join(
        f'<circle cx="{_xy(i, float(p.current))[0]}" cy="{_xy(i, float(p.current))[1]}" '
        f'r="2.5" fill="var(--blue-600)"/>'
        for i, p in enumerate(points)
        if p.current is not None
    )

    return (
        f'<svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" role="img" '
        f'aria-label="Daily net sales trend" class="chart-svg">'
        f'{"".join(grid_lines)}{"".join(y_labels)}{area}{current_line}{prior_line}{dots}'
        f'{"".join(x_labels)}</svg>'
    )


BUCKET_COLORS = {
    "beer": "var(--blue-600)",
    "wine": "var(--red-600)",
    "spirits": "var(--neutral-700)",
    "na": "var(--green-600)",
    "other": "var(--neutral-400)",
}


def mix_bar(slices: list) -> str:
    """One horizontal stacked bar showing category share of net sales."""
    if not slices:
        return ""
    width = 560
    x = 0.0
    segments: list[str] = []
    for s in slices:
        w = round(width * s.share, 1)
        x2 = x + w
        segments.append(
            f'<rect x="{x:.1f}" y="0" width="{max(w - 0.5, 0.5):.1f}" height="16" '
            f'rx="3" fill="{BUCKET_COLORS.get(s.bucket, "var(--neutral-400)")}" />'
        )
        x = x2
    return (
        f'<svg viewBox="0 0 {width} 16" role="img" '
        f'aria-label="Category mix share" class="chart-svg mix-bar">{"".join(segments)}</svg>'
    )
