"""Atualização sozinha do editor: todo comando (fazer, render, rascunho) confere se o code guide
soltou versão mais nova na pasta compartilhada e, se sim, troca motor/ e o LEIA-ME e roda de novo.
KINGBOT_SEM_ATUALIZAR=1 pula (os subcomandos chamados pelo fazer.py já herdam isso)."""
import os
import re
import sys
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
CODE_GUIDE = "5e3eba9f-5e75-4be8-83ac-d2c836619992"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def versao(txt):
    return tuple(int(x) for x in re.findall(r"\d+", txt or "0"))


# ------------------------------------------------------------------ pacote sempre atual

def achar_pacote():
    """Pacote mais novo na pasta compartilhada do code guide. O compartilhamento não sobrescreve
    (sai 'editor-video-zaza-atual (2).zip' etc.), então olha todos e fica com a maior VERSAO."""
    import glob
    d = AQUI
    while d != os.path.dirname(d):
        pasta = os.path.join(d, "_compartilhados", CODE_GUIDE)
        if os.path.isdir(pasta):
            melhor = (None, ())
            for z in glob.glob(os.path.join(pasta, "editor-video-zaza*.zip")):
                try:
                    with zipfile.ZipFile(z) as zf:
                        vv = [n for n in zf.namelist() if n.endswith("motor/VERSAO")]
                        v = versao(zf.read(vv[0]).decode()) if vv else ()
                except (zipfile.BadZipFile, OSError):
                    continue
                if v > melhor[1]:
                    melhor = (z, v)
            return melhor[0]
        d = os.path.dirname(d)
    return None


def atualizar(script=None):
    if os.environ.get("KINGBOT_SEM_ATUALIZAR") == "1":
        return
    os.environ["KINGBOT_SEM_ATUALIZAR"] = "1"  # quem for chamado daqui pra frente não confere de novo
    z = achar_pacote()
    if not z:
        log("aviso: não achei o pacote atual na pasta compartilhada; seguindo com esta versão")
        return
    meu = open(os.path.join(AQUI, "VERSAO")).read().strip() if os.path.exists(os.path.join(AQUI, "VERSAO")) else "0"
    with zipfile.ZipFile(z) as zf:
        nomes = zf.namelist()
        vv = [n for n in nomes if n.endswith("motor/VERSAO")]
        if not vv:
            return
        novo = zf.read(vv[0]).decode().strip()
        if versao(novo) <= versao(meu):
            log(f"editor v{meu} (atual)")
            return
        pre = vv[0][: -len("motor/VERSAO")]
        for n in nomes:
            rel = n[len(pre):]
            if n.endswith("/") or not (rel.startswith("motor/") or rel == "LEIA-ME.md"):
                continue
            destino = os.path.join(RAIZ, rel)
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino, "wb") as f:
                f.write(zf.read(n))
    log(f"editor atualizado sozinho: v{meu} -> v{novo} (motor/ e LEIA-ME). Rodando de novo com a versão nova...")
    script = os.path.abspath(script or sys.argv[0])
    os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])


