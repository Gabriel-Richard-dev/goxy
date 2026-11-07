"""Baixa fotos de CATALOGO dos produtos Gocase (gocase.com.br) e mapeia SKUs -> familia.

Fotos sao de catalogo, nunca evidencia de avaria. Idempotente: pula imagens ja salvas.
Uso: .venv/bin/python scripts/baixar_produtos.py
"""
import io, json, re, sqlite3, time, unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "assets" / "produtos"
DB = ROOT / "data" / "goxys.db"
BASE = "https://www.gocase.com.br"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_REQ = 60

# slug: (nome, busca, regex do href do produto)
FAMILIAS = {
    # No site, todos os modelos de case sao variacoes da mesma pagina "capinha-para-celular-*";
    # Slim Air/Standard/Infinite/Guard personalizadas usam a foto da case personalizada com nome.
    "case-personalizada": ("Case personalizada (nome/iniciais/foto)", "capinha iphone 16", r"capinha-para-celular-manuscrita"),
    "case-slim-air-vichy": ("Case Xadrez Vichy", "xadrez vichy", r"vichy"),
    "case-clear": ("Case Clear", "capinha iphone 16", r"capinha-para-celular-clear"),
    "copo-vibe": ("Copo Vibe 470 ml", "copo vibe", r"vibe"),
    "copo-life": ("Copo Life 1100 ml", "copo life", r"copo.*life|life.*1100"),
    "copo-daily": ("Copo Daily 600 ml", "copo daily", r"daily"),
    "garrafa-fresh": ("Garrafa Fresh", "garrafa fresh", r"fresh"),
    "garrafa-urban": ("Garrafa Urban 500 ml", "garrafa urban", r"garrafa.*urban"),
    "garrafa-mini": ("Garrafa Mini 350 ml", "garrafa mini", r"garrafa.*mini|mini.*350"),
    "connect-card": ("Connect Card Magsafe", "connect card", r"connect-card"),
    "necessaire-makeup-double": ("Necessaire Makeup Double", "necessaire makeup double", r"makeup|double"),
    "necessaire-voyage": ("Necessaire Voyage", "necessaire voyage", r"voyage"),
    "tote-bag-daily": ("Tote Bag Daily", "tote bag", r"/tote-bag|tote-daily|bolsa-tote"),
    "mini-tote": ("Mini Tote", "mini tote", r"mini-tote"),
    "bolsa-moove": ("Bolsa Moove", "bolsa moove", r"moove"),
    "mala-trip": ("Mala Trip 20\"", "mala trip", r"mala"),
}


def norm(s):
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()


def sku_familia(nome):
    n = norm(nome)
    rules = [
        ("slim air" in n and "vichy" in n, "case-slim-air-vichy"),
        ("vichy" in n, "case-slim-air-vichy"),
        (n.startswith("case") and "clear" in n, "case-clear"),
        (n.startswith("case"), "case-personalizada"),
        ("vibe" in n, "copo-vibe"), ("copo life" in n, "copo-life"), ("copo daily" in n, "copo-daily"),
        ("fresh" in n, "garrafa-fresh"), ("urban" in n, "garrafa-urban"), ("garrafa mini" in n, "garrafa-mini"),
        ("connect card" in n, "connect-card"), ("makeup" in n, "necessaire-makeup-double"),
        ("voyage" in n, "necessaire-voyage"), ("mini tote" in n, "mini-tote"),
        ("tote bag" in n, "tote-bag-daily"), ("moove" in n, "bolsa-moove"), ("mala trip" in n, "mala-trip"),
    ]
    return next((slug for ok, slug in rules if ok), None)


class Client:
    def __init__(self):
        self.c = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
        self.n = 0

    def get(self, url):
        if self.n >= MAX_REQ:
            raise RuntimeError("limite de requisicoes atingido")
        self.n += 1
        time.sleep(0.5)
        return self.c.get(url)


def og_image(html):
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html) or \
        re.search(r'content="([^"]+)"[^>]+property="og:image"', html)
    return m.group(1) if m else None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "manifest.json"
    old = json.loads(mpath.read_text()).get("familias", {}) if mpath.exists() else {}
    cli, fams = Client(), {}
    for slug, (nome, q, rx) in FAMILIAS.items():
        dst = OUT / f"{slug}.webp"
        if dst.exists():
            fams[slug] = old.get(slug, {"img": f"/assets-goxys/produtos/{slug}.webp", "nome": nome, "url_origem": None})
            continue
        try:
            html = cli.get(f"{BASE}/search?q={q.replace(' ', '+')}").text
            links = list(dict.fromkeys(re.findall(r'href="(/[^"?#]+/p)"', html)))
            hit = next((l for l in links if re.search(rx, l) and "base-de-silicone" not in l), None)
            if not hit:
                print(f"[falta] {slug}: nenhum produto na busca"); continue
            url = BASE + hit
            img = og_image(cli.get(url).text)
            if not img:
                print(f"[falta] {slug}: sem og:image em {url}"); continue
            im = Image.open(io.BytesIO(cli.get(img).content)).convert("RGB")
            im.thumbnail((600, 600))
            im.save(dst, "WEBP", quality=82)
            fams[slug] = {"img": f"/assets-goxys/produtos/{slug}.webp", "nome": nome, "url_origem": url}
            print(f"[ok] {slug} <- {url}")
        except Exception as e:
            print(f"[erro] {slug}: {e}")
    skus = {}
    for sku, nome in sqlite3.connect(DB).execute("select sku, nome from produtos where marca='Gocase'"):
        s = sku_familia(nome)
        if s in fams:
            skus[sku] = s
        else:
            print(f"[sem foto] {sku} {nome}")
    mpath.write_text(json.dumps({"gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "aviso": "Fotos de catalogo; nunca evidencia de avaria.",
                                 "familias": fams, "skus": skus}, ensure_ascii=False, indent=2))
    print(f"familias={len(fams)} skus={len(skus)} requisicoes={cli.n}")


if __name__ == "__main__":
    main()
