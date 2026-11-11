#!/usr/bin/env python3
"""Rascunho automático: bruto + lista de cortes -> projeto.json com cortes colados nas falas
e legendas já cronometradas (texto original em `en`, `pt` vazio pra traduzir).

A transcrição (faster-whisper, tempo de cada palavra) é feita uma vez por pedaço e fica
guardada em cache ao lado do bruto (<bruto>.palavras.json): rodar de novo com outros cortes
reaproveita o que já foi ouvido.

Quem fala: gravação do OmeTV tem 2 faixas de áudio (faixa 0 = só o gringo, faixa 1 = gringo + mic).
Pra cada palavra o script compara o Zaza (faixa 1 - faixa 0) com a faixa 0. A voz do Zaza volta
pelo mic do gringo (~1 s depois): esse eco é medido e a palavra explicada por ele vira "eco" e sai
das legendas (--com-eco mantém). Sai em cada legenda como "quem": "gringo" | "zaza".

Uso:
  python3 rascunho.py BRUTO --cortes "120.3-124.6,125.2-130.5" --saida pasta/projeto.json
      [--nome chileno] [--idioma es|pt|en] [--modelo small|medium|large-v3]
      [--faixa-mix 1 --faixa-gringo 0] [--empilhar "0,34,560,498;680,34,560,498"]
      [--balao "texto do balão"] [--sem-colar]
  python3 rascunho.py BRUTO --so-transcrever [--de 0 --ate 300]   # só ouve e guarda no cache, mostra as falas
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from array import array

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from render import sondar  # noqa: E402

TAXA = 16000
MAX_CHARS, MAX_DUR, PAUSA = 40, 2.6, 0.45


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def audio_bruto(cam, faixa, de, ate):
    """PCM mono 16 kHz (float) de uma faixa, entre de e ate."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{de:.3f}", "-t", f"{ate - de:.3f}", "-i", cam,
           "-map", f"0:a:{faixa}", "-ac", "1", "-ar", str(TAXA), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    a = array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    return a


def rms(a, i0, i1):
    i0, i1 = max(0, i0), min(len(a), i1)
    if i1 <= i0:
        return 0.0
    s = 0.0
    for x in a[i0:i1]:
        s += x * x
    return math.sqrt(s / (i1 - i0))


# ------------------------------------------------------------------ transcrição com cache

def cache_de(cam):
    return cam + ".palavras.json"


def ler_cache(cam):
    c = cache_de(cam)
    if os.path.exists(c):
        with open(c, encoding="utf-8") as f:
            return json.load(f)
    return {"pedacos": []}


def gravar_cache(cam, cache):
    with open(cache_de(cam), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


_modelo = {}
_trava = None
TRAVA = "/tmp/kingbot-whisper.lock"  # /tmp é da máquina toda: um whisper por vez entre TODOS os agentes


def travar_whisper():
    """2 GB de memória pra todos: dois whisper juntos morrem (137). O segundo espera o primeiro."""
    global _trava
    if _trava is not None:
        return
    import fcntl
    import time
    _trava = open(TRAVA, "a+")
    try:
        fcntl.flock(_trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("outro whisper rodando na máquina: esperando ele terminar (não rode dois juntos, estoura a memória) ...")
        t0 = time.time()
        fcntl.flock(_trava, fcntl.LOCK_EX)
        log(f"liberou depois de {time.time() - t0:.0f}s")


def audio_sem_eco(cam, faixa_mix, faixa_gringo, de, ate, eco):
    """Mix com o eco do Zaza abafado na faixa do gringo: o whisper não ouve a fala duas vezes
    (nem embola a fala nova do Zaza com o eco da anterior). Tempo igual ao do bruto."""
    import numpy as np
    L = eco["atraso"]
    d0 = max(0.0, de - L)
    mix = _np(audio_bruto(cam, faixa_mix, d0, ate))
    gr = _np(audio_bruto(cam, faixa_gringo, d0, ate))
    n = min(len(mix), len(gr))
    mix, gr = mix[:n], gr[:n]
    a = eco["mix_gr"]
    ez, eg = _env(mix - a * gr), _env(a * gr)
    lag = int(round(L * TAXA / JAN))
    prev = np.zeros_like(eg)
    prev[lag:] = eco["ganho"] * a * ez[: len(ez) - lag]
    eco_manda = prev >= 0.5 * eg  # quadro em que a faixa do gringo é basicamente o eco
    # alarga 50 ms pros lados (rabo do eco) e suaviza a porta pra não estalar
    k = 5
    eco_manda = np.convolve(eco_manda.astype(np.float32), np.ones(2 * k + 1), "same") > 0
    porta = np.where(eco_manda, 0.08, 1.0).astype(np.float32)
    porta = np.convolve(porta, np.ones(3) / 3, "same")
    porta = np.repeat(porta, JAN)
    porta = np.concatenate([porta, np.ones(n - len(porta), np.float32)])
    limpo = mix - a * gr * (1.0 - porta)
    i0 = int(round((de - d0) * TAXA))
    limpo = np.clip(limpo[i0:] * 32768.0, -32768, 32767).astype(np.int16)
    return array("h", limpo.tobytes()), float(eco_manda.mean())


def transcrever_pedaco(cam, de, ate, faixa_mix, faixa_gringo, idioma, modelo, eco=None):
    from faster_whisper import WhisperModel
    travar_whisper()
    if modelo not in _modelo:
        _modelo[modelo] = WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=2)
    limpo = bool(eco and eco.get("corr", 0) >= 0.35 and faixa_gringo is not None and faixa_gringo != faixa_mix)
    if limpo:
        mix, frac = audio_sem_eco(cam, faixa_mix, faixa_gringo, de, ate, eco)
    else:
        mix = audio_bruto(cam, faixa_mix, de, ate)
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(TAXA), "-ac", "1", "-i", "-", tmp.name],
                       input=mix.tobytes(), check=True)
        segs, info = _modelo[modelo].transcribe(tmp.name, language=idioma, word_timestamps=True,
                                                vad_filter=True, beam_size=1, condition_on_previous_text=False)
        palavras = []
        for s in segs:
            for w in (s.words or []):
                palavras.append({"w": w.word.strip(), "de": round(de + w.start, 3), "ate": round(de + w.end, 3),
                                 "p": round(w.probability, 2)})
    gr = None
    if faixa_gringo is not None and faixa_gringo != faixa_mix:
        try:
            gr = audio_bruto(cam, faixa_gringo, de, ate)
        except subprocess.CalledProcessError:
            gr = None
    for w in palavras:
        if gr is None:
            w["quem"] = "?"
            continue
        i0, i1 = int((w["de"] - de) * TAXA), int((w["ate"] - de) * TAXA)
        r_mix, r_gr = rms(mix, i0, i1), rms(gr, i0, i1)
        w["quem"] = "gringo" if r_gr >= 0.5 * r_mix else "zaza"
    ped = {"de": de, "ate": ate, "faixa": faixa_mix, "idioma": idioma or info.language, "modelo": modelo,
           "palavras": palavras}
    if limpo:
        ped["sem_eco"] = True
    return ped


def palavras_em(cam, de, ate, args, cache):
    """Palavras entre de e ate, transcrevendo só o que ainda não está no cache."""
    eco = None
    if args.faixa_gringo is not None and args.faixa_gringo != args.faixa_mix:
        eco = eco_do_cache(cam, args, cache, de, ate)
    forte = bool(eco and eco["corr"] >= 0.35)
    # com eco, pedaço ouvido antes do v7 (com o eco junto) tem palavra embolada e tempo errado: ouve de novo
    cobertos = [c for c in cache["pedacos"] if c["faixa"] == args.faixa_mix and c["modelo"] == args.modelo
                and (args.idioma in (None, c["idioma"]))
                and (c.get("sem_eco") or c.get("externo") or not forte or getattr(args, "cache_com_eco", False))]
    usar = cobertos if forte and not getattr(args, "cache_com_eco", False) else None
    faltam, t = [], de
    for c in sorted(cobertos, key=lambda c: c["de"]):
        if c["ate"] <= t or c["de"] >= ate:
            continue
        if c["de"] > t:
            faltam.append((t, c["de"]))
        t = max(t, c["ate"])
    if t < ate:
        faltam.append((t, ate))
    # pedaços de no máximo 20 s: transcrever minutos de uma vez estoura os 2 GB de memória da máquina
    picados = []
    for a, b in faltam:
        while b - a > 20:
            picados.append((a, a + 20)); a += 20
        picados.append((a, b))
    picados = [(a, b) for a, b in picados if b - a >= 0.3]
    if picados and (args.palavras or args.sem_whisper):
        # transcrição vem do Zaza (PC dele): aqui nunca roda whisper, só avisa o que falta
        for x, y in picados:
            log(f"AVISO: {x:.1f}-{y:.1f}s sem transcrição no JSON (fica sem legenda; peça esse trecho ao Zaza)")
        picados = []
    for a, b in picados:
        a2, b2 = max(0, a - 1.0), b + 1.0  # margem pra não cortar palavra na borda
        log(f"ouvindo {a2:.1f}-{b2:.1f}s ...")
        ped = transcrever_pedaco(cam, a2, b2, args.faixa_mix, args.faixa_gringo, args.idioma, args.modelo, eco)
        cache["pedacos"].append(ped)
        gravar_cache(cam, cache)
    # pedaços se sobrepõem (margem de 1s): cada palavra vale do pedaço onde ela fica mais longe da borda
    cand = []
    for c in cache["pedacos"]:
        if c["faixa"] != args.faixa_mix or c["modelo"] != args.modelo:
            continue
        if usar is not None and not (c.get("sem_eco") or c.get("externo")):
            continue
        for w in c["palavras"]:
            meio = (w["de"] + w["ate"]) / 2
            if de <= meio <= ate:
                # pedaço ouvido sem o eco ganha de um antigo (com eco) no mesmo lugar
                cand.append((min(meio - c["de"], c["ate"] - meio) + (100 if c.get("sem_eco") else 0), w))
    cand.sort(key=lambda x: -x[0])
    out = []
    for _, w in cand:
        if all(min(w["ate"], o["ate"]) - max(w["de"], o["de"]) < 0.5 * min(w["ate"] - w["de"], o["ate"] - o["de"]) + 1e-3 for o in out):
            out.append(w)
    out.sort(key=lambda w: w["de"])
    return out


def importar_palavras(cam, args, cache):
    """Aceita lista de palavras ou de segmentos com "words", em vários formatos de chave
    (w/word/text, de/start, ate/end), tempos em segundos do bruto. Calcula quem fala pelo áudio local."""
    with open(args.palavras, encoding="utf-8") as f:
        dado = json.load(f)
    if isinstance(dado, dict) and dado.get("pedacos"):
        # cache de outro agente (bruto.mp4.palavras.json): mantém cada pedaço com a cobertura que ele
        # tem de verdade. Antes virava um bloco só do 1º ao último segundo, e o buraco entre dois
        # cortes ouvidos passava por "já ouvido" e saía sem legenda nenhuma.
        n = 0
        # transcrição do bruto inteiro (transcritor do Zaza): os pedaços por idioma têm buraco de silêncio
        # entre eles, mas foi tudo ouvido. Estica um até o outro pra o buraco não virar "sem transcrição".
        peds = sorted(dado["pedacos"], key=lambda c: c["de"])
        if dado.get("duracao"):
            for i, c in enumerate(peds):
                c["de"] = 0 if i == 0 else peds[i - 1]["ate"]
            peds[-1]["ate"] = max(peds[-1]["ate"], dado["duracao"])
        for c in peds:
            ws = [w for w in c.get("palavras", []) if "w" in w and "de" in w and "ate" in w]
            if any(abs(o["de"] - c["de"]) < 0.01 and abs(o["ate"] - c["ate"]) < 0.01 and o["faixa"] == args.faixa_mix
                   and o["modelo"] == args.modelo for o in cache["pedacos"]):
                continue
            # idioma do roteiro (o de quem transcreveu pode vir diferente) e "externo": vale como ouvido,
            # com ou sem eco. Sem isso o JSON do Zaza era ignorado e o corte saía sem legenda.
            cache["pedacos"].append({"de": c["de"], "ate": c["ate"], "faixa": args.faixa_mix,
                                     "idioma": args.idioma or c.get("idioma"), "modelo": args.modelo,
                                     "idioma_origem": c.get("idioma"), "externo": True,
                                     "palavras": ws, "importado": os.path.basename(args.palavras),
                                     **({"sem_eco": True} if c.get("sem_eco") else {})})
            n += len(ws)
        idi = {c.get("idioma") for c in dado["pedacos"]} | {dado.get("idioma")}
        idi.discard(None)
        if args.idioma and idi and args.idioma not in idi:
            log(f"AVISO: a transcrição veio em '{'/'.join(sorted(idi))}' e o roteiro pede '{args.idioma}': "
                f"se a conversa é em {args.idioma}, ela foi TRADUZIDA. Peça ao Zaza pra transcrever de novo com idioma {args.idioma}.")
        if dado.get("eco") and not cache.get("eco"):
            cache["eco"] = dado["eco"]
        gravar_cache(cam, cache)
        log(f"{n} palavras importadas de {args.palavras} ({len(dado['pedacos'])} pedaços; quem fala é refeito pelo áudio)")
        return
    dado_idioma = dado.get("language") or dado.get("idioma") if isinstance(dado, dict) else None
    if isinstance(dado, dict):
        dado = dado.get("words") or dado.get("segments") or dado.get("palavras") or []
    plano = []
    for item in dado:
        sub = item.get("words") if isinstance(item, dict) and item.get("words") else [item]
        for w in sub:
            txt = w.get("w") or w.get("word") or w.get("text") or w.get("punctuated_word")
            de = w.get("de", w.get("start"))
            ate = w.get("ate", w.get("end"))
            if txt is None or de is None or ate is None:
                continue
            de, ate = float(de), float(ate)
            if de > 1000 and ate > 1000:  # milissegundos
                de, ate = de / 1000, ate / 1000
            de, ate = de + args.palavras_de, ate + args.palavras_de  # transcrição de um trecho: tempo do bruto
            plano.append({"w": str(txt).strip(), "de": round(de, 3), "ate": round(ate, 3),
                          "p": float(w.get("p", w.get("probability", w.get("confidence", 0.9))))})
    if not plano:
        sys.exit("ERRO: não achei palavras com início e fim em " + args.palavras)
    de, ate = min(w["de"] for w in plano), max(w["ate"] for w in plano)
    mix = audio_bruto(cam, args.faixa_mix, de, ate)
    gr = audio_bruto(cam, args.faixa_gringo, de, ate) if args.faixa_gringo is not None and args.faixa_gringo != args.faixa_mix else None
    for w in plano:
        if gr is None:
            w["quem"] = "?"
            continue
        i0, i1 = int((w["de"] - de) * TAXA), int((w["ate"] - de) * TAXA)
        w["quem"] = "gringo" if rms(gr, i0, i1) >= 0.5 * rms(mix, i0, i1) else "zaza"
    # tira só o que esta transcrição cobre (antes apagava tudo: mandar 2 trechos separados perdia o 1º)
    cache["pedacos"] = [c for c in cache["pedacos"] if not (c["faixa"] == args.faixa_mix and c["modelo"] == args.modelo
                                                            and c["de"] < ate + 1 and c["ate"] > de - 1)]
    cache["pedacos"].append({"de": de - 1, "ate": ate + 1, "faixa": args.faixa_mix, "idioma": args.idioma or dado_idioma,
                             "modelo": args.modelo, "palavras": plano, "externo": True,
                             "importado": os.path.basename(args.palavras)})
    gravar_cache(cam, cache)
    log(f"{len(plano)} palavras importadas de {args.palavras}")


# ------------------------------------------------------------------ quem fala + eco (v7)
# No OmeTV a voz do Zaza sai no alto-falante do gringo e volta pelo mic dele: a faixa 0
# ("só o gringo") carrega o Zaza de novo, ~0,5-1,5 s depois. Sem tratar, toda fala do Zaza
# vira duas legendas (a segunda marcada "gringo"). Aqui: zaza = mix - gringo; mede o atraso
# e o volume do eco cruzando os envelopes; palavra da faixa 0 explicada pelo eco vira "eco"
# e sai das legendas.

ECO_MIN, ECO_MAX, JAN = 0.2, 2.0, 160  # atraso procurado (s) e janela do envelope (10 ms)


def _np(a):
    import numpy as np
    return np.frombuffer(a.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0


def _env(x):
    import numpy as np
    n = len(x) // JAN
    return np.sqrt((x[: n * JAN].reshape(n, JAN) ** 2).mean(1) + 1e-12)


def medir_eco(cam, args, de, ate):
    """Atraso, ganho e força do eco do Zaza na faixa do gringo, entre de e ate (até 90 s)."""
    import numpy as np
    ate = min(ate, de + 90)
    mix = _np(audio_bruto(cam, args.faixa_mix, de, ate))
    gr = _np(audio_bruto(cam, args.faixa_gringo, de, ate))
    n = min(len(mix), len(gr))
    mix, gr = mix[:n], gr[:n]
    a = float(np.dot(mix, gr) / (np.dot(gr, gr) + 1e-9))
    z = mix - a * gr
    ez, eg = _env(z), _env(gr)
    lz, lg = np.log(ez + 1e-4), np.log(eg + 1e-4)
    melhor = (-1.0, 0)
    for lag in range(int(ECO_MIN * 100), int(ECO_MAX * 100) + 1):
        if len(lz) - lag < 300:
            break
        c = float(np.corrcoef(lz[: len(lz) - lag], lg[lag:])[0, 1])
        if c > melhor[0]:
            melhor = (c, lag)
    c, lag = melhor
    # ganho do eco: nos quadros em que o Zaza fala alto e (lag depois) a faixa 0 tem som
    zs, gs = ez[: len(ez) - lag], eg[lag:]
    forte = zs > np.percentile(zs, 85)
    k = float(np.median(gs[forte] / zs[forte])) if forte.any() else 0.0
    return {"atraso": round(lag / 100, 2), "ganho": round(k, 3), "corr": round(c, 3), "mix_gr": round(a, 3),
            "medido_em": [round(de, 2), round(ate, 2)]}


def _norm(t):
    import unicodedata
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(ch for ch in t if ch.isalnum())


def marcar_quem(cam, ws, args, eco):
    """Refaz "quem" de cada palavra (gringo | zaza | eco) pelo áudio, com o eco medido."""
    import numpy as np
    if not ws or args.faixa_gringo is None or args.faixa_gringo == args.faixa_mix:
        return ws
    tem_eco = eco and eco["corr"] >= 0.35
    L = eco["atraso"] if tem_eco else 0.0
    de = max(0.0, min(w["de"] for w in ws) - L - 0.5)
    ate = max(w["ate"] for w in ws) + 0.2
    mix = _np(audio_bruto(cam, args.faixa_mix, de, ate))
    gr = _np(audio_bruto(cam, args.faixa_gringo, de, ate))
    n = min(len(mix), len(gr))
    mix, gr = mix[:n], gr[:n]
    z = mix - (eco["mix_gr"] if eco else 1.0) * gr

    def r(x, a, b):
        i0, i1 = max(0, int((a - de) * TAXA)), min(len(x), int((b - de) * TAXA))
        return float(np.sqrt((x[i0:i1] ** 2).mean())) if i1 > i0 else 0.0

    for w in ws:
        rz, rg = r(z, w["de"], w["ate"]), r(gr, w["de"], w["ate"])
        w["_fraco"] = rz < 0.5 * rg
        e = eco["ganho"] * r(z, w["de"] - L, w["ate"] - L) if tem_eco else 0.0
        resto = math.sqrt(max(0.0, rg * rg - e * e))  # faixa 0 sem o eco = voz do gringo
        if e >= 0.6 * rg and rz < 0.5 * rg:  # a faixa 0 só tem o eco e o Zaza não fala agora
            w["quem"] = "eco"
        elif rz >= resto:
            w["quem"] = "zaza"
        else:
            w["quem"] = "gringo"
    if tem_eco:  # mesma palavra que o Zaza disse ~L antes = eco, mesmo que o volume engane
        zaza = [w for w in ws if w["quem"] == "zaza"]
        for w in ws:
            if w["quem"] == "eco" or not w["_fraco"] or not _norm(w["w"]):
                continue
            if any(o is not w and _norm(o["w"]) == _norm(w["w"]) and abs((w["de"] - o["de"]) - L) <= 0.6 for o in zaza):
                w["quem"] = "eco"
    for w in ws:
        w.pop("_fraco", None)
    # tempo de palavra do whisper erra uns 100 ms: palavra solta no meio da frase de outra pessoa
    # ("are"=zaza "you"=gringo "from"=zaza) quebrava a legenda em pedacinhos. Uma palavra isolada,
    # colada nas vizinhas, fica com quem fala em volta dela.
    for _ in range(2):
        for i in range(1, len(ws) - 1):
            a_, w, b_ = ws[i - 1], ws[i], ws[i + 1]
            if "eco" in (a_["quem"], w["quem"], b_["quem"]):
                continue
            curta = w["ate"] - w["de"] < 0.3  # só a palavra curta (tempo menos confiável) muda
            if curta and a_["quem"] == b_["quem"] != w["quem"] and w["de"] - a_["ate"] < 0.35 and b_["de"] - w["ate"] < 0.35:
                w["quem"] = a_["quem"]
    return ws


_eco_avisado = False


def eco_do_cache(cam, args, cache, de, ate):
    """Eco medido 1x e guardado no cache. Se a medida guardada não achou eco e foi feita em outro
    trecho (onde o Zaza talvez nem falasse), mede de novo neste e fica com a mais forte."""
    global _eco_avisado
    if ate - de < 40:  # janela curta não mede atraso direito: usa pelo menos 60 s em volta
        meio = (de + ate) / 2
        de, ate = max(0.0, meio - 30), meio + 30
    e = cache.get("eco")
    faixas = [args.faixa_mix, args.faixa_gringo]
    if e and e.get("faixas") == faixas and e["corr"] < 0.35:
        m0, m1 = e.get("medido_em", [0, 0])
        if de < m0 - 5 or ate > m1 + 5:
            novo = medir_eco(cam, args, de, ate)
            novo["faixas"] = faixas
            if novo["corr"] > e["corr"]:
                e = cache["eco"] = novo
                gravar_cache(cam, cache)
    if not e or e.get("faixas") != faixas:
        e = medir_eco(cam, args, de, ate)
        e["faixas"] = faixas
        cache["eco"] = e
        gravar_cache(cam, cache)
    if e["corr"] >= 0.35 and not _eco_avisado:
        _eco_avisado = True
        log(f"eco do Zaza na faixa do gringo: {e['atraso']:.2f}s depois (força {e['corr']:.2f}); "
            f"o whisper ouve sem ele e as palavras de eco saem das legendas")
    return e


# ------------------------------------------------------------------ montagem

def agrupar(palavras):
    """Junta palavras em legendas: quebra em troca de quem fala, pausa, fim de frase, tamanho."""
    grupos, atual = [], []
    for w in palavras:
        if atual:
            texto = " ".join(x["w"] for x in atual)
            ultimo = atual[-1]
            quebra = (w.get("quem") != ultimo.get("quem")
                      or w["de"] - ultimo["ate"] > PAUSA
                      or len(texto) + 1 + len(w["w"]) > MAX_CHARS
                      or w["ate"] - atual[0]["de"] > MAX_DUR
                      or (ultimo["w"][-1:] in ".?!" and len(texto) > 12))
            if quebra:
                grupos.append(atual)
                atual = []
        atual.append(w)
    if atual:
        grupos.append(atual)
    return grupos


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from atualizar import atualizar
    atualizar(__file__)
    ap = argparse.ArgumentParser(description="Rascunho automático do projeto.json")
    ap.add_argument("bruto")
    ap.add_argument("--cortes", help='"de-ate,de-ate" em segundos do bruto, na ordem do vídeo')
    ap.add_argument("--saida")
    ap.add_argument("--nome")
    ap.add_argument("--idioma", default=None, help="es, pt, en... (sem isso o whisper adivinha por pedaço)")
    ap.add_argument("--modelo", default="small")
    ap.add_argument("--faixa-mix", type=int, default=None, help="faixa com todo mundo (padrão: 1 se tiver 2 faixas, senão 0)")
    ap.add_argument("--faixa-gringo", type=int, default=None, help="faixa só com o gringo (padrão: 0 se tiver 2 faixas)")
    ap.add_argument("--empilhar", help='recortes "x,y,w,h;x,y,w,h" (bruto cru do OmeTV)')
    ap.add_argument("--balao")
    ap.add_argument("--sem-colar", action="store_true", help="não ajusta as bordas dos cortes nas palavras")
    ap.add_argument("--so-transcrever", action="store_true")
    ap.add_argument("--palavras", help="JSON com palavras já transcritas (ex.: a que o Zaza faz no PC dele): pula o whisper local")
    ap.add_argument("--palavras-de", type=float, default=0.0,
                    help="se a transcrição foi de um trecho que começa em N s do bruto, soma N nos tempos")
    ap.add_argument("--com-eco", action="store_true", help="mantém nas legendas as palavras marcadas como eco do Zaza")
    ap.add_argument("--cache-com-eco", action="store_true",
                    help="aceita transcrição antiga (feita antes do v7, com o eco junto) sem ouvir de novo: mais rápido, pior")
    ap.add_argument("--so-importar", action="store_true", help="só importa o --palavras pro cache e sai")
    ap.add_argument("--sem-whisper", action="store_true", help="nunca ouve aqui (já é assim com --palavras)")
    ap.add_argument("--de", type=float, default=0)
    ap.add_argument("--ate", type=float, default=None)
    a = ap.parse_args()

    cam = os.path.abspath(a.bruto)
    info = sondar(cam)
    if info["audios"] == 0:
        sys.exit("ERRO: o bruto não tem áudio")
    if a.faixa_mix is None:
        a.faixa_mix = 1 if info["audios"] >= 2 else 0
    if a.faixa_gringo is None:
        a.faixa_gringo = 0 if info["audios"] >= 2 else None
    cache = ler_cache(cam)
    if a.palavras:
        importar_palavras(cam, a, cache)
        if a.so_importar:
            return

    if a.so_transcrever:
        ws = palavras_em(cam, a.de, a.ate or info["duracao"], a, cache)
        if ws and a.faixa_gringo is not None:
            eco = eco_do_cache(cam, a, cache, ws[0]["de"], ws[-1]["ate"])
            ws = marcar_quem(cam, ws, a, eco)
            if not a.com_eco:
                ws = [w for w in ws if w["quem"] != "eco"]
        for g in agrupar(ws):
            print(f"[{g[0]['de']:7.2f}-{g[-1]['ate']:7.2f}] {g[0].get('quem', '?'):6} {' '.join(x['w'] for x in g)}")
        return

    if not a.cortes or not a.saida:
        sys.exit("ERRO: precisa de --cortes e --saida (ou --so-transcrever)")
    cortes, soltos = [], set()
    for parte in a.cortes.split(","):
        parte = parte.strip()
        if parte.endswith("!"):  # "182-184!" = não cola nas falas (ex.: cold open com riso/dança)
            parte = parte[:-1]
            soltos.add(len(cortes))
        d, t = parte.split("-")
        cortes.append([float(d), float(t)])

    eco = None
    if a.faixa_gringo is not None:
        eco = eco_do_cache(cam, a, cache, max(0, cortes[0][0] - 2), max(t for _, t in cortes))
    trechos, legendas, ini, n_eco = [], [], 0.0, 0
    for i_corte, (d, t) in enumerate(cortes):
        ws = palavras_em(cam, d, t, a, cache)
        if ws and eco is not None:
            ws = marcar_quem(cam, ws, a, eco)
            n_eco += sum(w["quem"] == "eco" for w in ws)
            if not a.com_eco:
                ws = [w for w in ws if w["quem"] != "eco"]
        if ws and not a.sem_colar and i_corte not in soltos:  # cola nas falas: começa um tiquinho antes da 1ª palavra, termina um pouco depois da última
            d2 = max(d - 0.3, ws[0]["de"] - 0.08)
            t2 = min(t + 0.3, ws[-1]["ate"] + 0.15)
            d, t = round(max(0, d2), 3), round(min(info["duracao"], t2), 3)
        tr = {"midia": "bruto", "de": d, "ate": t}
        if info["audios"] >= 2:
            tr["faixas"] = [{"faixa": a.faixa_mix}]
        trechos.append(tr)
        for g in agrupar([w for w in ws if d <= (w["de"] + w["ate"]) / 2 <= t]):
            de = ini + max(0, g[0]["de"] - d)
            ate = ini + min(t - d, g[-1]["ate"] - d)
            legendas.append({"de": round(de, 3), "ate": round(ate, 3), "en": " ".join(x["w"] for x in g), "pt": "",
                             "quem": g[0].get("quem", "?"),
                             "confianca": round(sum(x["p"] for x in g) / len(g), 2)})
        ini += t - d

    # sem piscar: legenda vai até a próxima se o buraco for pequeno; e nunca sobrepõe
    legendas.sort(key=lambda l: l["de"])
    for i, l in enumerate(legendas[:-1]):
        prox = legendas[i + 1]
        if prox["de"] - l["ate"] < 0.3:
            l["ate"] = prox["de"]
        l["ate"] = min(l["ate"], prox["de"])
    legendas = [l for l in legendas if l["ate"] - l["de"] >= 0.15]

    pasta = os.path.dirname(os.path.abspath(a.saida))
    os.makedirs(pasta, exist_ok=True)
    rel = os.path.relpath(cam, pasta)
    midia = {"arquivo": rel}
    if a.empilhar:
        midia["empilhar"] = [dict(zip("xywh", map(int, r.split(",")))) for r in a.empilhar.split(";")]
    proj = {"nome": a.nome or os.path.splitext(os.path.basename(a.saida))[0],
            "midia": {"bruto": midia if a.empilhar else rel},
            "trechos": trechos, "legendas": legendas, "sfx": []}
    if a.balao:
        proj["balao"] = {"texto": a.balao, "de": 0, "ate": round(min(3.5, ini), 3)}
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, indent=2)
    baixa = [l for l in legendas if l["confianca"] < 0.6]
    log(f"rascunho: {a.saida}  ({ini:.2f}s, {len(trechos)} cortes, {len(legendas)} legendas"
        f"{f', {len(baixa)} com confiança baixa (conferir de ouvido)' if baixa else ''}"
        f"{f', {n_eco} palavras de eco tiradas' if n_eco and not a.com_eco else ''})")
    for l in legendas:
        print(f"[{l['de']:6.2f}-{l['ate']:6.2f}] {l['quem']:6} {l['confianca']:.2f}  {l['en']}")


if __name__ == "__main__":
    main()
