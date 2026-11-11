#!/usr/bin/env python3
"""Motor de render do editor do Zaza.

Lê um projeto.json (formato em LEIA-ME.md) e gera o vídeo final com ffmpeg.
Legendas e balão são desenhados com o mesmo CSS do editor (estilo.css) pelo
Chromium, viram PNG transparente e entram por cima do vídeo.

Uso:
  python3 render.py projeto.json                 # vídeo final (1080x1920)
  python3 render.py projeto.json --rascunho      # rápido, 540x960, pra conferir
  python3 render.py projeto.json --info          # só mostra a linha do tempo
  python3 render.py projeto.json --quadros 1.2 5 # depois do render, tira JPGs nesses tempos
  python3 render.py projeto.json --saida x.mp4
"""
import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
FONTE = os.path.join(RAIZ, "fontes", "Montserrat.ttf")
CSS = os.path.join(AQUI, "estilo.css")
IMAGENS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# chave do projeto.json -> variável CSS
VARS_LEGENDA = {
    "y": "--leg-y", "largura": "--leg-largura", "tam_en": "--tam-en", "tam_pt": "--tam-pt",
    "cor_en": "--cor-en", "cor_pt": "--cor-pt", "contorno_en": "--contorno-en",
    "contorno_pt": "--contorno-pt", "espaco": "--leg-espaco",
    "fonte_en": "--fonte-en", "fonte_pt": "--fonte-pt",
}
VARS_BALAO = {
    "y": "--balao-y", "largura": "--balao-largura", "tam": "--balao-tam",
    "fundo": "--balao-fundo", "cor": "--balao-cor", "fonte": "--balao-fonte",
}
SEM_PX = {"cor_en", "cor_pt", "fundo", "cor"}


class ErroProjeto(Exception):
    pass


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- leitura

def ler_projeto(caminho):
    with open(caminho, encoding="utf-8") as f:
        p = json.load(f)
    base = os.path.dirname(os.path.abspath(caminho))
    fmt = {"largura": 1080, "altura": 1920, "fps": 30, "lufs": -14}
    fmt.update(p.get("formato", {}))
    p["formato"] = fmt
    midia, layout = {}, {}
    for nome, rel in p.get("midia", {}).items():
        if isinstance(rel, dict):  # {"arquivo": ..., "empilhar": [{x,y,w,h}, ...]}
            if rel.get("empilhar"):
                for r_ in rel["empilhar"]:
                    if not all(k in r_ for k in ("x", "y", "w", "h")):
                        raise ErroProjeto(f"midia '{nome}': cada recorte de 'empilhar' precisa de x, y, w, h")
                layout[nome] = rel["empilhar"]
            rel = rel.get("arquivo", "")
        cam = rel if os.path.isabs(rel) else os.path.join(base, rel)
        if not os.path.exists(cam):
            raise ErroProjeto(f"midia '{nome}' não existe: {cam}")
        midia[nome] = cam
    p["_midia"] = midia
    p["_layout"] = layout
    p["_base"] = base
    validar(p)
    return p


def sondar(cam):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
         "-of", "json", cam], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    tipos = [s["codec_type"] for s in j.get("streams", [])]
    dur = float(j.get("format", {}).get("duration") or 0)
    return {"audios": tipos.count("audio"), "video": "video" in tipos, "duracao": dur}


def validar(p):
    if not p.get("trechos"):
        raise ErroProjeto("projeto sem trechos")
    sond = {}
    for i, t in enumerate(p["trechos"]):
        nome = t.get("midia")
        if nome not in p["_midia"]:
            raise ErroProjeto(f"trecho {i}: midia '{nome}' não está em 'midia'")
        cam = p["_midia"][nome]
        if cam not in sond:
            sond[cam] = sondar(cam)
        info = sond[cam]
        t["_info"] = info
        img = os.path.splitext(cam)[1].lower() in IMAGENS
        t["_imagem"] = img
        if img:
            t.setdefault("de", 0)
            if "duracao" not in t and "ate" not in t:
                raise ErroProjeto(f"trecho {i}: imagem precisa de 'duracao'")
            t["_dur"] = float(t.get("duracao", t.get("ate", 0) - t.get("de", 0)))
        else:
            de, ate = float(t["de"]), float(t["ate"])
            if ate <= de:
                raise ErroProjeto(f"trecho {i}: 'ate' ({ate}) tem que ser maior que 'de' ({de})")
            if info["duracao"] and ate > info["duracao"] + 0.05:
                log(f"aviso: trecho {i} vai até {ate}s mas '{nome}' tem {info['duracao']:.2f}s (completa congelando o último quadro)")
            t["_dur"] = ate - de
        for f in t.get("faixas", []):
            if f.get("faixa", 0) >= info["audios"]:
                raise ErroProjeto(f"trecho {i}: faixa {f.get('faixa')} não existe ('{nome}' tem {info['audios']} faixa(s) de áudio)")
    # duração de cada trecho em quadros inteiros: vídeo e áudio do trecho com o mesmo tamanho,
    # senão a diferença (até meio quadro por corte) soma e o áudio escorrega do vídeo com muitos cortes
    F = p["formato"]["fps"]
    for t in p["trechos"]:
        t["_dur"] = max(1, round(t["_dur"] * F)) / F
    total = 0.0
    for t in p["trechos"]:
        t["_ini"] = total
        total += t["_dur"]
    p["_total"] = total
    ant = None
    for i, l in enumerate(p.get("legendas", [])):
        if l["ate"] <= l["de"]:
            raise ErroProjeto(f"legenda {i}: 'ate' tem que ser maior que 'de'")
        if l["de"] > total:
            log(f"aviso: legenda {i} começa em {l['de']}s, depois do fim do vídeo ({total:.2f}s)")
        if ant and l["de"] < ant["ate"] - 0.001 and l.get("pista", 0) == ant.get("pista", 0):
            log(f"aviso: legenda {i} começa antes da anterior acabar ({l['de']} < {ant['ate']})")
        ant = l
    for i, s in enumerate(p.get("sfx", [])):
        if s.get("midia") not in p["_midia"]:
            raise ErroProjeto(f"sfx {i}: midia '{s.get('midia')}' não está em 'midia'")
    for i, o in enumerate(p.get("sobre", [])):
        if o.get("midia") not in p["_midia"]:
            raise ErroProjeto(f"sobre {i}: midia '{o.get('midia')}' não está em 'midia'")
        cam = p["_midia"][o["midia"]]
        if cam not in sond:
            sond[cam] = sondar(cam)
        o["_info"] = sond[cam]
        o["_imagem"] = os.path.splitext(cam)[1].lower() in IMAGENS
        o["_dur"] = float(o.get("duracao", 2)) if o["_imagem"] else float(o["ate"]) - float(o.get("de", 0))
        if o["_dur"] <= 0:
            raise ErroProjeto(f"sobre {i}: duração tem que ser maior que zero")
    m = p.get("musica")
    if m and m.get("midia") not in p["_midia"]:
        raise ErroProjeto(f"musica: midia '{m.get('midia')}' não está em 'midia'")


def info(p):
    log(f"duração final: {p['_total']:.2f}s  ({p['formato']['largura']}x{p['formato']['altura']} {p['formato']['fps']}fps)")
    for i, t in enumerate(p["trechos"]):
        extra = []
        if t.get("encaixe"):
            extra.append(t["encaixe"])
        if t.get("mudo"):
            extra.append("mudo")
        if t.get("zoom"):
            extra.append(f"zoom {t['zoom']}")
        orig = f"{t.get('de', 0):.2f}–{t.get('de', 0) + t['_dur']:.2f}"
        log(f"  trecho {i}: {t['midia']} {orig}  ->  final {t['_ini']:.2f}–{t['_ini'] + t['_dur']:.2f}  {' '.join(extra)}")
    log(f"  {len(p.get('legendas', []))} legendas, {len(p.get('sfx', []))} sfx, balão: {'sim' if p.get('balao') else 'não'}")


# ---------------------------------------------------------------- textos

def css_vars(estilo, mapa):
    partes = []
    for k, v in (estilo or {}).items():
        if k in mapa:
            if k.startswith("fonte"):
                val = "'" + str(v).replace('"', '').replace("'", "") + "'"
            else:
                val = v if (k in SEM_PX or isinstance(v, str)) else f"{v}px"
            partes.append(f"{mapa[k]}:{val}")
    return ";".join(partes)


def html_textos(p):
    """Devolve [(inicio, fim, html_do_item)] para cada legenda e balão."""
    est = p.get("estilo", {})
    itens = []
    for l in p.get("legendas", []):
        if l.get("desativado"):
            continue
        st = css_vars({**est.get("legenda", {}), **l.get("estilo", {})}, VARS_LEGENDA)
        linhas = ""
        if l.get("en"):
            linhas += f'<div class="en">{html.escape(l["en"])}</div>'
        if l.get("pt"):
            linhas += f'<div class="pt">{html.escape(l["pt"])}</div>'
        itens.append((l["de"], l["ate"], f'<div class="legenda" style="{st}">{linhas}</div>'))
    b = p.get("balao")
    if b and b.get("texto") and not b.get("desativado"):
        st = css_vars({**est.get("balao", {}), **b.get("estilo", {})}, VARS_BALAO)
        cls = "balao quebra" if b.get("quebra") else "balao"
        itens.append((b.get("de", 0), b.get("ate", 3.5), f'<div class="{cls}" style="{st}">{html.escape(b["texto"])}</div>'))
    return itens


def desenhar_textos(p, pasta):
    """Desenha a camada de textos como UMA sequência de PNGs (um por intervalo em que
    o conjunto de textos visíveis não muda) e devolve o caminho de uma lista ffconcat,
    ou None se não tem texto. Uma camada só = um overlay só no ffmpeg (bem mais rápido)."""
    itens = html_textos(p)
    if not itens:
        return None
    from playwright.sync_api import sync_playwright
    W, H, F = p["formato"]["largura"], p["formato"]["altura"], p["formato"]["fps"]
    total = p["_total"]
    q = lambda t: round(round(float(t) * F) / F, 6)  # alinha no quadro
    pontos = sorted({0.0, q(total)} | {min(q(i), q(total)) for i, _, _ in itens} | {min(q(f), q(total)) for _, f, _ in itens})
    css = open(CSS, encoding="utf-8").read()
    pasta_f = os.path.join(RAIZ, "fontes")
    fontes_css = open(os.path.join(pasta_f, "fontes.css"), encoding="utf-8").read().replace('url("', 'url("file://' + pasta_f + '/')
    fontes_css += '@font-face { font-family: "Noto Color Emoji"; src: local("Noto Color Emoji"); }\n'
    lista = ["ffconcat version 1.0"]
    cache = {}
    with sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        pg = nav.new_page(viewport={"width": W, "height": H})
        # documento em arquivo (file://) pra ele poder carregar as fontes locais; só o miolo muda por quadro
        doc = f"""<!doctype html><html><head><meta charset="utf-8"><style>
{fontes_css}
html,body {{ margin:0; background: transparent; }}
{css}
.quadro {{ width:{W}px; height:{H}px; }}
</style></head><body><div class="quadro" id="q"></div></body></html>"""
        cam_doc = os.path.join(pasta, "textos.html")
        with open(cam_doc, "w", encoding="utf-8") as f:
            f.write(doc)
        pg.goto("file://" + cam_doc)
        faltou = pg.evaluate("""async () => {
            const faces = [...document.fonts];
            await Promise.all(faces.map(f => f.load().catch(() => null)));
            return faces.filter(f => f.status !== 'loaded').map(f => f.family + ' ' + f.weight);
        }""")
        if faltou:
            log("aviso: fontes que não carregaram: " + ", ".join(faltou))
        for a, b in zip(pontos, pontos[1:]):
            if b - a <= 0:
                continue
            meio = (a + b) / 2
            corpo = "".join(h for i, f, h in itens if q(i) <= meio < q(f))
            if corpo not in cache:
                png = os.path.join(pasta, f"textos_{len(cache):03d}.png")
                pg.evaluate("async (h) => { document.getElementById('q').innerHTML = h; await document.fonts.ready; await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); }", corpo)
                pg.screenshot(path=png, omit_background=True, clip={"x": 0, "y": 0, "width": W, "height": H})
                cache[corpo] = png
            lista.append(f"file '{cache[corpo]}'")
            lista.append(f"duration {b - a:.6f}")
        nav.close()
    lista.append(f"file '{cache[corpo]}'")  # o concat ignora a duração do último
    cam = os.path.join(pasta, "textos.ffconcat")
    with open(cam, "w") as f:
        f.write("\n".join(lista) + "\n")
    log(f"camada de textos: {len(cache)} imagens, {len(pontos) - 1} intervalos")
    return cam


# ---------------------------------------------------------------- ffmpeg

def empilhar_filtro(recortes, W, H, rot):
    """Filtro que recorta cada região e empilha uma embaixo da outra em W x H (sem rótulos de entrada/saída)."""
    n = len(recortes)
    if n == 1:
        c = recortes[0]
        return f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    alturas = [H // n] * n
    alturas[-1] = H - sum(alturas[:-1])
    partes = [f"split={n}" + "".join(f"[{rot}s{i}]" for i in range(n))]
    for i, c in enumerate(recortes):
        partes.append(f"[{rot}s{i}]crop={c['w']}:{c['h']}:{c['x']}:{c['y']},"
                      f"scale={W}:{alturas[i]}:force_original_aspect_ratio=increase,crop={W}:{alturas[i]}[{rot}c{i}]")
    partes.append("".join(f"[{rot}c{i}]" for i in range(n)) + f"vstack=inputs={n},setsar=1")
    return ";".join(partes)


def par(n):
    n = int(math.ceil(n))
    return n + (n % 2)


def montar(p, textos, rascunho, so_trecho=None, prontos=None):
    """Grafo inteiro. so_trecho=k: só o trecho k (vídeo+áudio prontos, pra pré-render).
    prontos=lista ffconcat dos trechos já prontos: entra como uma entrada só, no lugar do concat."""
    W, H, F = p["formato"]["largura"], p["formato"]["altura"], p["formato"]["fps"]
    if rascunho:  # tudo em meia resolução desde o começo
        W, H = W // 2, H // 2
    entradas, filtros = [], []
    vs_as = []

    if prontos:
        entradas.append(["-f", "concat", "-safe", "0", "-i", prontos])
        filtros.append("[0:v]setpts=PTS-STARTPTS[vc]")
        filtros.append("[0:a]asetpts=PTS-STARTPTS,aresample=48000[ac]")
    for k, t in enumerate(p["trechos"]):
        if prontos or (so_trecho is not None and k != so_trecho):
            continue
        cam = p["_midia"][t["midia"]]
        dur = t["_dur"]
        idx = len(entradas)
        if t["_imagem"]:
            entradas.append(["-loop", "1", "-framerate", str(F), "-t", f"{dur:.3f}", "-i", cam])
        else:
            entradas.append(["-threads", "1", "-ss", f"{float(t['de']):.3f}", "-t", f"{dur:.3f}", "-i", cam])  # 1 thread por entrada: menos memória
        z = float(t.get("zoom", 1) or 1)
        cw, ch = par(W * z), par(H * z)
        cobrir = f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={W}:{H}"
        pre = f"[{idx}:v]setpts=PTS-STARTPTS,fps={F}"
        if t["midia"] in p["_layout"]:  # bruto do OmeTV: recorta as câmeras e empilha em pé, só neste trecho
            filtros.append(f"{pre},{empilhar_filtro(p['_layout'][t['midia']], W, H, f'e{k}')}[emp{k}]")
            pre = f"[emp{k}]null"
        enc = t.get("encaixe", "cobrir")
        if enc == "borrado":
            filtros.append(f"{pre},split[bg{k}][fg{k}]")
            filtros.append(f"[bg{k}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma={20 if rascunho else 40},eq=brightness=-0.08[bgb{k}]")
            filtros.append(f"[fg{k}]scale={par(W * z)}:{par(H * z)}:force_original_aspect_ratio=decrease[fgs{k}]")
            filtros.append(f"[bgb{k}][fgs{k}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p,"
                           f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f}[v{k}]")
        else:
            if enc == "conter":
                esc = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
            else:
                esc = cobrir
            filtros.append(f"{pre},{esc},setsar=1,format=yuv420p,"
                           f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f}[v{k}]")

        info = t["_info"]
        fade = min(0.012, dur / 4)
        bordas = f"afade=t=in:d={fade:.3f},afade=t=out:st={dur - fade:.3f}:d={fade:.3f}"
        if t.get("mudo") or info["audios"] == 0 or t["_imagem"]:
            filtros.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{k}]")
        else:
            base = float(t.get("volume", 1.0))  # volume do trecho multiplica o de cada faixa
            faixas = t.get("faixas") or [{"faixa": 0}]
            nomes = []
            for j, fx in enumerate(faixas):
                nm = f"a{k}_{j}"
                filtros.append(f"[{idx}:a:{fx.get('faixa', 0)}]asetpts=PTS-STARTPTS,aresample=48000,"
                               f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={fx.get('volume', 1.0) * base:.4f}[{nm}]")
                nomes.append(f"[{nm}]")
            mix = "".join(nomes) + (f"amix=inputs={len(nomes)}:normalize=0," if len(nomes) > 1 else "anull,")
            filtros.append(f"{mix}apad,atrim=duration={dur:.3f},{bordas}[a{k}]")
        vs_as.append(f"[v{k}][a{k}]")

    if so_trecho is not None:
        filtros.append(f"[v{so_trecho}]null[vf]")
        filtros.append(f"[a{so_trecho}]anull[af]")
        return entradas, filtros
    if not prontos:
        n = len(p["trechos"])
        filtros.append("".join(vs_as) + f"concat=n={n}:v=1:a=1[vc][ac]")

    # clipes por cima (camadas): pista maior fica por cima; texto vai por cima de tudo
    atual = "vc"
    sobre = sorted((o for o in p.get("sobre", []) if not o.get("desativado")), key=lambda o: o.get("pista", 0))
    aud_sobre = []
    for j, o in enumerate(sobre):
        cam = p["_midia"][o["midia"]]
        dur, em = o["_dur"], float(o.get("em", 0))
        idx = len(entradas)
        if o["_imagem"]:
            entradas.append(["-loop", "1", "-framerate", str(F), "-t", f"{dur:.3f}", "-i", cam])
        else:
            entradas.append(["-threads", "1", "-ss", f"{float(o.get('de', 0)):.3f}", "-t", f"{dur:.3f}", "-i", cam])
        k = W / p["formato"]["largura"]
        larg = par(float(o.get("largura", p["formato"]["largura"])) * k)
        pre = f"[{idx}:v]setpts=PTS-STARTPTS,fps={F}"
        if o["midia"] in p["_layout"]:
            filtros.append(f"{pre},{empilhar_filtro(p['_layout'][o['midia']], W, H, f'so{j}')}[soe{j}]")
            pre = f"[soe{j}]null"
        filtros.append(f"{pre},scale={larg}:-2,format=yuva420p,setpts=PTS+{em:.3f}/TB[so{j}]")
        x = f"{float(o.get('x', 0)) * k:.1f}"
        y = f"{float(o['y']) * k:.1f}" if o.get("y") is not None else "(H-h)/2"
        filtros.append(f"[{atual}][so{j}]overlay={x}:{y}:eof_action=pass:enable='between(t,{em:.3f},{em + dur:.3f})'[vso{j}]")
        atual = f"vso{j}"
        if not (o.get("mudo") or o["_imagem"] or o["_info"]["audios"] == 0):
            fx = (o.get("faixas") or [{"faixa": 0}])[0].get("faixa", 0)
            filtros.append(f"[{idx}:a:{fx}]asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                           f"atrim=duration={dur:.3f},volume={float(o.get('volume', 1.0)):.4f},adelay={int(round(em * 1000))}:all=1[sa{j}]")
            aud_sobre.append(f"[sa{j}]")

    # textos por cima
    if textos:
        idx = len(entradas)
        entradas.append(["-f", "concat", "-safe", "0", "-i", textos])
        esc_txt = f",scale={W}:{H}" if rascunho else ""
        filtros.append(f"[{idx}:v]fps={F}{esc_txt},format=yuva420p[txt]")
        filtros.append(f"[{atual}][txt]overlay=0:0:eof_action=pass:format=yuv420[vt]")
        atual = "vt"
    filtros.append(f"[{atual}]null[vf]")

    # sfx e música
    mixes = ["[ac]"] + aud_sobre
    for j, s in enumerate(x for x in p.get("sfx", []) if not x.get("desativado")):
        idx = len(entradas)
        entradas.append(["-i", p["_midia"][s["midia"]]])
        ms = int(round(float(s["em"]) * 1000))
        ini = float(s.get("inicio", 0) or 0)  # de onde o som começa (aparado pela borda esquerda)
        if s.get("duracao"):
            corte = f"atrim=start={ini:.3f}:end={ini + float(s['duracao']):.3f},asetpts=PTS-STARTPTS,"
        elif ini:
            corte = f"atrim=start={ini:.3f},asetpts=PTS-STARTPTS,"
        else:
            corte = ""
        filtros.append(f"[{idx}:a]{corte}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"volume={s.get('volume', 1.0)},adelay={ms}:all=1[s{j}]")
        mixes.append(f"[s{j}]")
    m = p.get("musica")
    if m:
        idx = len(entradas)
        entradas.append(["-stream_loop", "-1", "-i", p["_midia"][m["midia"]]])
        tot = p["_total"]
        filtros.append(f"[{idx}:a]atrim=start={float(m.get('de', 0)):.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                       f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={m.get('volume', 0.15)},"
                       f"atrim=duration={tot:.3f},afade=t=out:st={max(0, tot - 1.0):.3f}:d=1[mus]")
        mixes.append("[mus]")
    if len(mixes) > 1 and p.get("ducking", True):
        # ducking: efeito, música e som dos clipes por cima abaixam sozinhos quando tem fala no vídeo.
        # Aqui só separa: fala sai em [af] e efeitos em [efx]; o sidechain é feito depois, lendo do
        # arquivo (dentro deste grafo grande, com 20+ entradas, ele travava/estourava memória - 10/11).
        filtros.append("[ac]anull[af]")
        filtros.append("".join(mixes[1:]) + f"amix=inputs={len(mixes) - 1}:normalize=0:duration=longest,apad[efx]")
        p["_duck"] = True
    elif len(mixes) > 1:
        filtros.append("".join(mixes) + f"amix=inputs={len(mixes)}:normalize=0:duration=first[af]")
    else:
        filtros.append("[ac]anull[af]")
    return entradas, filtros


def medir_lufs(cam):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", cam, "-vn",
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    return json.loads(m.group(0)) if m else None


LIMITE_TRECHOS = 6  # acima disso cada trecho é renderizado sozinho antes (um decodificador por vez)


def pre_render_trechos(p, rascunho, tmp):
    """Com muitos cortes, abrir um decodificador por trecho no mesmo ffmpeg estoura os 2 GB
    (iraniana, 20 cortes, 10/11). Aqui cada trecho sai pronto (recorte, zoom, encaixe, volume,
    faixas) num arquivo intermediário, um de cada vez, e o grafo final lê todos como uma entrada só."""
    F = p["formato"]["fps"]
    lista = ["ffconcat version 1.0"]
    t0 = time.time()
    for k, t in enumerate(p["trechos"]):
        entradas, filtros = montar(p, None, rascunho, so_trecho=k)
        script = os.path.join(tmp, f"trecho_{k:03d}.txt")
        with open(script, "w") as f:
            f.write(";\n".join(filtros))
        out = os.path.join(tmp, f"trecho_{k:03d}.mkv")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "2"]
        for e in entradas:
            cmd += e
        cmd += ["-filter_complex_script", script, "-map", "[vf]", "-map", "[af]",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24" if rascunho else "12", "-threads", "2",
                "-pix_fmt", "yuv420p", "-r", str(F), "-g", str(F),
                "-c:a", "pcm_f32le", "-ar", "48000", "-ac", "2", "-t", f"{t['_dur']:.3f}", out]
        r = subprocess.run(cmd)
        if r.returncode != 0:
            raise ErroProjeto(f"ffmpeg falhou no pré-render do trecho {k}")
        lista += [f"file '{out}'", f"duration {t['_dur']:.6f}"]
    cam = os.path.join(tmp, "trechos.ffconcat")
    with open(cam, "w") as f:
        f.write("\n".join(lista) + "\n")
    log(f"{len(p['trechos'])} trechos prontos um a um ({time.time() - t0:.1f}s)")
    return cam


def render(p, saida, rascunho):
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="render_")
    try:
        textos = desenhar_textos(p, tmp)
        log(f"textos prontos ({time.time() - t0:.1f}s)")
        pre = None
        if len(p["trechos"]) > LIMITE_TRECHOS:
            pre = pre_render_trechos(p, rascunho, tmp)
        entradas, filtros = montar(p, textos, rascunho, prontos=pre)
        script = os.path.join(tmp, "grafo.txt")
        with open(script, "w") as f:
            f.write(";\n".join(filtros))
        bruto = os.path.join(tmp, "sem_loudnorm.mp4")
        F = p["formato"]["fps"]
        # a máquina dos agentes tem 2 GB pra todos: limita threads e buffers do ffmpeg
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats", "-filter_complex_threads", "2"]
        for e in entradas:
            cmd += e
        cmd += ["-filter_complex_script", script, "-map", "[vf]", "-map", "[af]"] + (["-map", "[efx]"] if p.get("_duck") else []) + [
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-x264-params", "rc-lookahead=10",
                "-crf", "28" if rascunho else "18", "-pix_fmt", "yuv420p", "-r", str(F),
                "-c:a", "pcm_f32le", "-t", f"{p['_total']:.3f}", bruto.replace(".mp4", ".mkv")]
        bruto = bruto.replace(".mp4", ".mkv")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            shutil.copy(script, os.path.join(os.path.dirname(os.path.abspath(saida)), "grafo_com_erro.txt"))
            raise ErroProjeto("ffmpeg falhou (grafo salvo em grafo_com_erro.txt)")
        log(f"vídeo montado ({time.time() - t0:.1f}s)")

        # volume final: ganho fixo + limitador de pico, medido e corrigido. O loudnorm "linear" do ffmpeg
        # cai sozinho no modo dinâmico quando o pico ou a faixa dinâmica não deixam, e aí entregava
        # -16,4 em vez de -14 (iraniana, 10/11). Aqui: ganho = alvo - medido, limita em -1,5 dBTP,
        # mede de novo e completa o que o limitador comeu (até 3 passadas, só o áudio).
        alvo = p["formato"]["lufs"]
        fonte = bruto
        if p.get("_duck"):  # fala (faixa 0) abaixa os efeitos (faixa 1) e as duas se juntam
            fonte = os.path.join(tmp, "mix_ducking.wav")
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", bruto, "-filter_complex",
                            "[0:a:0]asplit=2[voz][chave];[0:a:1][chave]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=250[efxd];"
                            "[voz][efxd]amix=inputs=2:normalize=0:duration=first[m]", "-map", "[m]", "-c:a", "pcm_f32le", fonte], check=True)
        med = medir_lufs(fonte)
        teto = 10 ** (-2.5 / 20)  # alimiter olha pico de amostra (e o AAC sobe ~0,5): 1 dB de folga pro pico real ficar < -1,5
        ganho = (alvo - float(med["input_i"])) if med and med["input_i"] not in ("-inf", "inf") else 0.0
        wav = os.path.join(tmp, "audio_final.wav")
        for passada in range(3):
            af = (f"volume={ganho:.2f}dB,alimiter=limit={teto:.4f}:attack=5:release=50:level=false,"
                  "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo")
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", fonte, "-vn", "-af", af,
                            "-c:a", "pcm_f32le", wav], check=True)
            m = medir_lufs(wav)
            if not m or m["input_i"] in ("-inf", "inf"):
                break
            erro = alvo - float(m["input_i"])
            if abs(erro) <= 0.3:
                break
            ganho += erro
        cmd2 = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", bruto, "-i", wav, "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", saida]
        subprocess.run(cmd2, check=True)
        fim = medir_lufs(saida)
        log(f"pronto: {saida}  ({time.time() - t0:.1f}s, {fim['input_i'] if fim else '?'} LUFS)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def quadros(saida, tempos):
    base = os.path.splitext(saida)[0]
    feitos = []
    for t in tempos:
        jpg = f"{base}_quadro_{t:06.2f}.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", saida,
                        "-frames:v", "1", "-q:v", "3", jpg], check=True)
        feitos.append(jpg)
    log("quadros: " + ", ".join(feitos))


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from atualizar import atualizar
    atualizar(__file__)
    ap = argparse.ArgumentParser(description="Render do editor do Zaza")
    ap.add_argument("projeto")
    ap.add_argument("--saida")
    ap.add_argument("--rascunho", action="store_true")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--quadros", nargs="*", type=float, default=[])
    a = ap.parse_args()
    try:
        p = ler_projeto(a.projeto)
        info(p)
        if a.info:
            return
        nome = p.get("nome") or os.path.splitext(os.path.basename(a.projeto))[0]
        saida = a.saida or os.path.join(p["_base"], f"{nome}{'_rascunho' if a.rascunho else ''}.mp4")
        render(p, saida, a.rascunho)
        if a.quadros:
            quadros(saida, a.quadros)
    except ErroProjeto as e:
        log(f"ERRO: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
