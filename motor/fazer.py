#!/usr/bin/env python3
"""Um comando do roteiro até a prévia conferida.

  python3 motor/fazer.py pasta/roteiro.json            # projeto.json + prévia 540x960 + quadros + conferir.md + post.txt
  python3 motor/fazer.py pasta/roteiro.json --final    # depois de revisar: render final 1080x1920 do projeto.json
  python3 motor/fazer.py pasta/roteiro.json --sem-previa   # só o projeto.json e o conferir.md (sem render)

Formato do roteiro: ver exemplos/roteiro_modelo.json e o LEIA-ME.
Transcrição: SEMPRE do JSON do Zaza (`palavras`). Aqui nunca roda whisper.
Antes de tudo, confere se tem pacote mais novo na pasta compartilhada do code guide
(editor-video-zaza-atual.zip) e se atualiza sozinho (--sem-atualizar pula).
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import unicodedata
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
CODE_GUIDE = "5e3eba9f-5e75-4be8-83ac-d2c836619992"
PACOTE = "editor-video-zaza-atual.zip"
OMETV = "0,34,560,498;680,34,560,498"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


from atualizar import versao, atualizar  # noqa: E402,F401


# ------------------------------------------------------------------ texto

def norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(ch for ch in t if ch.isalnum() or ch == " ").split()


def palavras_do_cache(bruto):
    c = bruto + ".palavras.json"
    if not os.path.exists(c):
        return []
    dado = json.load(open(c, encoding="utf-8"))
    vistos, out = set(), []
    for p in dado.get("pedacos", []):
        for w in p.get("palavras", []):
            k = (w["w"], round(w["de"], 2))
            if k not in vistos:
                vistos.add(k)
                out.append(w)
    return sorted(out, key=lambda w: w["de"])


def achar_fecho(ws, fecho, de, ate):
    """Fim da última ocorrência da frase de fecho dentro do corte (folga de 3 s pra depois)."""
    alvo = norm(fecho)
    if not alvo:
        return None
    js = [w for w in ws if de - 0.5 <= w["de"] <= ate + 3]
    toks = [(t, w) for w in js for t in norm(w["w"])]
    fim = None
    for i in range(len(toks) - len(alvo) + 1):
        if [t for t, _ in toks[i:i + len(alvo)]] == alvo:
            fim = toks[i + len(alvo) - 1][1]["ate"]
    return fim


def traduzir(legendas, traducoes):
    """Preenche `pt` pelas traduções do roteiro (casa pelo texto original, tolera diferença pequena)."""
    if not traducoes:
        return 0
    chaves = {" ".join(norm(k)): v for k, v in traducoes.items()}
    n = 0
    for l in legendas:
        k = " ".join(norm(l.get("en", "")))
        if not k or l.get("pt"):
            continue
        if k in chaves:
            l["pt"] = chaves[k]; n += 1
            continue
        m = difflib.get_close_matches(k, list(chaves), n=1, cutoff=0.8)
        if m:
            l["pt"] = chaves[m[0]]; n += 1
    return n


# ------------------------------------------------------------------ principal

def main():
    ap = argparse.ArgumentParser(description="Roteiro -> projeto.json -> prévia conferida")
    ap.add_argument("roteiro")
    ap.add_argument("--final", action="store_true", help="render final 1080x1920 do projeto.json (já revisado)")
    ap.add_argument("--sem-previa", action="store_true")
    ap.add_argument("--sem-atualizar", action="store_true")
    a = ap.parse_args()
    if a.sem_atualizar:
        os.environ["KINGBOT_SEM_ATUALIZAR"] = "1"
    atualizar(__file__)

    rot_path = os.path.abspath(a.roteiro)
    pasta = os.path.dirname(rot_path)
    R = json.load(open(rot_path, encoding="utf-8"))
    nome = R.get("nome") or os.path.basename(pasta)
    proj_path = os.path.join(pasta, "projeto.json")
    render = os.path.join(AQUI, "render.py")

    if a.final:
        if not os.path.exists(proj_path):
            sys.exit("ERRO: rode primeiro sem --final pra gerar o projeto.json")
        p = json.load(open(proj_path, encoding="utf-8"))
        sem_pt = [l for l in p.get("legendas", []) if not l.get("pt") and not l.get("desativado")]
        if sem_pt:
            log(f"aviso: {len(sem_pt)} legendas sem PT (ex.: '{sem_pt[0].get('en')}')")
        subprocess.run([sys.executable, render, proj_path, "--saida", os.path.join(pasta, f"{nome}.mp4")], check=True)
        return

    def caminho(x):
        return x if os.path.isabs(x) else os.path.join(pasta, x)

    bruto = caminho(R["bruto"])
    if not os.path.exists(bruto):
        sys.exit(f"ERRO: bruto não encontrado: {bruto}")
    if not R.get("palavras"):
        sys.exit("ERRO: falta \"palavras\" no roteiro (o JSON da transcrição do Zaza). Aqui não roda whisper.")
    palavras = caminho(R["palavras"])
    if not os.path.exists(palavras):
        sys.exit(f"ERRO: transcrição não encontrada: {palavras}")
    cortes = R.get("cortes") or []
    if not cortes:
        sys.exit("ERRO: roteiro sem cortes")
    cortes = [c if isinstance(c, dict) else {"de": c[0], "ate": c[1]} for c in cortes]

    rasc = os.path.join(AQUI, "rascunho.py")
    base = [sys.executable, rasc, bruto, "--sem-whisper"]
    if R.get("idioma"):
        base += ["--idioma", R["idioma"]]

    # 1) importa a transcrição do Zaza (sem ouvir nada)
    subprocess.run(base + ["--palavras", palavras, "--palavras-de", str(R.get("palavras_de", 0)), "--so-importar"],
                   check=True, stdout=subprocess.DEVNULL)
    ws = palavras_do_cache(bruto)

    # 2) fecho seco: o último corte termina logo depois da frase de fecho
    avisos = []
    if R.get("fecho"):
        ult = cortes[-1]
        fim = achar_fecho(ws, R["fecho"], ult["de"], ult["ate"])
        if fim is None:
            avisos.append(f"Não achei o fecho \"{R['fecho']}\" no último corte ({ult['de']}-{ult['ate']}s): o fim ficou como está no roteiro.")
        else:
            ult["ate"] = round(fim + 0.15, 3)

    # 3) rascunho
    txt = ",".join(f"{c['de']}-{c['ate']}{'!' if c.get('colar') is False else ''}" for c in cortes)
    cmd = base + ["--cortes", txt, "--saida", proj_path, "--nome", nome]
    emp = R.get("empilhar", "ometv")
    if emp:
        cmd += ["--empilhar", OMETV if emp == "ometv" else emp]
    balao = R.get("balao")
    if isinstance(balao, dict):
        balao_txt = balao.get("texto")
    else:
        balao_txt, balao = balao, {}
    if balao_txt:
        cmd += ["--balao", balao_txt]
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        sys.exit("ERRO no rascunho (acima)")
    avisos += [l.replace("AVISO: ", "") for l in r.stderr.splitlines() if l.startswith("AVISO")]

    p = json.load(open(proj_path, encoding="utf-8"))
    n_tr = traduzir(p["legendas"], R.get("traducoes"))
    if balao_txt and balao.get("ate"):
        p["balao"]["ate"] = balao["ate"]
    for k in ("estilo", "sfx", "musica"):
        if R.get(k):
            p[k] = R[k]
    p["roteiro"] = os.path.basename(rot_path)
    json.dump(p, open(proj_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 4) conferência automática das regras
    dur = round(sum(c["ate"] - c["de"] for c in p["trechos"]), 2)
    leg = [l for l in p["legendas"] if not l.get("desativado")]
    regras = R.get("regras", {})
    alvo = regras.get("alvo", [20, 45])
    max_sem = regras.get("max_sem_fala", 4)
    ok, prob = [], []
    planejado = round(sum(c["ate"] - c["de"] for c in cortes), 2)
    extra = round(dur - planejado, 2)
    nota = (f" O roteiro somava {planejado}s; colar nas falas {'somou' if extra > 0 else 'tirou'} {abs(extra)}s"
            f" (cada corte pode crescer até 0,3s de cada lado pra não cortar palavra; colar:false trava o corte)."
            if abs(extra) >= 0.5 else "")
    (ok if alvo[0] <= dur <= alvo[1] else prob).append(f"Duração {dur}s (alvo {alvo[0]}-{alvo[1]}s).{nota}")
    marcos = [0.0] + [l["de"] for l in leg] + [dur]
    buracos = [(marcos[i], marcos[i + 1]) for i in range(len(marcos) - 1) if marcos[i + 1] - marcos[i] > max_sem]
    if buracos:
        prob.append("Mais de %ss sem fala nova: %s." % (max_sem, ", ".join(f"{x:.1f}-{y:.1f}s" for x, y in buracos)))
    else:
        ok.append(f"Fala nova pelo menos a cada {max_sem}s.")
    if R.get("fecho") and leg:
        ultima = leg[-1]
        casa = norm(R["fecho"])[-1:] == norm(ultima["en"])[-1:]
        sobra = dur - ultima["ate"]
        if casa and sobra <= 0.6:
            ok.append(f"Termina seco no fecho (\"{ultima['en']}\", sobra {sobra:.2f}s).")
        else:
            prob.append(f"Última legenda é \"{ultima['en']}\" e sobram {sobra:.2f}s depois dela: o fecho não está seco.")
    baixa = [l for l in leg if l.get("confianca", 1) < 0.6]
    sem_pt = [l for l in leg if not l.get("pt")]
    eco = [l for l in leg if l.get("quem") == "eco"]

    linhas = [f"# Conferir: {nome}", "", f"Projeto: projeto.json ({len(p['trechos'])} cortes, {len(leg)} legendas, {dur}s)", ""]
    if prob or avisos:
        linhas += ["## Resolver antes do final"] + [f"- {x}" for x in prob + avisos] + [""]
    if baixa:
        linhas += ["## Conferir de ouvido (confiança < 0.6)"]
        linhas += [f"- {l['de']:.2f}s [{l.get('quem')}] {l['en']} ({l['confianca']})" for l in baixa] + [""]
    if sem_pt:
        linhas += [f"## Sem tradução PT ({len(sem_pt)}; {n_tr} preenchidas pelo roteiro)"]
        linhas += [f"- {l['de']:.2f}s {l['en']}" for l in sem_pt] + [""]
    linhas += ["## OK"] + [f"- {x}" for x in ok] + [""]
    linhas += ["## Legendas", ""] + [f"- {l['de']:6.2f}-{l['ate']:6.2f} {l.get('quem', '?'):6} {l['en']}  |  {l.get('pt', '')}" for l in leg]
    open(os.path.join(pasta, "conferir.md"), "w", encoding="utf-8").write("\n".join(linhas) + "\n")
    if R.get("legenda_post"):
        open(os.path.join(pasta, "post.txt"), "w", encoding="utf-8").write(R["legenda_post"].strip() + "\n")

    # 5) prévia + quadros (frame 0, começo de cada corte, fim)
    if not a.sem_previa:
        t, marcas = 0.0, [0.0]
        for c in p["trechos"][:-1]:
            t += c["ate"] - c["de"]
            marcas.append(round(t + 0.1, 2))
        marcas.append(round(max(0, dur - 0.1), 2))
        prev = os.path.join(pasta, f"{nome}_previa.mp4")
        subprocess.run([sys.executable, render, proj_path, "--rascunho", "--saida", prev, "--quadros"]
                       + [str(m) for m in marcas], check=True)
    print(f"{nome}: {dur}s, {len(leg)} legendas, {len(baixa)} pra conferir de ouvido, {len(sem_pt)} sem PT")
    for x in prob + avisos:
        print("RESOLVER: " + x)
    log(f"pronto: {proj_path}, conferir.md" + ("" if a.sem_previa else f", {nome}_previa.mp4 + quadros")
        + (", post.txt" if R.get("legenda_post") else ""))


if __name__ == "__main__":
    main()
