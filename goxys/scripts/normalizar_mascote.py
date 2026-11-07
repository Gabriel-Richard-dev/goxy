"""Normaliza os PNG/WebP transparentes do mascote para o mesmo enquadramento.

Cada personagem é recortado pelo canal alfa e recolocado numa tela 1024x1024 com a mesma
altura de corpo (ALTURA) e os pés na mesma linha de base (BASE), centralizado na horizontal.
Assim, trocar de um agente para outro não muda o tamanho aparente.

    goxys/.venv/bin/python goxys/scripts/normalizar_mascote.py [arquivo.png ...]

Sem argumentos, processa todos os .png de assets/goxys (exceto _fonte/ e hero*). Idempotente.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

PASTA = Path(__file__).resolve().parents[2] / "assets" / "goxys"
LADO, ALTURA, LARGURA_MAX, BASE = 1024, 896, 960, 992   # px na tela final (mesmas proporções da de 512)
LADO_SM = 512  # miniatura <nome>@512.webp


def normalizar(png: Path) -> None:
    im = Image.open(png).convert("RGBA")
    # ignora pixels quase transparentes (halo do recorte) ao medir
    alfa = im.getchannel("A").point(lambda a: 255 if a > 24 else 0)
    bbox = alfa.getbbox()
    if not bbox:
        return
    corpo = im.crop(bbox)
    w, h = corpo.size
    escala = min(ALTURA / h, LARGURA_MAX / w)
    corpo = corpo.resize((max(1, round(w * escala)), max(1, round(h * escala))), Image.LANCZOS)
    tela = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    x = (LADO - corpo.width) // 2
    y = BASE - corpo.height
    tela.alpha_composite(corpo, (x, y))
    tela.save(png, optimize=True)  # PNG sem perda
    tela.save(png.with_suffix(".webp"), quality=92, method=6)
    tela.resize((LADO_SM, LADO_SM), Image.LANCZOS).save(png.with_name(f"{png.stem}@512.webp"), quality=92, method=6)
    print(f"{png.name}: corpo {w}x{h} -> {corpo.width}x{corpo.height}")


if __name__ == "__main__":
    alvos = [Path(a) for a in sys.argv[1:]] or [
        p for p in sorted(PASTA.glob("*.png")) if not p.name.startswith("hero")
    ]
    for p in alvos:
        normalizar(p)
