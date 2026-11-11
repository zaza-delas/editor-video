#!/usr/bin/env python3
"""Gera a versão online do editor (site/index.html, pro GitHub Pages).

Mesmo editor da página do chat (editor_modelo.html + estilo.css), mas sem mídia embutida:
abre o projeto do repo privado (zaza-delas/editor-video-projetos), salva sozinho lá e puxa o
que o King Bot mudar. Também abre um editor.html que veio no chat (aí só baixa o json).

Uso: python3 motor/pagina_online.py [--saida site/index.html]
"""
import argparse
import os

AQUI = os.path.dirname(os.path.abspath(__file__))


def gerar(saida):
    modelo = open(os.path.join(AQUI, "editor_modelo.html"), encoding="utf-8").read()
    css = open(os.path.join(AQUI, "estilo.css"), encoding="utf-8").read()
    carregador = open(os.path.join(AQUI, "site_carregador.html"), encoding="utf-8").read()
    versao = open(os.path.join(AQUI, "VERSAO")).read().strip()
    assert modelo.count("<script>") == 1 and "/*__DADOS__*/null" in modelo
    html = (modelo.replace("/*__CSS_QUADRO__*/", css).replace("__TITULO__", "Editor do Zaza")
            .replace("/*__DADOS__*/null", "window.__DADOS")
            .replace("<script>", '<script type="text/plain" id="principal">')
            .replace("</body>", carregador + f"\n<!-- editor v{versao} -->\n</body>"))
    os.makedirs(os.path.dirname(os.path.abspath(saida)), exist_ok=True)
    with open(saida, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"site: {saida} ({os.path.getsize(saida) / 1e3:.0f} kB, v{versao})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=os.path.join(os.path.dirname(AQUI), "site", "index.html"))
    gerar(ap.parse_args().saida)
