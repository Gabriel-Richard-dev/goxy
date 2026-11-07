"""Gera os assets animados do mascote Goxy no ComfyUI remoto (SPEC §17, CONTRACTS §7).

Etapas (cada uma idempotente: pula o que já existe em disco):
  imagens  -> Krea2 Edit (identidade preservada) sobre fundo verde #00FF00:
              8 agentes + 5 poses de estado + base   -> assets/goxys/_fonte/<nome>.png
  videos   -> MiniMax H3 imagem->vídeo (nós locais, first=last frame p/ loop):
              1 clipe por estado (+ idle por agente com --idle)
              -> assets/goxys/_fonte/video-<nome>/f_###.png (quadros verdes)
  pos      -> chroma key + despill (numpy), webm VP9 com alfa, pôsteres, estáticos, manifest.json
  validar  -> decodifica um quadro de cada webm com libvpx-vp9 e compõe sobre fundo escuro/claro

Regras de GPU (SPEC §17): um job por vez, fila conferida vazia antes de enfileirar,
POST /free entre jobs, tempo limite por job com POST /interrupt.

Uso:
  goxys/.venv/bin/python goxys/scripts/mascote/gerar.py tudo
  goxys/.venv/bin/python goxys/scripts/mascote/gerar.py imagens --so base,fraude
  goxys/.venv/bin/python goxys/scripts/mascote/gerar.py imagens --refazer fraude --tentativa 2
  goxys/.venv/bin/python goxys/scripts/mascote/gerar.py videos --so fraude
  goxys/.venv/bin/python goxys/scripts/mascote/gerar.py pos
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

COMFY = os.environ.get("COMFY_URL", "http://100.80.129.22:8188")
ROOT = Path(__file__).resolve().parents[3]  # gohacks/
BASE_IMG = ROOT / "assets" / "goxy.png"
OUT = ROOT / "assets" / "goxys"
FONTE = OUT / "_fonte"
LOG = Path(__file__).resolve().parent / "gerar.log"
URL_PREFIX = "/assets-goxys/goxys"

TIMEOUT_IMG = 4 * 60
TIMEOUT_VID = 10 * 60

# ---------------------------------------------------------------- descrição do personagem

IDENTIDADE = (
    "Keep the exact same cartoon mascot character: a cream-colored cloud-shaped creature with a thick "
    "black outline, big black eyes, a coral cheek on the left and a turquoise cheek on the right, a small "
    "smiling mouth, one turquoise arm and one cream arm, coral pants, one turquoise shoe and one coral shoe, "
    "a blue and turquoise letter 'g' on the chest. Same flat illustration style, same line weight, same "
    "colors and proportions."
)
FUNDO = (
    "Replace the background with a completely flat, uniform, solid pure chroma-key green (#00FF00) "
    "background filling the whole image. No shadow, no floor, no gradient, no texture, no vignette."
)
FUNDO_FORTE = (
    "IMPORTANT: the entire background must be one single flat pure bright green color #00FF00 "
    "(RGB 0,255,0), like a green screen, edge to edge, with no cast shadow under the character."
)

AGENTES = {
    "maestro": "The character holds a thin white conductor's baton raised in its cream hand, as if conducting an orchestra.",
    "porteiro": "The character holds a small clipboard and wears a small name badge on a lanyard around its body.",
    "rastreador": "The character carries a small brown cardboard parcel box under one arm and holds a small folded map.",
    "detetive": "The character holds a magnifying glass and wears a small brown detective deerstalker hat on top of its head.",
    "executor": "The character holds a big silver wrench (spanner) proudly in its cream hand.",
    "voz": "The character wears a small black headset with a microphone, and a small white speech bubble floats next to it.",
    "mensageiro": "The character raises a white letter envelope with its cream hand, holding the envelope in front of its hand: fingers wrapped over the envelope edge, the whole envelope in the foreground, clear overlap order, the envelope does not pass through the hand or arm. A small golden bell in its teal hand.",
    "analista": "The character wears round black glasses and holds a small card with a colorful bar chart.",
}

ESTADOS = {
    "investigando": "The character holds a magnifying glass in front of one eye with its cream hand, and holds a small paper order receipt in its teal hand, examining the receipt with a curious focused expression. Only one magnifying glass and one receipt.",
    "fraude": "The character stands still and holds up a small photograph as evidence with its cream hand, with a serious frowning expression and furrowed brows.",
    "esperando": "The character waits patiently, turning its head and eyes to look to the right side (toward a supervisor) with a calm patient expression, holding a small orange hourglass in its cream hand, its teal arm relaxed at its side.",
    "resolvido": "The character gives one thumbs up with its cream hand, while its teal arm hangs relaxed at its side, smiling widely with happily closed eyes, celebrating.",
    "humano": "The character hands over a blue folder, stretching its cream arm straight out to the side holding the folder firmly, while waving goodbye with its raised teal hand.",
}

# 3ª variação (reforço), com ref_boost menor para a pose mudar mais e lados descritos na imagem
REFORCO = {
    "resolvido": "In this image the character's teal arm is on the left side and hangs down relaxed. Its cream arm on the right "
                 "side is raised, giving one thumbs up. Both eyes are closed in happy upward curves with a wide open smile.",
    "esperando": "The character's head and both eyes turn to look toward the right edge of the image with a calm patient "
                 "expression. Its cream hand on the right side holds a small orange hourglass. Its teal arm on the left side hangs relaxed.",
    "investigando": "The character's cream hand on the right side holds one magnifying glass up in front of its right eye. Its teal "
                    "hand on the left side holds one small white paper receipt. Curious focused expression.",
    "mensageiro": "The character has exactly two arms. Its cream arm on the right side of the image raises a white letter "
                  "envelope: the fingers wrap over the front edge of the envelope, the whole envelope is in front of the hand, "
                  "clear overlap order. Its teal arm on the left side hangs down, holding a small golden bell. No third arm.",
    "humano": "The character's cream arm on the right side is stretched straight out horizontally to the right, holding a blue "
              "folder at the end of the arm. Its teal hand on the left side is raised, waving goodbye.",
}

# quadros-chave extras por estado (editados a partir da pose k1 = <estado>.png, mesma cena)
QUADROS_CHAVE = {
    "investigando": [
        "Tilt the same single magnifying glass slightly and hold it a little lower, closer to the receipt, with the eyes looking down at the receipt. Only one magnifying glass.",
        "Keep holding the magnifying glass and the receipt, with the eyes looking to the right and eyebrows raised in curiosity.",
    ],
    "fraude": [
        "Raise the photograph a little higher, with the mouth open in an 'aha!' expression, still serious.",
        "Narrow the eyes and tilt the photograph slightly toward the viewer, with a determined serious expression.",
    ],
    "esperando": [
        "Glance the eyes to the other side for a moment, keeping the same calm waiting pose and the hourglass.",
        "Close the eyes calmly with a small patient smile, one foot slightly raised as if tapping.",
    ],
    "resolvido": [
        "Make a small happy hop with the body slightly lifted and a big open-mouth smile, keeping the same thumbs up.",
        "Wink with one eye while keeping the thumbs up and the wide smile.",
    ],
    "humano": [
        "Keep holding the blue folder out, and tilt the waving teal hand to the other side mid-wave.",
        "Turn the body slightly to the side as if starting to walk away, still holding the folder out and waving goodbye.",
    ],
}
ANATOMIA = (
    "Exactly two arms, one teal arm and one cream arm, same character as the reference, no extra hands, "
    "no extra arms, no duplicated objects."
)
MANTER_CENA = (
    "Keep everything else exactly the same: same character design, same objects, same position and size in "
    "the frame, same line style and colors, same flat solid pure green (#00FF00) background with no shadow. "
    + ANATOMIA
)

# prompts de movimento para o MiniMax (câmera fixa, fundo verde constante, laço)
MOV_ESTADOS = {
    "investigando": "The mascot moves the magnifying glass slowly left and right over the receipt, searching, its eyes following the glass, then returns to the starting position.",
    "fraude": "The mascot raises the photograph a little higher with a firm serious nod, holds it, then lowers it back to the starting position.",
    "esperando": "The mascot waits, gently bobbing up and down and tapping one foot, glancing to the side and back, then returns to the starting position.",
    "resolvido": "The mascot bounces happily with its thumbs up, gives a small cheerful hop, then settles back into the starting position.",
    "humano": "The mascot extends the folder forward as if handing it over, waves goodbye, then returns to the starting position.",
}
MOV_IDLE = "The mascot breathes gently and sways slightly in place, blinking once, then returns to the starting position."
VIDEO_PROMPT = (
    "Flat 2D cartoon animation of the cream cloud-shaped mascot with thick black outline from <Picture 1>. "
    "Static locked-off camera, no zoom, no pan, no cuts. The background stays a flat solid pure chroma-key "
    "green (#00FF00) the whole time, no shadows, no other objects appear. The character keeps exactly the "
    "same design, colors and line style. {mov} Short, smooth, subtle motion that loops seamlessly: the last "
    "frame matches the first frame. Audio: silence."
)

# ---------------------------------------------------------------- utilidades


def log(msg: str) -> None:
    linha = f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(linha, flush=True)
    with LOG.open("a") as f:
        f.write(linha + "\n")


def semente(nome: str, tentativa: int) -> int:
    return int(hashlib.sha1(f"{nome}:{tentativa}".encode()).hexdigest()[:12], 16)


class ParadaSeguranca(RuntimeError):
    """A máquina do ComfyUI dá sinais de aperto: parar tudo e reportar (não insistir)."""


RAM_LIVRE_MIN_GB = 12
VRAM_LIVRE_MIN_GB = 4
FREE_A_CADA = 4


class Comfy:
    def __init__(self, base: str = COMFY):
        self.base = base.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=60)
        self.cid = str(uuid.uuid4())
        self.jobs = 0

    def memoria(self) -> tuple[float, float]:
        """(ram_free, vram_free) em GB. Resposta lenta (> 5 s) ou falha = parada de segurança."""
        t0 = time.time()
        try:
            st = self.http.get("/system_stats", timeout=5).json()
        except (httpx.HTTPError, ValueError) as e:
            raise ParadaSeguranca(f"/system_stats não respondeu em 5 s ({type(e).__name__})") from e
        if time.time() - t0 > 5:
            raise ParadaSeguranca(f"/system_stats demorou {time.time() - t0:.1f}s")
        return st["system"]["ram_free"] / 1e9, st["devices"][0]["vram_free"] / 1e9

    def checar_saude(self) -> None:
        """Antes de cada job: memória mínima; senão /free, 20 s, nova checagem; se seguir baixa, para."""
        ram, vram = self.memoria()
        if ram < RAM_LIVRE_MIN_GB or vram < VRAM_LIVRE_MIN_GB:
            log(f"memória baixa (RAM {ram:.1f} GB, VRAM {vram:.1f} GB): /free e espera 20 s")
            self.liberar()
            time.sleep(20)
            ram, vram = self.memoria()
            if ram < RAM_LIVRE_MIN_GB or vram < VRAM_LIVRE_MIN_GB:
                raise ParadaSeguranca(f"memória continua baixa após /free (RAM {ram:.1f} GB, VRAM {vram:.1f} GB)")

    def disponivel(self) -> bool:
        try:
            return self.http.get("/system_stats", timeout=10).status_code == 200
        except httpx.HTTPError:
            return False

    def esperar_fila_vazia(self, maximo: float = 30 * 60) -> None:
        t0 = time.time()
        avisou = False
        while True:
            try:
                q = self.http.get("/queue", timeout=90).json()
            except (httpx.HTTPError, ValueError):
                time.sleep(15)
                continue
            if not q.get("queue_running") and not q.get("queue_pending"):
                return
            if not avisou:
                log("fila do ComfyUI ocupada (dono usando?); aguardando...")
                avisou = True
            if time.time() - t0 > maximo:
                raise RuntimeError("fila do ComfyUI não esvaziou a tempo")
            time.sleep(15)

    def liberar(self) -> None:
        try:
            self.http.post("/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError as e:
            log(f"aviso: /free falhou: {e}")

    def upload(self, caminho: Path, nome: str) -> str:
        with caminho.open("rb") as fh:
            r = self.http.post(
                "/upload/image",
                files={"image": (nome, fh, "image/png")},
                data={"overwrite": "true", "type": "input"},
            )
        r.raise_for_status()
        d = r.json()
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    def executar(self, grafo: dict, tempo_limite: float, rotulo: str, liberar: bool = True) -> dict:
        """Enfileira um job (só com a fila vazia), acompanha e devolve os outputs.
        liberar=True: /free antes e depois (vídeos). Imagens Krea2 rodam em sequência sem /free (liberado pelo dono)."""
        import fcntl

        self._trava = open(Path(__file__).with_name(".comfy.lock"), "w")
        fcntl.flock(self._trava, fcntl.LOCK_EX)  # um job por vez também entre processos deste script
        try:
            return self._executar(grafo, tempo_limite, rotulo, liberar)
        finally:
            fcntl.flock(self._trava, fcntl.LOCK_UN)
            self._trava.close()

    def _executar(self, grafo: dict, tempo_limite: float, rotulo: str, liberar: bool) -> dict:
        self.esperar_fila_vazia()
        if liberar or (self.jobs and self.jobs % FREE_A_CADA == 0):
            self.liberar()
        self.checar_saude()
        self.jobs += 1
        r = self.http.post("/prompt", json={"prompt": grafo, "client_id": self.cid})
        if r.status_code != 200:
            raise RuntimeError(f"/prompt recusou {rotulo}: {r.text[:2000]}")
        pid = r.json()["prompt_id"]
        t0 = time.time()
        self._acompanhar_ws(pid, rotulo, t0)
        try:
            while True:
                time.sleep(5)
                try:
                    h = self.http.get(f"/history/{pid}", timeout=90).json()
                except (httpx.HTTPError, ValueError):
                    h = {}  # servidor engasgado (troca de modelo/swap): tenta de novo até o tempo limite
                if pid in h:
                    item = h[pid]
                    st = item.get("status", {})
                    if st.get("status_str") == "error":
                        msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                        raise RuntimeError(f"{rotulo} falhou: {json.dumps(msgs)[:2000]}")
                    if st.get("completed") or item.get("outputs"):
                        log(f"{rotulo}: concluído em {time.time() - t0:.0f}s")
                        return item["outputs"]
                if time.time() - t0 > tempo_limite:
                    log(f"{rotulo}: TEMPO LIMITE ({tempo_limite:.0f}s) estourado; /interrupt + /free e 60 s de espera")
                    try:
                        self.http.post("/interrupt", json={"prompt_id": pid})
                        self.http.post("/queue", json={"delete": [pid]})
                    except httpx.HTTPError:
                        pass
                    self.liberar()
                    time.sleep(60)
                    raise TimeoutError(rotulo)
        finally:
            if liberar:
                self.liberar()

    def saidas_anteriores(self, prefixo: str, no: str) -> list[dict] | None:
        """Reaproveita um job já concluído no servidor (ex.: o script caiu enquanto o job rodava)."""
        try:
            h = self.http.get("/history", params={"max_items": 64}, timeout=90).json()
        except (httpx.HTTPError, ValueError):
            return None
        for item in reversed(list(h.values())):
            imgs = item.get("outputs", {}).get(no, {}).get("images", [])
            if imgs and imgs[0]["filename"].startswith(prefixo.split("/")[-1]) and imgs[0].get("subfolder", "") == "/".join(prefixo.split("/")[:-1]):
                return imgs
        return None

    def _acompanhar_ws(self, pid: str, rotulo: str, t0: float) -> None:
        """Registra no log em que nó o job está (carregamento vs. amostragem), via websocket, se disponível."""
        try:
            import threading

            from websockets.sync.client import connect
        except ImportError:
            return
        url = self.base.replace("http", "ws", 1) + f"/ws?clientId={self.cid}"
        tipos = {}

        def loop() -> None:
            try:
                with connect(url, open_timeout=30, max_size=None) as ws:
                    ultimo = None
                    while True:
                        msg = ws.recv(timeout=TIMEOUT_VID + 60)
                        if not isinstance(msg, str):
                            continue
                        m = json.loads(msg)
                        d = m.get("data", {})
                        if d.get("prompt_id") not in (None, pid):
                            continue
                        if m["type"] == "executing":
                            no = d.get("node")
                            if no is None:
                                return
                            tipos.setdefault(no, no)
                            log(f"  {rotulo}: +{time.time() - t0:.0f}s nó {no}")
                        elif m["type"] == "progress":
                            v, mx = d.get("value", 0), d.get("max", 1)
                            if mx and (v == 1 or v == mx) and (v, mx) != ultimo:
                                ultimo = (v, mx)
                                log(f"  {rotulo}: +{time.time() - t0:.0f}s passo {v}/{mx}")
                        elif m["type"] in ("execution_error", "execution_interrupted", "execution_success"):
                            return
            except Exception:
                return

        threading.Thread(target=loop, daemon=True).start()

    def baixar(self, info: dict, destino: Path) -> None:
        r = self.http.get(
            "/view",
            params={"filename": info["filename"], "subfolder": info.get("subfolder", ""), "type": info.get("type", "output")},
            timeout=120,
        )
        r.raise_for_status()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)


# ---------------------------------------------------------------- grafos (formato API)


def grafo_krea2(imagem: str, prompt: str, seed: int, prefixo: str, ref_position: str = "aligned (trained)",
                lado: int = 1024, ref_boost: float = 4.0, passos: int = 12) -> dict:
    """Krea2_Edit_identidade.json convertido para API (grupo 2 / segunda referência fica de fora)."""
    return {
        "55": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "56": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "57": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "71": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["55", 0], "lora_name": "krea2_identity_edit_v2.safetensors", "strength_model": 1.0}},
        "72": {"class_type": "LoadImage", "inputs": {"image": imagem}},
        "73": {"class_type": "VAEEncode", "inputs": {"pixels": ["72", 0], "vae": ["57", 0]}},
        "82": {"class_type": "EmptySD3LatentImage", "inputs": {"width": lado, "height": lado, "batch_size": 1}},
        "79": {"class_type": "Krea2EditModelPatch", "inputs": {
            "model": ["71", 0], "source_latent": ["73", 0], "ref_boost": ref_boost, "ref_boost_a": 1.0, "fit_mode": "fit",
            "vae": ["57", 0], "source_image": ["72", 0], "target_latent": ["82", 0],
            "ref_position": ref_position, "ref_position_a": "aligned (trained)"}},
        "84": {"class_type": "Krea2EditGroundedEncode", "inputs": {"clip": ["56", 0], "prompt": prompt, "image": ["72", 0], "grounding_px": 768, "system_prompt": ""}},
        "85": {"class_type": "Krea2EditGroundedEncode", "inputs": {"clip": ["56", 0], "prompt": "", "image": ["72", 0], "grounding_px": 768, "system_prompt": ""}},
        "53": {"class_type": "KSampler", "inputs": {
            "model": ["79", 0], "positive": ["84", 0], "negative": ["85", 0], "latent_image": ["82", 0],
            "seed": seed, "steps": passos, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "54": {"class_type": "VAEDecode", "inputs": {"samples": ["53", 0], "vae": ["57", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["54", 0], "filename_prefix": prefixo}},
    }


def grafo_upscale(imagem: str, prefixo: str, lado: int = 2048) -> dict:
    """4x-AnimeSharp (1024 -> 4096) e redução Lanczos para 2048 no próprio Comfy; sai em PNG (sem perda)."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": imagem}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x-AnimeSharp.pth"}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "upscale_method": "lanczos", "width": lado,
                                                      "height": lado, "crop": "disabled"}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": prefixo}},
    }


HD = FONTE / "hd"


def fonte(nome: str) -> Path:
    """Verde de origem: a versão 2048 do upscaler (_fonte/hd/) se existir e for mais nova que a de 1024."""
    hd, sd = HD / f"{nome}.png", FONTE / f"{nome}.png"
    if hd.exists() and (not sd.exists() or hd.stat().st_mtime >= sd.stat().st_mtime):
        return hd
    return sd


def etapa_upscale(c: Comfy, nomes: list[str]) -> list[str]:
    feitos = []
    HD.mkdir(parents=True, exist_ok=True)
    for nome in nomes:
        src = FONTE / f"{nome}.png"
        if not src.exists():
            continue
        dest = HD / f"{nome}.png"
        if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
            continue
        prefixo = f"goxys/hd_{nome}_{int(src.stat().st_mtime)}"
        up = c.upload(src, f"goxys_hd_{nome}.png")
        c.esperar_fila_vazia()
        prontas = c.saidas_anteriores(prefixo, "29")
        try:
            saidas = {"29": {"images": prontas}} if prontas else c.executar(grafo_upscale(up, prefixo), TIMEOUT_IMG,
                                                                           f"upscale {nome}", liberar=False)
        except ParadaSeguranca:
            raise
        except (TimeoutError, RuntimeError) as e:
            log(f"upscale {nome}: falhou ({e})")
            continue
        c.baixar(saidas["29"]["images"][0], dest)
        feitos.append(nome)
    return feitos


def comprimento_quadros(segundos: float) -> int:
    """Mesma conta do ComfyMathExpression do template (grade 17k+5 a 24 fps)."""
    n = max(5, round(segundos * 24))
    return n + (5 - (n % 17)) % 17


def grafo_minimax(imagem: str, prompt: str, seed: int, prefixo: str, lado: int, segundos: float,
                  passos: int = 4) -> dict:
    """Subgraph 'Image to Video (MiniMax H3)' expandido, com LoRA turbo 4 passos, first=last frame,
    sem decodificação de áudio; os quadros saem como PNG (SaveImage) para o chroma key ficar limpo."""
    return {
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "121": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["6", 0], "lora_name": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", "strength_model": 1.0}},
        "13": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "type": "minimax", "device": "default"}},
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_int8_convrot.safetensors"}},
        "114": {"class_type": "LoadImage", "inputs": {"image": imagem}},
        "104": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["13", 0], "vae": ["11", 0], "prompt": prompt, "width": lado, "height": lado,
            "length": comprimento_quadros(segundos), "first_frame": ["114", 0], "last_frame": ["114", 0]}},
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "16": {"class_type": "BasicGuider", "inputs": {"model": ["121", 0], "conditioning": ["104", 0]}},
        "17": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "9": {"class_type": "BasicScheduler", "inputs": {"model": ["121", 0], "scheduler": "simple", "steps": passos, "denoise": 1.0}},
        "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["15", 0], "guider": ["16", 0], "sampler": ["17", 0], "sigmas": ["9", 0], "latent_image": ["104", 1]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["11", 0]}},
        "30": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": prefixo}},
    }


# ---------------------------------------------------------------- etapa 1: imagens


def itens_imagem() -> dict[str, str]:
    itens = {"base": "Keep the same pose, neutral friendly expression."}
    itens.update(AGENTES)
    itens.update(ESTADOS)
    return itens


def prompt_imagem(acao: str, tentativa: int) -> str:
    p = f"{acao} {ANATOMIA} {IDENTIDADE} {FUNDO}"
    if tentativa >= 2:
        p += " " + FUNDO_FORTE
    return p


def fundo_verde_ok(caminho: Path) -> float:
    """Fração da borda (faixa de 16 px) que é verde liso: matiz verde, saturado e perto da cor mediana."""
    f = np.asarray(Image.open(caminho).convert("RGB")).astype(np.float32) / 255.0
    b = 16
    borda = np.concatenate([f[:b].reshape(-1, 3), f[-b:].reshape(-1, 3), f[:, :b].reshape(-1, 3), f[:, -b:].reshape(-1, 3)])
    verd = borda[:, 1] - np.maximum(borda[:, 0], borda[:, 2])
    if (verd > 0.2).mean() < 0.5:
        return float((verd > 0.2).mean())
    med = np.median(borda[verd > 0.2], axis=0)
    return float(((verd > 0.2) & (np.abs(borda - med).max(1) < 0.08)).mean())


def etapa_imagens(c: Comfy, so: set[str] | None, refazer: set[str], tentativa: int) -> None:
    FONTE.mkdir(parents=True, exist_ok=True)
    ref_base = c.upload(BASE_IMG, "goxys_base_ref.png")
    itens = [(n, a, None) for n, a in itens_imagem().items()]
    for est, extras in QUADROS_CHAVE.items():
        for i, mud in enumerate(extras, start=2):
            itens.append((f"{est}-k{i}", mud, est))
    for nome, acao, origem in itens:
        if so and nome not in so:
            continue
        destino = FONTE / f"{nome}.png"
        if destino.exists() and nome not in refazer:
            continue
        t = tentativa if nome in refazer else 1
        if origem:
            if not (FONTE / f"{origem}.png").exists():
                log(f"{nome}: falta a pose {origem}.png; pulando")
                continue
            ref = c.upload(FONTE / f"{origem}.png", f"goxys_ref_{origem}.png")
        else:
            ref = ref_base
        while t <= 2:
            pos = "aligned (trained)" if nome in ("base",) or nome in AGENTES or t == 1 else "beside (right of target)"
            rot = f"krea2 {nome} (tentativa {t})"
            if origem:
                pos = "aligned (trained)"
                prompt = f"{acao} {MANTER_CENA}" + (" " + FUNDO_FORTE if t >= 2 else "")
            else:
                prompt = prompt_imagem(acao, t)
            grafo = grafo_krea2(ref, prompt, semente(nome, t), f"goxys/{nome}_t{t}", ref_position=pos)
            try:
                c.esperar_fila_vazia()
                prontas = c.saidas_anteriores(f"goxys/{nome}_t{t}", "29")
                if prontas:
                    log(f"{rot}: reaproveitando resultado já gerado no servidor")
                    saidas = {"29": {"images": prontas}}
                else:
                    saidas = c.executar(grafo, TIMEOUT_IMG, rot, liberar=False)
            except ParadaSeguranca:
                raise
            except (TimeoutError, RuntimeError) as e:
                log(f"{rot}: falhou ({e}); seguindo")
                break
            info = saidas["29"]["images"][0]
            bruto = FONTE / f"{nome}_t{t}.png"
            c.baixar(info, bruto)
            verde = fundo_verde_ok(bruto)
            log(f"{rot}: borda verde {verde:.0%} -> {bruto.name}")
            shutil.copyfile(bruto, destino)
            if verde >= 0.9:
                break
            t += 1
        if not origem:
            publicar_parcial(nome)
        elif all((FONTE / f"{origem}-k{i}.png").exists() for i in range(2, 2 + len(QUADROS_CHAVE[origem]))):
            try:
                animar_quadros_chave(origem, forcar=True)
                escrever_manifest()
            except Exception as e:
                log(f"aviso: animar {origem} falhou: {e}")
    c.liberar()  # descarrega o Krea2 antes de qualquer vídeo


# ---------------------------------------------------------------- etapa 2: vídeos

LADO_VIDEO = 448
SEGUNDOS = 1.6  # -> 39 quadros (grade 17k+5)
RAM_MIN_GB = 26
VRAM_MIN_GB = 10


def etapa_videos(c: Comfy, so: set[str] | None, idle: bool, lado: int, segundos: float) -> None:
    if os.environ.get("GOXYS_PERMITIR_VIDEO") != "1":
        log("vídeo (MiniMax H3) PROIBIDO: travou a máquina do ComfyUI em 07/11; use os laços por quadros-chave. "
            "Para reativar conscientemente: GOXYS_PERMITIR_VIDEO=1")
        return
    """MiniMax H3 local. SUSPENSO por padrão: o TE de 32B + DiT de 19,5 GB travaram a máquina (32 GB RAM).
    Só roda com memória folgada (ram_free >= 26 GB e vram_free >= 10 GB), um clipe por chamada."""
    try:
        st = c.http.get("/system_stats", timeout=30).json()
        ram = st["system"]["ram_free"] / 1e9
        vram = st["devices"][0]["vram_free"] / 1e9
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        log("vídeo: /system_stats não respondeu; MiniMax não será tentado")
        return
    if ram < RAM_MIN_GB or vram < VRAM_MIN_GB:
        log(f"vídeo: memória insuficiente (RAM livre {ram:.1f} GB, VRAM livre {vram:.1f} GB); MiniMax pulado")
        return
    alvos = {f"estado-{k}": (k, MOV_ESTADOS[k]) for k in ESTADOS}
    if idle:
        alvos.update({f"idle-{k}": (k, MOV_IDLE) for k in AGENTES})
    for nome, (fonte, mov) in alvos.items():
        if so and nome not in so and fonte not in so:
            continue
        pasta = FONTE / f"video-{nome}"
        if (pasta / ".ok").exists():
            continue
        img = FONTE / f"{fonte}.png"
        if not img.exists():
            log(f"{nome}: sem imagem de origem {img.name}; pulando")
            continue
        c.esperar_fila_vazia()
        prefixo = f"goxys/vid_{nome}"
        imgs = c.saidas_anteriores(prefixo, "30")
        if imgs:
            log(f"{nome}: reaproveitando {len(imgs)} quadros já gerados no servidor")
            _salvar_quadros(c, imgs, pasta, lado, segundos)
            continue
        # entrada já no tamanho do vídeo, fundo verde
        tmp = Path(tempfile.mkdtemp()) / f"goxys_{nome}_in.png"
        Image.open(img).convert("RGB").resize((lado, lado), Image.LANCZOS).save(tmp)
        nome_up = c.upload(tmp, tmp.name)
        prompt = VIDEO_PROMPT.format(mov=mov)
        rot = f"minimax {nome} ({lado}px, {comprimento_quadros(segundos)} quadros)"
        try:
            saidas = c.executar(grafo_minimax(nome_up, prompt, semente(nome, 1), prefixo, lado, segundos),
                                TIMEOUT_VID, rot)
        except (TimeoutError, RuntimeError) as e:
            log(f"{rot}: falhou ({e}); seguindo com quadros-chave. Não tente vídeo de novo nesta sessão.")
            return
        _salvar_quadros(c, saidas["30"]["images"], pasta, lado, segundos)
        return  # um único vídeo por execução


def _salvar_quadros(c: Comfy, imgs: list[dict], pasta: Path, lado: int, segundos: float) -> None:
    if pasta.exists():
        shutil.rmtree(pasta)
    for i, info in enumerate(imgs):
        c.baixar(info, pasta / f"f_{i:03d}.png")
    (pasta / ".ok").write_text(json.dumps({"quadros": len(imgs), "lado": lado, "segundos": segundos}))
    log(f"{pasta.name}: {len(imgs)} quadros baixados")


# ---------------------------------------------------------------- etapa 3: chroma key


def _hue(f: np.ndarray) -> np.ndarray:
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx, mn = f.max(-1), f.min(-1)
    d = np.maximum(mx - mn, 1e-6)
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4))
    return h * 60.0


def cor_fundo(f: np.ndarray, b: int = 8) -> np.ndarray:
    borda = np.concatenate([f[:b].reshape(-1, 3), f[-b:].reshape(-1, 3), f[:, :b].reshape(-1, 3), f[:, -b:].reshape(-1, 3)])
    verde = (borda[:, 1] - np.maximum(borda[:, 0], borda[:, 2])) > 0.12
    return np.median(borda[verde], axis=0) if verde.sum() > 50 else np.array([0.0, 1.0, 0.0], np.float32)


def recortar(rgb: np.ndarray, bg: np.ndarray | None = None) -> np.ndarray:
    """Chroma key do verde -> RGBA uint8, sem halo.

    O Krea2/MiniMax devolvem um verde "abafado" (~#55A958), e o personagem tem turquesa; um key só por
    cor é estreito demais. Então:
      1. máscara de fundo = pixels de matiz verde (±28° do fundo) com saturação, em componentes grandes
         (o personagem não tem nenhuma área verde, e tudo nele é contornado de preto);
      2. alfa suave só numa faixa de ~3 px na fronteira: a = 1 - verdura(C)/verdura(BG), que é exato
         quando o vizinho do fundo é o contorno preto (verdura 0);
      3. "unmix" F = (C - (1-a)BG)/a + despill na faixa, o que tira o verde das bordas.
    """
    from scipy import ndimage as ndi

    f = rgb.astype(np.float32) / 255.0
    if bg is None:
        bg = cor_fundo(f)
    bg = bg.astype(np.float32)
    verd = f[..., 1] - np.maximum(f[..., 0], f[..., 2])
    vbg = max(float(bg[1] - max(bg[0], bg[2])), 0.05)
    hbg = float(_hue(bg[None, None, :])[0, 0])
    dh = np.abs(((_hue(f) - hbg) + 180) % 360 - 180)
    esverdeado = (dh < 28) & (verd > 0.45 * vbg)
    esverdeado = ndi.binary_opening(esverdeado, iterations=1)
    lab, n = ndi.label(esverdeado)
    if n:
        tam = ndi.sum(esverdeado, lab, index=np.arange(1, n + 1))
        grandes = np.zeros(n + 1, bool)
        grandes[1:] = tam >= max(60, esverdeado.size // 4000)
        fundo = grandes[lab]
    else:
        fundo = esverdeado
    perto_fundo = ndi.binary_dilation(fundo, iterations=3)
    perto_fg = ndi.binary_dilation(~fundo, iterations=3)
    faixa = perto_fundo & perto_fg
    suave = 1.0 - np.clip(verd / vbg, 0.0, 1.0)
    suave = np.clip((suave - 0.10) / 0.80, 0.0, 1.0)  # textura leve do fundo -> 0
    a = np.where(fundo, 0.0, 1.0).astype(np.float32)
    a = np.where(faixa, suave, a)
    a = ndi.gaussian_filter(a, 0.5)
    a = np.where(a < 0.04, 0.0, np.where(a > 0.97, 1.0, a))
    m = max(2, a.shape[0] // 256)  # o upscaler deixa uma moldura escura nas bordas da imagem: zera
    a[:m], a[-m:], a[:, :m], a[:, -m:] = 0, 0, 0, 0
    aa = np.maximum(a, 1e-3)[..., None]
    fg = np.clip((f - (1.0 - aa) * bg[None, None, :]) / aa, 0.0, 1.0)
    # despill na faixa: G não passa de max(R,B) (contorno é preto, não há verde legítimo ali)
    g_lim = np.maximum(fg[..., 0], fg[..., 2]) + 0.02
    fg[..., 1] = np.where(faixa, np.minimum(fg[..., 1], g_lim), fg[..., 1])
    out = np.dstack([fg, a])
    return (out * 255 + 0.5).astype(np.uint8)


def recortar_original(rgb: np.ndarray, tol: float = 0.07) -> np.ndarray:
    """Recorte do goxy.png original (fundo creme liso, quase igual ao corpo): flood-fill a partir das 4 bordas
    sobre pixels próximos da cor do fundo; o contorno preto grosso isola o creme do corpo. Na faixa de 3 px
    da fronteira o alfa vem da luminância (mistura fundo creme x contorno escuro) e a cor é 'desmisturada'."""
    from scipy import ndimage as ndi

    f = rgb.astype(np.float32) / 255.0
    b = 6
    borda = np.concatenate([f[:b].reshape(-1, 3), f[-b:].reshape(-1, 3), f[:, :b].reshape(-1, 3), f[:, -b:].reshape(-1, 3)])
    bg = np.median(borda, axis=0)
    perto = np.abs(f - bg).max(-1) < tol
    lab, n = ndi.label(perto)
    tocam = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    fundo = np.isin(lab, tocam[tocam > 0])
    lum = f @ np.array([0.299, 0.587, 0.114], np.float32)
    lbg = float(bg @ np.array([0.299, 0.587, 0.114], np.float32))
    contorno = np.percentile(lum[~fundo], 2)
    faixa = ndi.binary_dilation(fundo, iterations=3) & ndi.binary_dilation(~fundo, iterations=3)
    suave = np.clip((lbg - lum) / max(lbg - contorno, 1e-3), 0.0, 1.0)
    suave = np.clip((suave - 0.04) / 0.92, 0.0, 1.0)
    a = np.where(fundo, 0.0, 1.0).astype(np.float32)
    a = np.where(faixa, np.where(fundo, suave, np.maximum(suave, 0.0)), a)
    # dentro do personagem, na faixa, pixels claros (creme do corpo encostado no contorno) continuam opacos
    a = np.where(faixa & ~fundo & (lum < lbg - 0.02), np.maximum(a, 1.0), a)
    a = ndi.gaussian_filter(a, 0.5)
    a = np.where(a < 0.04, 0.0, np.where(a > 0.97, 1.0, a))
    aa = np.maximum(a, 1e-3)[..., None]
    fg = np.where(faixa[..., None], np.clip((f - (1.0 - aa) * bg) / aa, 0.0, 1.0), f)
    return (np.dstack([fg, a]) * 255 + 0.5).astype(np.uint8)


def caixa_alfa(frames: list[np.ndarray], margem: int = 12) -> tuple[int, int, int, int]:
    """Caixa quadrada que contém o alfa de todos os quadros (para o clipe não pular)."""
    h, w = frames[0].shape[:2]
    acc = np.zeros((h, w), bool)
    for fr in frames:
        acc |= fr[..., 3] > 8
    ys, xs = np.where(acc)
    if len(ys) == 0:
        return 0, 0, w, h
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    lado = max(x1 - x0, y1 - y0) + 2 * margem
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    lado = min(lado, max(w, h))
    x0 = int(np.clip(cx - lado // 2, 0, max(0, w - lado)))
    y0 = int(np.clip(cy - lado // 2, 0, max(0, h - lado)))
    return x0, y0, x0 + lado, y0 + lado


# enquadramento comum (mesmo de goxys/scripts/normalizar_mascote.py): tela 512, corpo 448 de altura,
# largura máx. 480, pés na linha 496 — proporcional para outros lados
ENQ_LADO, ENQ_ALTURA, ENQ_LARGURA, ENQ_BASE = 512, 448, 480, 496


def normalizar_png(png: Path) -> None:
    """Roda o normalizador do coordenador (PNG + WebP) se ele existir."""
    norm = ROOT / "goxys" / "scripts" / "normalizar_mascote.py"
    if not norm.exists() or not png.exists():
        return
    import importlib.util

    spec = importlib.util.spec_from_file_location("normalizar_mascote", norm)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.normalizar(png)


def enquadrar(frames: list[np.ndarray], lado: int) -> list[np.ndarray]:
    """Mesma escala e posição do quadro base (frames[0]) em todos os quadros, para o laço não pular."""
    k = lado / ENQ_LADO
    a0 = Image.fromarray(frames[0]).getchannel("A").point(lambda a: 255 if a > 24 else 0)
    bx0, by0, bx1, by1 = a0.getbbox() or (0, 0, frames[0].shape[1], frames[0].shape[0])
    w, h = bx1 - bx0, by1 - by0
    esc = min(ENQ_ALTURA * k / h, ENQ_LARGURA * k / w)
    ox = (lado - w * esc) / 2 - bx0 * esc
    oy = ENQ_BASE * k - h * esc - by0 * esc
    saida = []
    for f in frames:
        im = Image.fromarray(f)
        im = im.resize((max(1, round(im.width * esc)), max(1, round(im.height * esc))), Image.LANCZOS)
        tela = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
        tela.paste(im, (round(ox), round(oy)))
        saida.append(np.asarray(tela))
    return saida


def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def salvar_estatico(nome: str, lado: int = 512, forcar: bool = False) -> None:
    """Recorte em resolução cheia (nunca depois de reduzir) -> normalizar_mascote (tela 1024 + @512)."""
    src = BASE_IMG if nome == "base" else fonte(nome)
    if not src.exists():
        return
    alvo = OUT / f"{nome}.webp"
    if not forcar and alvo.exists() and alvo.stat().st_mtime >= src.stat().st_mtime and (OUT / f"{nome}@512.webp").exists():
        return
    rgb = np.asarray(Image.open(src).convert("RGB"))
    rgba = recortar_original(rgb) if nome == "base" else recortar(rgb)
    x0, y0, x1, y1 = caixa_alfa([rgba], margem=8)
    Image.fromarray(rgba).crop((x0, y0, x1, y1)).save(OUT / f"{nome}.png")
    normalizar_png(OUT / f"{nome}.png")


def codificar_webm(frames: list[np.ndarray], destino: Path, fps: int, crf: int) -> None:
    with tempfile.TemporaryDirectory() as td:
        for i, fr in enumerate(frames):
            Image.fromarray(fr).save(Path(td) / f"k_{i:03d}.png")
        cmd = [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(fps), "-i", str(Path(td) / "k_%03d.png"),
               "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", str(crf),
               "-deadline", "good", "-cpu-used", "1", "-row-mt", "1", "-an", str(destino)]
        subprocess.run(cmd, check=True)


def processar_clipe(nome: str, lado: int = 640, fps: int = 20, meta_kb: int = 400) -> dict | None:
    pasta = FONTE / f"video-{nome}"
    if not (pasta / ".ok").exists():
        return None
    destino = OUT / f"{nome}.webm"
    poster = OUT / f"{nome}.png"
    if destino.exists() and poster.exists():
        return {"video": destino, "poster": poster}
    quadros = sorted(pasta.glob("f_*.png"))
    meta = json.loads((pasta / ".ok").read_text())
    fps_src = 24
    # reamostra 24 -> fps
    idx = np.round(np.arange(0, len(quadros), fps_src / fps)).astype(int)
    idx = idx[idx < len(quadros)]
    rgba = enquadrar([recortar(np.asarray(Image.open(quadros[i]).convert("RGB"))) for i in idx], lado)
    for crf in (40, 46, 52, 58):
        codificar_webm(rgba, destino, fps, crf)
        kb = destino.stat().st_size / 1024
        log(f"{nome}.webm: crf {crf} -> {kb:.0f} KB ({len(rgba)} quadros, {lado}px, {fps} fps; origem {meta})")
        if kb < meta_kb:
            break
    Image.fromarray(rgba[0]).save(poster, optimize=True)
    return {"video": destino, "poster": poster}


def animar_estatico(nome: str, destino: Path, lado: int = 640, fps: int = 20, segundos: float = 2.0) -> bool:
    """Plano B sem GPU: laço leve (respira/quica) a partir do estático verde, webm VP9 com alfa."""
    src = fonte(nome)
    if not src.exists():
        return False
    base = Image.fromarray(enquadrar([recortar(np.asarray(Image.open(src).convert("RGB")))], lado)[0])
    pe_x, pe_y = lado / 2, ENQ_BASE * lado / ENQ_LADO
    n = int(fps * segundos)
    quadros = []
    for i in range(n):
        t = 2 * np.pi * i / n
        sy = 1.0 + 0.025 * np.sin(t)          # respira
        sx = 1.0 - 0.015 * np.sin(t)
        dy = -5.0 * max(0.0, np.sin(t)) * lado / 400  # quique leve
        w, h = round(lado * sx), round(lado * sy)
        im = base.resize((w, h), Image.LANCZOS)
        tela = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
        tela.paste(im, (round(pe_x - pe_x * sx), round(pe_y - pe_y * sy + dy)))
        quadros.append(np.asarray(tela))
    for crf in (40, 48, 56):
        codificar_webm(quadros, destino, fps, crf)
        if destino.stat().st_size < 400 * 1024:
            break
    log(f"{destino.name}: laço de respiração (sem GPU), {destino.stat().st_size / 1024:.0f} KB")
    return True


def publicar_parcial(nome: str, forcar: bool = False) -> None:
    """Publica o recorte de um item assim que fica pronto e reescreve o manifest parcial."""
    try:
        if (FONTE / f"{nome}.png").exists():
            if nome in ESTADOS:
                poster = OUT / f"estado-{nome}.png"
                if forcar or not poster.exists() or not (OUT / f"estado-{nome}@512.webp").exists() or (
                        poster.stat().st_mtime < (FONTE / f"{nome}.png").stat().st_mtime):
                    rgba = recortar(np.asarray(Image.open(fonte(nome)).convert("RGB")))
                    x0, y0, x1, y1 = caixa_alfa([rgba], margem=8)
                    Image.fromarray(rgba).crop((x0, y0, x1, y1)).save(poster)
                    normalizar_png(poster)
            else:
                salvar_estatico(nome, forcar=forcar)
        elif nome == "base":
            salvar_estatico(nome, forcar=forcar)
        escrever_manifest()
    except Exception as e:  # nunca derruba a geração por causa da publicação
        log(f"aviso: publicar {nome} falhou: {e}")


def escrever_manifest() -> dict:
    manifest = {"agentes": {}, "estados": {}, "base": None,
                "gerado_em": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    def sm(n: str) -> str | None:
        return f"{URL_PREFIX}/{n}@512.webp" if (OUT / f"{n}@512.webp").exists() else None

    if (OUT / "base.webp").exists():
        manifest["base"] = {"img": f"{URL_PREFIX}/base.webp", "img_sm": sm("base"), "png": f"{URL_PREFIX}/base.png"}
    if (OUT / "hero.webm").exists():
        manifest["hero"] = {"video": f"{URL_PREFIX}/hero.webm",
                            "poster": f"{URL_PREFIX}/hero.png" if (OUT / "hero.png").exists() else None}
    for nome in AGENTES:
        img = OUT / f"{nome}.webp"
        vid = OUT / f"idle-{nome}.webm"
        manifest["agentes"][nome] = {
            "img": f"{URL_PREFIX}/{nome}.webp" if img.exists() else None,
            "img_sm": sm(nome),
            "png": f"{URL_PREFIX}/{nome}.png" if (OUT / f"{nome}.png").exists() else None,
            "video": f"{URL_PREFIX}/idle-{nome}.webm" if vid.exists() else None,
        }
    for est in ESTADOS:
        manifest["estados"][est] = {
            "video": f"{URL_PREFIX}/estado-{est}.webm" if (OUT / f"estado-{est}.webm").exists() else None,
            "poster": f"{URL_PREFIX}/estado-{est}.png" if (OUT / f"estado-{est}.png").exists() else None,
            "poster_sm": sm(f"estado-{est}"),
            "img": f"{URL_PREFIX}/estado-{est}.webp" if (OUT / f"estado-{est}.webp").exists() else None,
            "img_sm": sm(f"estado-{est}"),
        }
    tmp = OUT / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(OUT / "manifest.json")
    return manifest


def animar_quadros_chave(est: str, lado: int = 640, fps: int = 20, segurar: int = 9, fundir: int = 6,
                         forcar: bool = False) -> bool:
    """Laço por quadros-chave: k1 -> k2 -> k3 -> k1 com crossfade (alfa pré-multiplicado) + bounce/escala leve.
    Saída: estado-<est>.webm (VP9 alfa, < 300 KB) e estado-<est>.png (pôster = k1)."""
    destino = OUT / f"estado-{est}.webm"
    if destino.exists() and not forcar:
        return True
    fontes = [fonte(est)] + [fonte(f"{est}-k{i}") for i in range(2, 2 + len(QUADROS_CHAVE.get(est, [])))]
    fontes = [f for f in fontes if f.exists()]
    if len(fontes) < 2:
        return False
    chaves = enquadrar([recortar(np.asarray(Image.open(f).convert("RGB"))) for f in fontes], lado)
    chaves = [k.astype(np.float32) / 255.0 for k in chaves]
    pre = [np.dstack([k[..., :3] * k[..., 3:], k[..., 3:]]) for k in chaves]  # pré-multiplicado
    seq = []
    n = len(pre)
    for i in range(n):
        a, b = pre[i], pre[(i + 1) % n]
        seq += [a] * segurar
        for j in range(1, fundir + 1):
            t = j / (fundir + 1)
            t = t * t * (3 - 2 * t)  # suaviza
            seq.append(a * (1 - t) + b * t)
    total = len(seq)
    pe_x, pe_y = lado / 2, ENQ_BASE * lado / ENQ_LADO
    quadros = []
    for i, q in enumerate(seq):
        fase = 2 * np.pi * i / total * n  # um "quique" por quadro-chave
        esc = 1.0 + 0.015 * np.sin(fase)
        dy = -4.0 * max(0.0, np.sin(fase)) * lado / 400
        rgb = np.where(q[..., 3:] > 1e-4, q[..., :3] / np.maximum(q[..., 3:], 1e-4), 0.0)
        im = Image.fromarray((np.dstack([rgb, q[..., 3]]) * 255 + 0.5).clip(0, 255).astype(np.uint8))
        w = h = max(1, round(lado * esc))
        im = im.resize((w, h), Image.LANCZOS)
        tela = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
        tela.paste(im, (round(pe_x - pe_x * esc), round(pe_y - pe_y * esc + dy)))
        quadros.append(np.asarray(tela))
    for crf in (36, 42, 48, 54):
        codificar_webm(quadros, destino, fps, crf)
        kb = destino.stat().st_size / 1024
        if kb < 400:
            break
    publicar_parcial(est)  # pôster = k1 em resolução cheia, normalizado (mesmo enquadramento do 1º quadro)
    log(f"estado-{est}.webm: {len(fontes)} quadros-chave, {total} quadros a {fps} fps ({total / fps:.1f}s), crf {crf}, {kb:.0f} KB")
    return True


def etapa_reexportar(lado_video: int = 640, fps: int = 20) -> None:
    """Reexporta tudo no formato 1024 (PNG sem perda, WebP q92, @512) e remonta os webm em 640 px (sem GPU)."""
    for nome in ["base", *AGENTES]:
        salvar_estatico(nome, forcar=True)
        escrever_manifest()
    for est in ESTADOS:
        if not (FONTE / f"{est}.png").exists():
            continue
        publicar_parcial(est, forcar=True)
        if not animar_quadros_chave(est, lado_video, fps, forcar=True):
            (OUT / f"estado-{est}.webm").unlink(missing_ok=True)
            animar_estatico(est, OUT / f"estado-{est}.webm", lado_video, fps)
        escrever_manifest()
    for nome in AGENTES:
        (OUT / f"idle-{nome}.webm").unlink(missing_ok=True)
        animar_estatico(nome, OUT / f"idle-{nome}.webm", lado_video, fps)
        escrever_manifest()
    log("reexportação 1024/640 concluída")


def etapa_pos(lado_video: int, fps: int, idle_leve: bool = True, estados_leves: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for nome in ["base", *AGENTES, *ESTADOS]:
        publicar_parcial(nome)
    for est in ESTADOS:
        clip = processar_clipe(f"estado-{est}", lado_video, fps)
        dest = OUT / f"estado-{est}.webm"
        if not clip and not dest.exists():
            clip = animar_quadros_chave(est, lado_video, fps)
        if not clip and estados_leves and (FONTE / f"{est}.png").exists() and not dest.exists():
            animar_estatico(est, dest, lado_video, fps)  # plano B: laço por quadros-chave
        escrever_manifest()
    for nome in AGENTES:
        clip = processar_clipe(f"idle-{nome}", lado_video, fps)
        dest = OUT / f"idle-{nome}.webm"
        if not clip and idle_leve and (OUT / f"{nome}.webp").exists() and not dest.exists():
            animar_estatico(nome, dest, lado_video, fps)
        escrever_manifest()
    m = escrever_manifest()
    log(f"manifest.json escrito ({sum(1 for v in m['estados'].values() if v['video'])} clipes de estado, "
        f"{sum(1 for v in m['agentes'].values() if v['video'])} idle de agente)")


# ---------------------------------------------------------------- herói da landing (único vídeo MiniMax autorizado)

HERO_MOV = ("The mascot gives a gentle friendly wave with its cream hand and breathes softly, its body rising and "
            "falling slightly, then returns exactly to the starting pose.")


def etapa_hero(c: Comfy) -> None:
    """UM vídeo MiniMax H3 mínimo (384 px, 73 quadros ~3 s, turbo 4 passos) do personagem base, com protocolo
    de segurança: /free + 30 s, exige RAM livre >= 26 GB e VRAM livre >= 11 GB; vigia /system_stats a cada 30 s
    (timeout 5 s); interrompe se lento ou RAM livre < 3 GB; tempo limite 8 min; /free no fim."""
    pasta = FONTE / "video-hero"
    if (pasta / ".ok").exists():
        log("hero: quadros já existem; pulando geração")
        return
    c.esperar_fila_vazia()

    def mem() -> tuple[float, float] | None:
        try:
            return c.memoria()
        except ParadaSeguranca as e:
            log(f"hero: {e}")
            return None

    c.liberar()
    time.sleep(30)
    m = mem()
    if m and (m[0] < 26 or m[1] < 11):
        log(f"hero: memória insuficiente (RAM {m[0]:.1f} GB, VRAM {m[1]:.1f} GB); /free de novo e 60 s")
        c.liberar()
        time.sleep(60)
        m = mem()
    if not m or m[0] < 26 or m[1] < 11:
        log(f"hero: ABORTADO antes de enfileirar (memória: {m})")
        return
    log(f"hero: memória ok (RAM {m[0]:.1f} GB, VRAM {m[1]:.1f} GB); enfileirando 384 px / 73 quadros / 4 passos")
    tmp = Path(tempfile.mkdtemp()) / "goxys_hero_in.png"
    Image.open(FONTE / "base.png").convert("RGB").resize((384, 384), Image.LANCZOS).save(tmp)
    nome_up = c.upload(tmp, tmp.name)
    grafo = grafo_minimax(nome_up, VIDEO_PROMPT.format(mov=HERO_MOV), semente("hero", 1), "goxys/vid_hero", 384, 3.0)
    r = c.http.post("/prompt", json={"prompt": grafo, "client_id": c.cid})
    if r.status_code != 200:
        log(f"hero: /prompt recusou: {r.text[:500]}")
        c.liberar()
        return
    pid = r.json()["prompt_id"]
    t0 = time.time()
    c._acompanhar_ws(pid, "hero", t0)
    motivo = None
    while True:
        time.sleep(30)
        try:
            t1 = time.time()
            h = c.http.get(f"/history/{pid}", timeout=5).json()
        except (httpx.HTTPError, ValueError):
            motivo = "/history não respondeu em 5 s"
            break
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                motivo = "erro de execução: " + json.dumps([x for x in st.get("messages", []) if x[0] == "execution_error"])[:400]
                break
            if h[pid].get("outputs"):
                break
        m = mem()
        if m is None:
            motivo = "/system_stats lento ou sem resposta"
            break
        log(f"hero: +{time.time() - t0:.0f}s RAM livre {m[0]:.1f} GB, VRAM livre {m[1]:.1f} GB")
        if m[0] < 3:
            motivo = f"RAM livre {m[0]:.1f} GB < 3 GB"
            break
        if time.time() - t0 > 8 * 60:
            motivo = "tempo limite de 8 min"
            break
    if motivo:
        log(f"hero: INTERROMPIDO ({motivo}) após {time.time() - t0:.0f}s; /interrupt + /free. Não tentar de novo.")
        for rota, corpo in (("/interrupt", {"prompt_id": pid}), ("/queue", {"delete": [pid]}),
                            ("/free", {"unload_models": True, "free_memory": True})):
            try:
                c.http.post(rota, json=corpo, timeout=10)
            except httpx.HTTPError:
                pass
        return
    log(f"hero: concluído em {time.time() - t0:.0f}s")
    imgs = h[pid]["outputs"]["30"]["images"]
    c.liberar()
    _salvar_quadros(c, imgs, pasta, 384, 3.0)


def montar_hero(lado: int = 384, fps: int = 20) -> None:
    pasta = FONTE / "video-hero"
    if not (pasta / ".ok").exists():
        return
    quadros = sorted(pasta.glob("f_*.png"))
    idx = np.round(np.arange(0, len(quadros), 24 / fps)).astype(int)
    idx = idx[idx < len(quadros)]
    bg = cor_fundo(np.asarray(Image.open(quadros[0]).convert("RGB")).astype(np.float32) / 255.0)
    rgba = enquadrar([recortar(np.asarray(Image.open(quadros[i]).convert("RGB")), bg) for i in idx], lado)
    destino = OUT / "hero.webm"
    for crf in (34, 40, 46, 52):
        codificar_webm(rgba, destino, fps, crf)
        kb = destino.stat().st_size / 1024
        if kb < 400:
            break
    Image.fromarray(rgba[0]).save(OUT / "hero.png", optimize=True)
    log(f"hero.webm: {len(rgba)} quadros, {lado}px, {fps} fps, crf {crf}, {kb:.0f} KB")
    escrever_manifest()


# ---------------------------------------------------------------- etapa 4: validação do alfa


def etapa_validar(so: set[str] | None = None, quadros: tuple[int, ...] = (0, 15, 30)) -> Path | None:
    """Decodifica quadros de cada webm com libvpx-vp9 (preserva o alfa), compõe sobre #070f26 e #eef3fb
    e junta tudo numa única grade: linhas = clipes, colunas = quadros (escuro | claro)."""
    dest = FONTE / "validacao"
    dest.mkdir(parents=True, exist_ok=True)
    linhas = []
    for webm in sorted(OUT.glob("*.webm")):
        if so and webm.stem not in so and webm.stem.split("-", 1)[-1] not in so:
            continue
        celulas = []
        for n in quadros:
            with tempfile.TemporaryDirectory() as td:
                png = Path(td) / "q.png"
                subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-c:v", "libvpx-vp9", "-i", str(webm),
                                "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-pix_fmt", "rgba", str(png)], check=True)
                if not png.exists():
                    continue
                q = Image.open(png).convert("RGBA")
            a = np.asarray(q)[..., 3]
            if n == quadros[0]:
                log(f"validar {webm.name}: {webm.stat().st_size / 1024:.0f} KB, alfa {a.min()}..{a.max()}, transparente {np.mean(a < 8):.0%}")
            for cor in ("#070f26", "#eef3fb"):
                fundo = Image.new("RGBA", q.size, cor)
                fundo.alpha_composite(q)
                celulas.append(fundo.convert("RGB").resize((160, 160), Image.LANCZOS))
        if celulas:
            linha = Image.new("RGB", (160 * len(celulas), 160))
            for i, c in enumerate(celulas):
                linha.paste(c, (i * 160, 0))
            linhas.append(linha)
    if not linhas:
        return None
    grade = Image.new("RGB", (max(l.width for l in linhas), 160 * len(linhas)))
    for i, l in enumerate(linhas):
        grade.paste(l, (0, i * 160))
    saida = dest / "grade_composta.png"
    grade.save(saida)
    log(f"validação -> {saida.relative_to(ROOT)}")
    return saida


def etapa_variantes(c: Comfy, estados: list[str], reforco: bool = False) -> None:
    """2 variações por estado a partir de assets/goxy.png (v1 aligned, v2 beside), sem tocar em <estado>.png.
    reforco=True: só a v3 (prompt com os lados explícitos, ref_boost 2.5)."""
    ref = c.upload(BASE_IMG, "goxys_base_ref.png")
    for est in estados:
        planos = [(3, "aligned (trained)")] if reforco else [(1, "aligned (trained)"), (2, "beside (right of target)")]
        for v, pos in planos:
            destino = FONTE / f"{est}_v{v}.png"
            if destino.exists():
                continue
            rot = f"variante {est} v{v}"
            prefixo = f"goxys/{est}_v{v}"
            c.esperar_fila_vazia()
            prontas = c.saidas_anteriores(prefixo, "29")
            try:
                saidas = {"29": {"images": prontas}} if prontas else c.executar(
                    grafo_krea2(ref, prompt_imagem(REFORCO[est] if v == 3 else {**AGENTES, **ESTADOS}[est], 1), semente(f"{est}-v", v),
                                prefixo, ref_position=pos, ref_boost=2.5 if v == 3 else 4.0),
                    TIMEOUT_IMG, rot, liberar=False)
            except ParadaSeguranca:
                raise
            except (TimeoutError, RuntimeError) as e:
                log(f"{rot}: falhou ({e}); seguindo")
                continue
            c.baixar(saidas["29"]["images"][0], destino)
            log(f"{rot}: borda verde {fundo_verde_ok(destino):.0%} -> {destino.name}")
        vs = [FONTE / f"{est}_v{i}.png" for i in (1, 2, 3)]
        folha_contato(vs, FONTE / f"folha_{est}.png", celula=256, colunas=len([x for x in vs if x.exists()]))


def escolher(est: str, variante: str) -> None:
    """Promove <estado>_<variante>.png a pose k1; aposenta pose e quadros-chave antigos; republica pôster/laço/manifest."""
    src = FONTE / f"{est}_{variante}.png"
    if not src.exists():
        raise SystemExit(f"não existe {src}")
    antigo = FONTE / "_antigo"
    antigo.mkdir(exist_ok=True)
    carimbo = dt.datetime.now().strftime("%H%M%S")
    for f in [FONTE / f"{est}.png", *FONTE.glob(f"{est}-k*.png")]:
        if f.exists():
            f.rename(antigo / f"{f.stem}_{carimbo}.png")
    shutil.copyfile(src, FONTE / f"{est}.png")
    if est in AGENTES:
        salvar_estatico(est, forcar=True)
        (OUT / f"idle-{est}.webm").unlink(missing_ok=True)
        animar_estatico(est, OUT / f"idle-{est}.webm")
        escrever_manifest()
        log(f"escolhido {est} = {variante} -> {(OUT / f'{est}.png').relative_to(ROOT)}")
        return
    for f in (OUT / f"estado-{est}.webm", OUT / f"estado-{est}.png", OUT / f"estado-{est}.webp"):
        f.unlink(missing_ok=True)
    publicar_parcial(est, forcar=True)
    animar_estatico(est, OUT / f"estado-{est}.webm")  # laço provisório até os quadros-chave
    escrever_manifest()
    log(f"escolhido {est} = {variante} -> {(OUT / f'estado-{est}.png').relative_to(ROOT)}")


def folha_contato(caminhos: list[Path], destino: Path, celula: int = 192, colunas: int = 4) -> None:
    """Grade pequena para revisão visual rápida (economiza leituras de imagem)."""
    caminhos = [c for c in caminhos if c.exists()]
    if not caminhos:
        return
    linhas = (len(caminhos) + colunas - 1) // colunas
    folha = Image.new("RGB", (celula * colunas, celula * linhas), "#808080")
    for i, c in enumerate(caminhos):
        im = Image.open(c).convert("RGBA")
        im.thumbnail((celula, celula))
        folha.paste(im, ((i % colunas) * celula, (i // colunas) * celula), im)
    destino.parent.mkdir(parents=True, exist_ok=True)
    folha.save(destino)


# ---------------------------------------------------------------- CLI


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("etapa", choices=["imagens", "variantes", "escolher", "reexportar", "upscale", "videos", "hero", "pos", "validar", "folha", "tudo"])
    ap.add_argument("--so", default="", help="lista separada por vírgula de itens")
    ap.add_argument("--refazer", default="", help="itens a regerar mesmo se existirem")
    ap.add_argument("--tentativa", type=int, default=1)
    ap.add_argument("--idle", action="store_true", help="também gera clipes idle por agente")
    ap.add_argument("--lado", type=int, default=LADO_VIDEO, help="lado do vídeo no MiniMax (múltiplo de 32)")
    ap.add_argument("--segundos", type=float, default=SEGUNDOS)
    ap.add_argument("--lado-webm", type=int, default=640)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--com-video", action="store_true", help="em 'tudo', também tenta o MiniMax (suspenso por padrão)")
    ap.add_argument("--estados-leves", action="store_true", help="estado sem clipe MiniMax ganha laço por quadros-chave (plano B)")
    ap.add_argument("--sem-idle-leve", action="store_true", help="não gera laços idle por quadros-chave (sem GPU) para os agentes")
    args = ap.parse_args()
    so = {s for s in args.so.split(",") if s} or None
    refazer = {s for s in args.refazer.split(",") if s}

    if args.etapa == "upscale":
        c = Comfy()
        alvos = sorted(so) if so else [*AGENTES, *ESTADOS] + [f"{e}-k{i}" for e in QUADROS_CHAVE for i in (2, 3)]
        try:
            feitos = etapa_upscale(c, alvos)
        except ParadaSeguranca as e:
            log(f"PARADA DE SEGURANÇA: {e}")
            sys.exit(3)
        c.liberar()
        log(f"upscale: {len(feitos)} imagens em {HD.relative_to(ROOT)}")
        return
    if args.etapa == "reexportar":
        etapa_reexportar(args.lado_webm, args.fps)
        return
    if args.etapa == "escolher":
        est, var = args.so.split(",")[:2]
        escolher(est, var)
        return
    if args.etapa == "variantes":
        c = Comfy()
        try:
            etapa_variantes(c, [e for e in (so or ESTADOS) if e in ESTADOS or e in AGENTES], reforco=args.tentativa >= 3)
        except ParadaSeguranca as e:
            log(f"PARADA DE SEGURANÇA: {e}. Nada mais será enviado ao ComfyUI.")
            sys.exit(3)
        c.liberar()
        return
    if args.etapa == "hero":
        c = Comfy()
        if os.environ.get("GOXYS_PERMITIR_VIDEO") != "1":
            log("hero (MiniMax H3) PROIBIDO: travou a máquina em 07/11. Para reativar conscientemente: GOXYS_PERMITIR_VIDEO=1")
        elif c.disponivel():
            etapa_hero(c)
        montar_hero()
        return
    if args.etapa in ("imagens", "videos", "tudo"):
        c = Comfy()
        if not c.disponivel():
            log("ComfyUI indisponível pela Tailscale; nada gerado (o painel usa o mascote estático)")
            sys.exit(2)
        if args.etapa in ("imagens", "tudo"):
            try:
                etapa_imagens(c, so, refazer, args.tentativa)
            except ParadaSeguranca as e:
                log(f"PARADA DE SEGURANÇA: {e}. Nada mais será enviado ao ComfyUI.")
                try:
                    c.http.post("/free", json={"unload_models": True, "free_memory": True}, timeout=10)
                except httpx.HTTPError:
                    pass
                sys.exit(3)
        if args.etapa == "videos" or (args.etapa == "tudo" and args.com_video):
            etapa_videos(c, so, args.idle, args.lado, args.segundos)
    if args.etapa in ("pos", "tudo"):
        etapa_pos(args.lado_webm, args.fps, not args.sem_idle_leve, args.estados_leves)
    if args.etapa in ("validar", "tudo"):
        etapa_validar(so)
    if args.etapa == "folha":
        nomes = sorted(so) if so else list(itens_imagem())
        folha_contato([FONTE / f"{n}.png" for n in nomes], FONTE / "folha.png")
        log(f"folha de contato -> {(FONTE / 'folha.png').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
