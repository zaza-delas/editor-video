#!/usr/bin/env python3
"""Baixa as fontes das legendas (Google Fonts, TTF) pra pasta fontes/ e escreve fontes/fontes.css.
A página do editor carrega as mesmas famílias direto do Google Fonts, então prévia e vídeo batem.
Pra pôr fonte nova: acrescente em FONTES (nome exato do Google Fonts + pesos) e rode de novo."""
import os, re, urllib.request
AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA = os.path.join(os.path.dirname(AQUI), "fontes")
FONTES = {  # nome: pesos ("" = só o peso único da fonte)
    "Montserrat": "600;700;900", "Poppins": "600;700;900", "Oswald": "600;700",
    "Anton": "", "Bebas Neue": "", "Bangers": "", "Luckiest Guy": "",
    "Archivo Black": "", "Lilita One": "", "Permanent Marker": "",
}
def url_css():
    fam = "&".join("family=" + n.replace(" ", "+") + (f":wght@{w}" if w else "") for n, w in FONTES.items())
    return "https://fonts.googleapis.com/css2?" + fam + "&display=swap"
def main():
    req = urllib.request.Request(url_css(), headers={"User-Agent": "Mozilla/4.0"})
    css = urllib.request.urlopen(req, timeout=30).read().decode()
    saida = []
    for bloco in re.findall(r"@font-face\s*\{[^}]*\}", css):
        fam = re.search(r"font-family:\s*'([^']+)'", bloco).group(1)
        peso = re.search(r"font-weight:\s*(\d+)", bloco).group(1)
        url = re.search(r"url\((https://[^)]+\.ttf)\)", bloco).group(1)
        nome = f"{fam.replace(' ', '')}-{peso}.ttf"
        cam = os.path.join(PASTA, nome)
        if not os.path.exists(cam):
            urllib.request.urlretrieve(url, cam)
        saida.append(f'@font-face {{ font-family: "{fam}"; font-weight: {peso}; src: url("{nome}"); }}')
    with open(os.path.join(PASTA, "fontes.css"), "w") as f:
        f.write("\n".join(saida) + "\n")
    print(f"{len(saida)} arquivos em {PASTA}")
    print("link da página:", url_css())
if __name__ == "__main__":
    main()
