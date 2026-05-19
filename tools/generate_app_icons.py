from pathlib import Path

from PIL import Image, ImageDraw


def _quad_bezier_points(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], samples: int = 64) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in range(samples + 1):
        t = i / samples
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        points.append((x, y))
    return points


def draw_mozu_icon(size: int = 1024) -> Image.Image:
    scale = size / 256

    def s(v: float) -> float:
        return v * scale

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Outer rounded square.
    draw.rounded_rectangle(
        [s(32), s(32), s(224), s(224)],
        radius=s(32),
        fill=(255, 255, 255, 255),
        outline=(34, 34, 34, 255),
        width=max(1, round(s(12))),
    )

    # Brush stroke represented as a quadratic bezier curve.
    points = _quad_bezier_points((s(80), s(176)), (s(128), s(80)), (s(176), s(176)))
    draw.line(points, fill=(34, 34, 34, 255), width=max(1, round(s(14))), joint="curve")

    # Brush tip dot.
    r = s(10)
    cx, cy = s(128), s(80)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(34, 34, 34, 255))

    # Foundation block.
    draw.rounded_rectangle(
        [s(88), s(192), s(168), s(208)],
        radius=s(4),
        fill=(34, 34, 34, 255),
    )

    return image


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    assets_dir = project_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    icon = draw_mozu_icon(1024)
    icon.save(assets_dir / "icon.png")
    icon.save(assets_dir / "icon_android.png")

    print(f"Generated icons in: {assets_dir}")


if __name__ == "__main__":
    main()
