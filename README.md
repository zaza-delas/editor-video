# Editor de vídeo do Zaza

Editor pros vídeos curtos (1080x1920, OmeTV e afins): uma página estilo CapCut pra editar no navegador e um motor em Python + ffmpeg que renderiza o vídeo final.

**Abrir o editor:** https://zaza-delas.github.io/editor-video/

## Como funciona

- Cada vídeo é um `projeto.json` (cortes, legendas, balão, efeitos, clipes por cima). Formato completo no [LEIA-ME.md](LEIA-ME.md).
- O King Bot monta o projeto e sobe pro repo privado `editor-video-projetos` (`motor/nuvem.py subir`).
- O Zaza abre o site, escolhe o projeto e edita. A página salva sozinha no GitHub e puxa o que o King Bot mudar a cada 10 s.
- Pra renderizar, o King Bot puxa a edição (`motor/nuvem.py baixar`) e roda `motor/render.py`.

A página pede um token do GitHub com acesso ao repo de projetos (fica só no navegador). Sem token, ela também abre um `editor.html` gerado pelo motor e baixa o `projeto.json` no Ctrl+S.

## Motor

```
python3 motor/fazer.py roteiro.json            # roteiro -> projeto.json + prévia
python3 motor/render.py projeto.json           # vídeo final, -14 LUFS
python3 motor/nuvem.py subir projeto.json      # manda pro editor online
python3 motor/nuvem.py baixar projeto.json     # traz a edição feita no site
python3 motor/pagina_online.py                 # gera site/index.html
```

Precisa de Python 3.10+, ffmpeg e Chromium (as legendas são desenhadas com o mesmo CSS da página).
