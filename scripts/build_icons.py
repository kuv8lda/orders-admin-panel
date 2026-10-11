"""Собрать css/vendor/fa-icons.css из SVG-файлов Font Awesome Free.

Панель использует только стиль solid и ~26 иконок. Вместо подключения всего Font Awesome
(CSS и шрифты с CDN) скрипт находит в html/js классы `fa-<имя>`, берёт SVG этих иконок
и генерирует маленький CSS: иконка рисуется через mask-image, цвет берётся из `color`.
Разметка `<i class="fa-solid fa-plus"></i>` не меняется.

Использование (нужен архив fontawesome-free-X.Y.Z-desktop или -web, распакованный):

    python scripts/build_icons.py --svg-dir /путь/к/fontawesome-free-7.3.1-desktop/svgs/solid

Лицензия иконок: CC BY 4.0, https://fontawesome.com/license/free
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["admin.html", "dashboard.html", *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "js").glob("*.js"))]
SKIP = {"fa-solid", "fa-regular", "fa-brands"}

HEADER = """/*
 * Иконки: Font Awesome Free {version} by @fontawesome - https://fontawesome.com
 * Лицензия: https://fontawesome.com/license/free (Icons: CC BY 4.0)
 * Copyright 2026 Fonticons, Inc.
 * Файл сгенерирован scripts/build_icons.py, руками не правьте.
 */
.fa-solid {{
  display: inline-block;
  width: 1em;
  height: 1em;
  vertical-align: -0.125em;
  background-color: currentColor;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-position: center;
  -webkit-mask-size: contain;
  mask-size: contain;
}}
"""


def used_icons() -> list[str]:
    names: set[str] = set()
    for rel in SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        names.update(re.findall(r"\bfa-[a-z0-9]+(?:-[a-z0-9]+)*", text))
    return sorted(names - SKIP)


def svg_to_data_uri(svg: str) -> str:
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    svg = svg.replace(' fill="currentColor"', "")
    svg = re.sub(r"\s+", " ", svg).strip()
    return "data:image/svg+xml," + quote(svg, safe="/:=,' ()")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--svg-dir", required=True, type=Path)
    parser.add_argument("--version", default="7.3.1")
    parser.add_argument("--out", type=Path, default=ROOT / "css" / "vendor" / "fa-icons.css")
    args = parser.parse_args()

    missing = []
    rules = []
    for cls in used_icons():
        svg_path = args.svg_dir / f"{cls[3:]}.svg"
        if not svg_path.is_file():
            missing.append(cls)
            continue
        uri = svg_to_data_uri(svg_path.read_text(encoding="utf-8"))
        rules.append(
            f'.fa-solid.{cls} {{ -webkit-mask-image: url("{uri}"); mask-image: url("{uri}"); }}'
        )
    if missing:
        print("Нет SVG для:", ", ".join(missing), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(HEADER.format(version=args.version) + "\n".join(rules) + "\n", encoding="utf-8")
    print(f"{len(rules)} иконок -> {args.out} ({args.out.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
