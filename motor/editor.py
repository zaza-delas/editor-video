#!/usr/bin/env python3
"""Gera a página do editor (um .html só) a partir de um projeto.json.

A página embute uma cópia leve (proxy) só dos pedaços das mídias que a edição usa,
com folga de cada lado, além dos SFX. Abre no chat do King Bot ou em qualquer navegador.

Uso:
  python3 editor.py projeto.json [--saida editor.html] [--folga 1.5]
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from array import array

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from render import ler_projeto, sondar, ErroProjeto, IMAGENS, log, empilhar_filtro  # noqa: E402

MODELO = os.path.join(AQUI, "editor_modelo.html")
CSS = os.path.join(AQUI, "estilo.css")
CAMPOS = ["nome", "formato", "midia", "trechos", "legendas", "balao", "sfx", "sobre", "musica", "estilo", "ducking", "roteiro"]


def limpo(p):
    """Só os campos do formato, sem os internos (_algo)."""
    def tira(o):
        if isinstance(o, dict):
            return {k: tira(v) for k, v in o.items() if not k.startswith("_")}
        if isinstance(o, list):
            return [tira(v) for v in o]
        return o
    return {k: tira(p[k]) for k in CAMPOS if k in p}


def b64(cam, mime):
    with open(cam, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def juntar(faixas):
    faixas.sort()
    out = []
    for a, b in faixas:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def proxy_video(cam, faixas, faixas_audio, pasta, nome, layout=None):
    """Codifica os pedaços [a,b] num MP4 leve e devolve (dataurl, mapa)."""
    partes, mapa, off = [], [], 0.0
    info = sondar(cam)
    for n, (a, b) in enumerate(faixas):
        out = os.path.join(pasta, f"{nome}_{n}.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", cam]
        vf = "scale=360:640:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30"
        if layout:
            vf = empilhar_filtro(layout, 360, 640, "p") + ",fps=30"
        if info["audios"] == 0:
            cmd += ["-f", "lavfi", "-t", f"{b - a:.3f}", "-i", "anullsrc=r=44100:cl=mono",
                    "-map", "0:v", "-map", "1:a"]
            af = None
        elif len(faixas_audio) > 1:
            ins = "".join(f"[0:a:{f}]" for f in faixas_audio)
            cmd += ["-filter_complex", f"{ins}amix=inputs={len(faixas_audio)}:normalize=0[am]", "-map", "0:v", "-map", "[am]"]
            af = None
        else:
            cmd += ["-map", "0:v", "-map", f"0:a:{faixas_audio[0] if faixas_audio else 0}"]
            af = None
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "33", "-g", "10",
                "-profile:v", "main", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "40k", "-ac", "1", "-ar", "44100", "-shortest", out]
        subprocess.run(cmd, check=True)
        partes.append(out)
    lista = os.path.join(pasta, f"{nome}.txt")
    with open(lista, "w") as f:
        f.write("".join(f"file '{x}'\n" for x in partes))
    final = os.path.join(pasta, f"{nome}.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lista,
                    "-c", "copy", "-movflags", "+faststart", final], check=True)
    for (a, b), parte in zip(faixas, partes):
        d = sondar(parte)["duracao"]
        mapa.append({"a": round(a, 3), "b": round(a + d, 3), "o": round(off, 3)})
        off += d
    mini, nm = miniaturas(final, pasta, nome)
    return b64(final, "video/mp4"), mapa, os.path.getsize(final) + nm, mini, picos(final)


def picos(cam, por_seg=40, maximo=None):
    """Envelope do áudio (0-100), por_seg valores por segundo, pra desenhar a onda."""
    cmd = ["ffmpeg", "-v", "error", "-i", cam]
    if maximo:
        cmd += ["-t", str(maximo)]
    cmd += ["-vn", "-ac", "1", "-ar", str(por_seg * 50), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    v = [max((abs(x) for x in a[i:i + 50]), default=0) for i in range(0, len(a), 50)]
    if not v:
        return []
    ref = sorted(v)[int(len(v) * 0.98)] or 1
    return [min(100, round(x * 100 / ref)) for x in v]


def miniaturas(cam, pasta, nome, por_seg=2):
    """Tira de miniaturas (36x64) do proxy, uma a cada 1/por_seg s, numa imagem só."""
    dur = sondar(cam)["duracao"]
    n = max(1, int(dur * por_seg + 0.999))
    out = os.path.join(pasta, f"{nome}_mini.jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", cam, "-vf",
                    f"fps={por_seg},scale=36:64:force_original_aspect_ratio=increase,crop=36:64,tile={n}x1",
                    "-frames:v", "1", "-q:v", "7", out], check=True)
    return {"src": b64(out, "image/jpeg"), "n": n, "por_seg": por_seg}, os.path.getsize(out)


def proxy_audio(cam, pasta, nome, maximo=None):
    out = os.path.join(pasta, f"{nome}.mp3")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", cam]
    if maximo:
        cmd += ["-t", str(maximo)]
    cmd += ["-vn", "-ac", "1", "-ar", "44100", "-b:a", "64k", out]
    subprocess.run(cmd, check=True)
    return b64(out, "audio/mpeg"), os.path.getsize(out)


def proxy_imagem(cam, pasta, nome):
    out = os.path.join(pasta, f"{nome}.jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", cam, "-vf",
                    "scale=540:960:force_original_aspect_ratio=decrease", "-q:v", "5", out], check=True)
    return b64(out, "image/jpeg"), os.path.getsize(out)


def montar_dados(caminho, folga=1.5):
    """{"projeto": ..., "midias": ...} com a mídia leve embutida (data URLs). Usado pela página do
    chat (gerar) e pela versão online (nuvem.py)."""
    p = ler_projeto(caminho)
    midias = {}
    tam = 0
    with tempfile.TemporaryDirectory(prefix="editor_") as pasta:
        # vídeos: pedaços usados + folga
        usos, faixas_audio = {}, {}
        for t in p["trechos"]:
            nome = t["midia"]
            if t["_imagem"]:
                continue
            dur = t["_info"]["duracao"] or (t["ate"] + folga)
            usos.setdefault(nome, []).append([max(0, t["de"] - folga), min(dur, t["ate"] + folga)])
            if nome not in faixas_audio:
                faixas_audio[nome] = [f.get("faixa", 0) for f in t.get("faixas", [])] or [0]
        for o in p.get("sobre", []):  # clipes por cima que vêm de vídeo
            if o.get("_imagem"):
                continue
            nome = o["midia"]
            dur = o["_info"]["duracao"] or (float(o["ate"]) + folga)
            usos.setdefault(nome, []).append([max(0, float(o.get("de", 0)) - folga), min(dur, float(o["ate"]) + folga)])
            faixas_audio.setdefault(nome, [0])
        # vídeos da pasta que ainda não entraram (memes etc.): até 20 s, pra poder pôr por cima na página
        for nome, cam in p["_midia"].items():
            if nome in usos or os.path.splitext(cam)[1].lower() in IMAGENS:
                continue
            inf = sondar(cam)
            if inf["video"] and inf["duracao"] and nome not in [s["midia"] for s in p.get("sfx", [])]:
                usos[nome] = [[0, min(inf["duracao"], 20)]]
                faixas_audio[nome] = [0]
        for nome, fx in usos.items():
            src, mapa, n, mini, pc = proxy_video(p["_midia"][nome], juntar(fx), faixas_audio[nome], pasta, nome, p["_layout"].get(nome))
            midias[nome] = {"tipo": "video", "src": src, "mapa": mapa, "mini": mini, "picos": pc,
                            "duracao": sondar(p["_midia"][nome])["duracao"]}
            tam += n
        for t in p["trechos"] + [o for o in p.get("sobre", []) if o.get("_imagem")] + \
                [{"midia": n, "_imagem": True} for n, c in p["_midia"].items() if os.path.splitext(c)[1].lower() in IMAGENS]:
            if t["_imagem"] and t["midia"] not in midias:
                src, n = proxy_imagem(p["_midia"][t["midia"]], pasta, t["midia"])
                midias[t["midia"]] = {"tipo": "imagem", "src": src}
                tam += n
        # áudios: todos os da pasta midia que não são vídeo de trecho (SFX disponíveis pra usar)
        for nome, cam in p["_midia"].items():
            if nome in midias:
                continue
            ext = os.path.splitext(cam)[1].lower()
            if ext in IMAGENS:
                continue
            info = sondar(cam)
            if info["video"] and nome not in [s["midia"] for s in p.get("sfx", [])]:
                continue
            eh_musica = p.get("musica", {}) and p["musica"].get("midia") == nome
            src, n = proxy_audio(cam, pasta, nome, None if eh_musica else 12)
            midias[nome] = {"tipo": "audio", "src": src, "duracao": info["duracao"],
                            "picos": picos(cam, 40, None if eh_musica else 12)}
            tam += n

    return {"projeto": limpo(p), "midias": midias}, tam, p


def gerar(caminho, saida, folga):
    dados, tam, p = montar_dados(caminho, folga)
    modelo = open(MODELO, encoding="utf-8").read()
    css = open(CSS, encoding="utf-8").read()
    titulo = "Edição " + (p.get("nome") or "do vídeo").replace("_", " ")
    html = (modelo.replace("/*__CSS_QUADRO__*/", css).replace("__TITULO__", titulo)
            .replace("/*__DADOS__*/null", json.dumps(dados, ensure_ascii=False)))
    with open(saida, "w", encoding="utf-8") as f:
        f.write(html)
    mb = os.path.getsize(saida) / 1e6
    log(f"editor: {saida}  ({mb:.2f} MB; mídia embutida {tam / 1e6:.2f} MB)")
    if mb > 5:
        log("AVISO: passou de 5 MB, o chat do King Bot recusa. Diminua a folga ou divida o projeto.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("projeto")
    ap.add_argument("--saida")
    ap.add_argument("--folga", type=float, default=1.5)
    a = ap.parse_args()
    saida = a.saida or os.path.join(os.path.dirname(os.path.abspath(a.projeto)), "editor.html")
    try:
        gerar(a.projeto, saida, a.folga)
    except ErroProjeto as e:
        log(f"ERRO: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
