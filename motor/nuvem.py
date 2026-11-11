#!/usr/bin/env python3
"""Editor online: sobe o projeto pro GitHub do Zaza e puxa a edição que ele fez no site.

Site:     https://zaza-delas.github.io/editor-video/   (repo público zaza-delas/editor-video)
Projetos: repo PRIVADO zaza-delas/editor-video-projetos, em projetos/<nome>/
          projeto.json (o mesmo formato do editor), midias.json (mídia leve que a página toca),
          midias.chave (pra não refazer a mídia se os cortes não mudaram) e projetos/indice.json.

Uso:
  python3 motor/nuvem.py subir  pasta/projeto.json [--folga 1.5] [--sem-midia]
  python3 motor/nuvem.py baixar pasta/projeto.json     # traz a edição do site (guarda a local em projeto.antes_nuvem.json)
  python3 motor/nuvem.py baixar <nome> [--pasta DIR]   # projeto criado no site: baixa json + mídia (padrão projetos/<nome>)
  python3 motor/nuvem.py lista
  python3 motor/nuvem.py site                          # publica a página + o motor no repo público

Token: variável EDITOR_GITHUB_TOKEN ou arquivo .segredos/github_token numa pasta acima do motor.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)

DONO = "zaza-delas"
REPO_PROJ = "editor-video-projetos"
REPO_SITE = "editor-video"
SITE = f"https://{DONO}.github.io/{REPO_SITE}/"
CACHE = os.path.expanduser("~/.cache/editor-video-nuvem")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def token():
    t = os.environ.get("EDITOR_GITHUB_TOKEN", "").strip()
    d = AQUI
    while not t and d != os.path.dirname(d):
        f = os.path.join(d, ".segredos", "github_token")
        if os.path.isfile(f):
            t = open(f).read().strip()
        d = os.path.dirname(d)
    if not t:
        sys.exit("ERRO: sem token do GitHub (EDITOR_GITHUB_TOKEN ou .segredos/github_token)")
    return t


def git(pasta, *args, ok=False):
    r = subprocess.run(["git", "-C", pasta, *args], capture_output=True, text=True)
    if r.returncode and not ok:
        msg = (r.stderr or r.stdout).replace(token(), "***")
        sys.exit(f"ERRO git {' '.join(args[:2])}: {msg.strip()}")
    return r.stdout.strip()


def clone(repo):
    """Cópia local do repo, atualizada com o GitHub."""
    pasta = os.path.join(CACHE, repo)
    url = f"https://x-access-token:{token()}@github.com/{DONO}/{repo}.git"
    if not os.path.isdir(os.path.join(pasta, ".git")):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", url, pasta], capture_output=True, text=True)
        if r.returncode:
            sys.exit("ERRO clone: " + r.stderr.replace(token(), "***"))
    git(pasta, "remote", "set-url", "origin", url)
    git(pasta, "config", "user.name", "zaza-delas")
    git(pasta, "config", "user.email", "261479457+zaza-delas@users.noreply.github.com")
    git(pasta, "fetch", "-q", "origin")
    git(pasta, "reset", "-q", "--hard", "origin/main")
    return pasta


def enviar(pasta, msg):
    git(pasta, "add", "-A")
    if not git(pasta, "status", "--porcelain"):
        log("nada mudou no GitHub")
        return False
    git(pasta, "commit", "-q", "-m", msg)
    for _ in range(3):
        if subprocess.run(["git", "-C", pasta, "push", "-q", "origin", "HEAD:main"], capture_output=True).returncode == 0:
            return True
        git(pasta, "pull", "-q", "--rebase", "origin", "main")
    sys.exit("ERRO: push não foi (tentei 3 vezes)")


def chave_midia(p, folga):
    """Muda quando muda o que a mídia leve precisa cobrir (cortes, clipes por cima, arquivos)."""
    arqs = {n: [os.path.basename(c), os.path.getsize(c)] for n, c in sorted(p["_midia"].items())}
    usos = [[t["midia"], t.get("de"), t.get("ate"), t.get("duracao")] for t in p["trechos"]]
    usos += [[o["midia"], o.get("de"), o.get("ate"), o.get("duracao")] for o in p.get("sobre", [])]
    extra = [[s["midia"]] for s in p.get("sfx", [])] + [[(p.get("musica") or {}).get("midia")]]
    return hashlib.sha1(json.dumps([arqs, usos, extra, folga, p.get("midia")], sort_keys=True).encode()).hexdigest()


def agora():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        return datetime.datetime.now()


def subir(caminho, folga, sem_midia):
    from render import ler_projeto
    from editor import montar_dados, limpo
    p = ler_projeto(caminho)
    nome = p.get("nome") or os.path.basename(os.path.dirname(os.path.abspath(caminho)))
    repo = clone(REPO_PROJ)
    dest = os.path.join(repo, "projetos", nome)
    os.makedirs(dest, exist_ok=True)
    chave = chave_midia(p, folga)
    arq_chave = os.path.join(dest, "midias.chave")
    velha = open(arq_chave).read().strip() if os.path.exists(arq_chave) else ""
    if sem_midia and not os.path.exists(os.path.join(dest, "midias.json")):
        sys.exit("ERRO: --sem-midia mas o projeto ainda não tem mídia no GitHub")
    if not sem_midia and chave != velha:
        log("mídia leve: gerando...")
        dados, tam, _ = montar_dados(caminho, folga)
        with open(os.path.join(dest, "midias.json"), "w", encoding="utf-8") as f:
            json.dump(dados["midias"], f, ensure_ascii=False, separators=(",", ":"))
        open(arq_chave, "w").write(chave + "\n")
        log(f"mídia leve: {os.path.getsize(os.path.join(dest, 'midias.json')) / 1e6:.1f} MB")
    else:
        log("mídia leve: igual, não refiz")
    with open(os.path.join(dest, "projeto.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(limpo(p), ensure_ascii=False, indent=2) + "\n")
    # índice que a página lista (mais novo primeiro)
    ind_arq = os.path.join(repo, "projetos", "indice.json")
    ind = json.load(open(ind_arq)) if os.path.exists(ind_arq) else []
    ind = [x for x in ind if x["nome"] != nome and os.path.isdir(os.path.join(repo, "projetos", x["nome"]))]
    ind.insert(0, {"nome": nome, "quando": agora().strftime("%d/%m %H:%M")})
    with open(ind_arq, "w", encoding="utf-8") as f:
        json.dump(ind, f, ensure_ascii=False, indent=1)
    enviar(repo, f"King Bot: {nome}")
    print(f"{SITE}?p={nome}")


def trazer_midia(repo, nome, pasta_local):
    """Mídia que o Zaza puxou do computador no site (projetos/<nome>/midia/) vem pra pasta local."""
    origem = os.path.join(repo, "projetos", nome, "midia")
    vindos = []
    if os.path.isdir(origem):
        for n in sorted(os.listdir(origem)):
            src, dst = os.path.join(origem, n), os.path.join(pasta_local, "midia", n)
            if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
            vindos.append(n)
    return vindos


def baixar(alvo, pasta=None):
    """alvo = projeto.json local, ou o nome de um projeto criado no site (vai pra --pasta ou projetos/<nome>)."""
    repo = clone(REPO_PROJ)
    if os.path.isfile(alvo):
        caminho = alvo
        local = json.load(open(caminho, encoding="utf-8"))
        nome = local.get("nome") or os.path.basename(os.path.dirname(os.path.abspath(caminho)))
    else:
        nome = alvo
        caminho = os.path.join(pasta or os.path.join(RAIZ, "projetos", nome), "projeto.json")
        local = json.load(open(caminho, encoding="utf-8")) if os.path.exists(caminho) else {}
    arq = os.path.join(repo, "projetos", nome, "projeto.json")
    if not os.path.exists(arq):
        sys.exit(f"ERRO: {nome} não está no GitHub")
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    vindos = trazer_midia(repo, nome, os.path.dirname(os.path.abspath(caminho)))
    if vindos:
        print(f"mídia do site: {', '.join(vindos)}")
    remoto = json.load(open(arq, encoding="utf-8"))
    from editor import CAMPOS
    novo = dict(remoto)
    for k, v in local.items():  # campos fora do formato da página continuam
        if k not in CAMPOS:
            novo[k] = v
    if novo == local:
        print("igual: o site não tem edição nova")
        return
    mud = [k for k in sorted(set(novo) | set(local)) if novo.get(k) != local.get(k)]
    if local:
        shutil.copy(caminho, os.path.splitext(caminho)[0] + ".antes_nuvem.json")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(json.dumps(novo, ensure_ascii=False, indent=2) + "\n")
    ultimo = git(repo, "log", "-1", "--format=%an, %cr", "--", f"projetos/{nome}/projeto.json")
    print(f"{caminho}: atualizado com a edição do site ({ultimo}); mudou: {', '.join(mud)}")


def lista():
    repo = clone(REPO_PROJ)
    ind_arq = os.path.join(repo, "projetos", "indice.json")
    for x in (json.load(open(ind_arq)) if os.path.exists(ind_arq) else []):
        print(f"{x['quando']}  {x['nome']}  {SITE}?p={x['nome']}")


def publicar_site():
    import pagina_online
    repo = clone(REPO_SITE)
    pagina_online.gerar(os.path.join(repo, "index.html"))
    open(os.path.join(repo, ".nojekyll"), "w").close()
    destm = os.path.join(repo, "motor")
    shutil.rmtree(destm, ignore_errors=True)
    os.makedirs(destm)
    for n in sorted(os.listdir(AQUI)):
        if n.endswith((".py", ".html", ".css")) or n == "VERSAO":
            shutil.copy(os.path.join(AQUI, n), destm)
    if os.path.isdir(os.path.join(AQUI, "modelos")):
        shutil.copytree(os.path.join(AQUI, "modelos"), os.path.join(destm, "modelos"))
    shutil.copy(os.path.join(RAIZ, "LEIA-ME.md"), repo)
    os.makedirs(os.path.join(repo, "exemplos"), exist_ok=True)
    shutil.copy(os.path.join(RAIZ, "exemplos", "roteiro_modelo.json"), os.path.join(repo, "exemplos"))
    readme = os.path.join(RAIZ, "README_github.md")
    if os.path.exists(readme):
        shutil.copy(readme, os.path.join(repo, "README.md"))
    open(os.path.join(repo, ".gitignore"), "w").write("__pycache__/\n*.mp4\n*.mp3\n.segredos/\n")
    v = open(os.path.join(AQUI, "VERSAO")).read().strip()
    if enviar(repo, f"editor v{v}"):
        print(f"publicado v{v}: {SITE} (o GitHub Pages leva ~1 min pra atualizar)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["subir", "baixar", "lista", "site"])
    ap.add_argument("projeto", nargs="?")
    ap.add_argument("--folga", type=float, default=1.5)
    ap.add_argument("--sem-midia", action="store_true")
    ap.add_argument("--pasta", help="baixar <nome>: pasta local do projeto (padrão projetos/<nome>)")
    a = ap.parse_args()
    if a.acao in ("subir", "baixar") and not a.projeto:
        ap.error("faltou o projeto.json")
    if a.acao == "subir":
        subir(a.projeto, a.folga, a.sem_midia)
    elif a.acao == "baixar":
        baixar(a.projeto, a.pasta)
    elif a.acao == "lista":
        lista()
    else:
        publicar_site()


if __name__ == "__main__":
    main()
