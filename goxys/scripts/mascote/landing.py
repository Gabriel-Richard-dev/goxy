"""Peças visuais da landing do Goxy no ComfyUI (só Krea2; MiniMax proibido).

Saída em assets/landing/ (PNG + WebP transparentes, ~1024 px no lado maior) e assets/landing/manifest.json:
  celular.png          smartphone com a TELA transparente (retângulo da tela em % no manifest)
  celular-goxy.png     o mesmo celular com o Goxy espiando por trás (Krea2 Edit, duas referências)
  adesivo-*.png        6 adesivos no estilo estampa Gocase
  fundo-hero.png       padrão esparso de formas (opcional)

Mesmas regras de segurança de gerar.py: um job por vez, checagem de RAM/VRAM e /system_stats < 5 s antes de cada
job, /free a cada 4, tempo limite de 4 min. Idempotente (pula o que já existe em _fonte/).

    goxys/.venv/bin/python goxys/scripts/mascote/landing.py [--so celular,adesivo-copo] [--pos]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerar  # noqa: E402
from gerar import Comfy, ParadaSeguranca, log, recortar, semente  # noqa: E402

ROOT = gerar.ROOT
OUT = ROOT / "assets" / "landing"
FONTE = OUT / "_fonte"
URL = "/assets-goxys/landing"
TIMEOUT = 4 * 60

ESTILO = (
    "Flat 2D cartoon sticker illustration, simple shapes, thick bold black outline, flat solid colors, no gradients, "
    "no realistic shading, no text, no letters, no logos. Use only this palette: hot pink #e61782, blue #2659a5, "
    "cyan #3dbfef, orange #f8ae13, yellow #d7d900, navy #0a1633, plus white and cream. "
)
FUNDO = (
    "Centered, on a completely flat solid pure chroma-key green (#00FF00) background filling the whole image, "
    "no shadow, no floor, no gradient."
)
TELA = (
    "The phone screen is completely filled with a flat solid pure magenta color (#FF00FF), perfectly uniform, "
    "with nothing drawn on it: no icons, no reflections, no glare, no notch content."
)

T2I = {
    "celular": "A modern smartphone seen from the front, slightly tilted, with a dark navy rounded frame and thin bezels. " + TELA,
    "adesivo-copo": "A single cute thermal tumbler cup sticker, pink with a cyan lid.",
    "adesivo-garrafa": "A single cute reusable water bottle sticker, blue with an orange cap.",
    "adesivo-caixa": "A single cute cardboard delivery parcel box sticker, orange with a navy tape strip.",
    "adesivo-lupa": "A single cute magnifying glass sticker, navy handle and a cyan glass lens.",
    "adesivo-balao": "A single cute chat speech bubble sticker, hot pink with three white dots.",
    "adesivo-coracao": "A single cute heart sticker, hot pink with a small white shine.",
    "fundo-hero": "A sparse, airy seamless pattern of small simple flat shapes (dots, circles, tiny stars, squiggles, "
                  "rounded triangles) in pink, blue, cyan, orange and yellow, widely spaced with lots of empty space.",
}

# Krea2 Edit: (origem, referência B ou None, prompt, lora)
EDITS = {
    "celular-goxy": ("celular", "goxy",
                     "Place the cartoon mascot character from image 2 behind the smartphone, peeking out from behind its "
                     "right side, holding the side of the phone with its hand. Keep the exact same mascot design. Keep the "
                     "phone exactly the same, with its screen a flat solid pure magenta (#FF00FF) with nothing drawn on it. "
                     "Same flat pure green (#00FF00) background, no shadow.",
                     "krea2_identity_edit_v2.safetensors"),
}


def grafo_t2i(prompt: str, seed: int, prefixo: str, lado: int = 1024) -> dict:
    """Krea2_Turbo_texto-para-imagem.json (subgraph expandido, sem LoRA de estilo e sem o reescritor de prompt)."""
    return {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["11", 0], "text": prompt}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": lado, "height": lado, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "positive": ["6", 0], "negative": ["13", 0], "latent_image": ["5", 0],
            "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefixo}},
    }


def grafo_edit(img_a: str, img_b: str | None, prompt: str, seed: int, prefixo: str, lora: str) -> dict:
    g = gerar.grafo_krea2(img_a, prompt, seed, prefixo)
    g["71"]["inputs"]["lora_name"] = lora
    if img_b:
        g["90"] = {"class_type": "LoadImage", "inputs": {"image": img_b}}
        g["92"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["90", 0], "vae": ["57", 0]}}
        g["79"]["inputs"].update({"source_latent_b": ["92", 0], "source_image_b": ["90", 0],
                                  "ref_position": "beside (right of target)", "ref_position_a": "aligned (trained)"})
        for no in ("84", "85"):
            g[no]["inputs"]["image_b"] = ["90", 0]
    return g


# ---------------------------------------------------------------- recorte


def tela_magenta(rgb: np.ndarray) -> np.ndarray:
    """Máscara do maior componente magenta (a tela)."""
    from scipy import ndimage as ndi

    f = rgb.astype(np.float32) / 255.0
    mag = np.minimum(f[..., 0], f[..., 2]) - f[..., 1]
    m = (mag > 0.35) & (np.abs(f[..., 0] - f[..., 2]) < 0.35)
    m = ndi.binary_opening(m, iterations=2)
    lab, n = ndi.label(m)
    if not n:
        return np.zeros(m.shape, bool)
    tam = ndi.sum(m, lab, index=np.arange(1, n + 1))
    return ndi.binary_fill_holes(lab == (int(np.argmax(tam)) + 1))


def tela_ok(mask: np.ndarray) -> tuple[bool, float]:
    """Tela retangular e lisa: ocupa >= 92% do próprio retângulo e >= 8% da imagem."""
    if not mask.any():
        return False, 0.0
    ys, xs = np.where(mask)
    caixa = (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)
    preench = mask.sum() / caixa
    return bool(preench >= 0.92 and mask.mean() >= 0.08), float(preench)


def recortar_com_tela(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Chroma key verde + tela magenta transparente (unmix na borda da tela). Devolve (rgba, máscara da tela)."""
    from scipy import ndimage as ndi

    rgba = recortar(rgb).astype(np.float32) / 255.0
    mask = tela_magenta(rgb)
    if mask.any():
        f = rgb.astype(np.float32) / 255.0
        cor = np.median(f[mask], axis=0)
        mbg = max(float(min(cor[0], cor[2]) - cor[1]), 0.05)
        mag = np.minimum(f[..., 0], f[..., 2]) - f[..., 1]
        faixa = ndi.binary_dilation(mask, iterations=3) & ~ndi.binary_erosion(mask, iterations=1)
        a_t = np.clip((1.0 - np.clip(mag / mbg, 0, 1) - 0.1) / 0.8, 0, 1)
        a = rgba[..., 3]
        a_novo = np.where(mask & ~faixa, 0.0, np.where(faixa, np.minimum(a, a_t), a))
        aa = np.maximum(a_novo, 1e-3)[..., None]
        fg = np.where(faixa[..., None], np.clip((f - (1 - aa) * cor) / aa, 0, 1), rgba[..., :3])
        rgba = np.dstack([fg, a_novo])
    return (rgba * 255 + 0.5).astype(np.uint8), mask


def salvar_objeto(nome: str, rgba: np.ndarray, tela: np.ndarray | None = None, lado: int = 1024) -> dict:
    """Recorta pelo alfa (margem 16 px), redimensiona para ~lado no maior eixo, salva PNG+WebP.
    Devolve o retângulo da tela em % da imagem final, se houver."""
    a = rgba[..., 3] > 24
    if tela is not None:
        a |= tela
    ys, xs = np.where(a)
    m = 16
    x0, y0 = max(0, xs.min() - m), max(0, ys.min() - m)
    x1, y1 = min(rgba.shape[1], xs.max() + m + 1), min(rgba.shape[0], ys.max() + m + 1)
    im = Image.fromarray(rgba).crop((x0, y0, x1, y1))
    esc = lado / max(im.size)
    im = im.resize((round(im.width * esc), round(im.height * esc)), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    im.save(OUT / f"{nome}.png", optimize=True)
    im.save(OUT / f"{nome}.webp", quality=88, method=6)
    info = {}
    if tela is not None and tela.any():
        ty, tx = np.where(tela)
        W, H = x1 - x0, y1 - y0
        info = {"x": round((tx.min() - x0) / W * 100, 2), "y": round((ty.min() - y0) / H * 100, 2),
                "w": round((tx.max() - tx.min() + 1) / W * 100, 2), "h": round((ty.max() - ty.min() + 1) / H * 100, 2)}
    return info


def publicar(nome: str) -> None:
    src = FONTE / f"{nome}.png"
    if not src.exists():
        return
    rgb = np.asarray(Image.open(src).convert("RGB"))
    telas = json.loads((OUT / "telas.json").read_text()) if (OUT / "telas.json").exists() else {}
    if nome.startswith("celular"):
        rgba, mask = recortar_com_tela(rgb)
        telas[nome] = salvar_objeto(nome, rgba, mask)
        (OUT / "telas.json").write_text(json.dumps(telas, indent=2))
    else:
        salvar_objeto(nome, recortar(rgb))
    escrever_manifest()


def escrever_manifest() -> None:
    telas = json.loads((OUT / "telas.json").read_text()) if (OUT / "telas.json").exists() else {}

    def url(n: str) -> str | None:
        return f"{URL}/{n}.webp" if (OUT / f"{n}.webp").exists() else None

    man = {
        "celular": {"img": url("celular"), "png": f"{URL}/celular.png" if (OUT / "celular.png").exists() else None,
                    "tela": telas.get("celular")},
        "celular_goxy": {"img": url("celular-goxy"), "tela": telas.get("celular-goxy")},
        "adesivos": [u for n in T2I if n.startswith("adesivo-") for u in [url(n)] if u],
        "fundo": url("fundo-hero"),
        "gerado_em": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------- geração


def gerar_item(c: Comfy, nome: str) -> None:
    destino = FONTE / f"{nome}.png"
    if destino.exists():
        return
    FONTE.mkdir(parents=True, exist_ok=True)
    tentativas = 3 if nome.startswith("celular") else 2
    for t in range(1, tentativas + 1):
        rot = f"landing {nome} (tentativa {t})"
        prefixo = f"goxys_landing/{nome}_t{t}"
        if nome in T2I:
            grafo = grafo_t2i(ESTILO + T2I[nome] + " " + FUNDO + (" " + gerar.FUNDO_FORTE if t >= 2 else ""),
                              semente(nome, t), prefixo)
        else:
            origem, ref_b, prompt, lora = EDITS[nome]
            if not (FONTE / f"{origem}.png").exists():
                log(f"{nome}: falta {origem}.png; pulando")
                return
            a = c.upload(FONTE / f"{origem}.png", f"goxys_landing_{origem}.png")
            b = c.upload(gerar.FONTE / "base.png", "goxys_base_verde.png") if ref_b else None
            grafo = grafo_edit(a, b, prompt, semente(nome, t), prefixo, lora)
        c.esperar_fila_vazia()
        prontas = c.saidas_anteriores(prefixo, "29")
        try:
            saidas = {"29": {"images": prontas}} if prontas else c.executar(grafo, TIMEOUT, rot, liberar=False)
        except ParadaSeguranca:
            raise
        except (TimeoutError, RuntimeError) as e:
            log(f"{rot}: falhou ({e})")
            continue
        bruto = FONTE / f"{nome}_t{t}.png"
        c.baixar(saidas["29"]["images"][0], bruto)
        verde = gerar.fundo_verde_ok(bruto)
        ok = verde >= 0.9
        extra = ""
        if nome.startswith("celular"):
            tok, preench = tela_ok(tela_magenta(np.asarray(Image.open(bruto).convert("RGB"))))
            ok = ok and tok
            extra = f", tela retangular {preench:.0%}"
        log(f"{rot}: borda verde {verde:.0%}{extra} -> {bruto.name}")
        if ok or t == tentativas:
            shutil.copyfile(bruto, destino)
            publicar(nome)
            return


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--so", default="")
    ap.add_argument("--pos", action="store_true", help="só refaz recortes e manifest (sem GPU)")
    args = ap.parse_args()
    ordem = ["celular"] + [n for n in T2I if n.startswith("adesivo-")] + ["celular-goxy", "fundo-hero"]
    so = {s for s in args.so.split(",") if s}
    alvos = [n for n in ordem if not so or n in so]
    if args.pos:
        for n in alvos:
            publicar(n)
        return
    c = Comfy()
    if not c.disponivel():
        log("landing: ComfyUI indisponível")
        sys.exit(2)
    try:
        for n in alvos:
            gerar_item(c, n)
    except ParadaSeguranca as e:
        log(f"landing: PARADA DE SEGURANÇA: {e}")
        c.liberar()
        sys.exit(3)
    c.liberar()
    log("landing: /free final")


if __name__ == "__main__":
    main()
