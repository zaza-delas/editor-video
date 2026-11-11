# Editor de vídeo do Zaza

Editor pro formato dos vídeos curtos (OmeTV e afins, 1080x1920). Cada vídeo é um `projeto.json`.
O King Bot edita o JSON; o Zaza edita pela página (`editor.html`); os dois geram o mesmo arquivo.

## Comandos

```
python3 motor/render.py  pasta/projeto.json              # vídeo final 1080x1920, -14 LUFS
python3 motor/render.py  pasta/projeto.json --rascunho   # 540x960, mais rápido, pra conferir
python3 motor/render.py  pasta/projeto.json --info       # só a linha do tempo (tempos finais de cada trecho)
python3 motor/render.py  pasta/projeto.json --quadros 1.2 5   # JPGs nesses tempos depois do render
python3 motor/editor.py  pasta/projeto.json              # gera pasta/editor.html (precisa ficar < 5 MB)
```

Velocidade medida neste servidor: ~2,5 s de render por segundo de vídeo final (o encode x264 é o gargalo).

## Formato do projeto.json

Caminhos de mídia são relativos ao projeto.json. Tempos em segundos.

```json
{
  "nome": "irlanda_v1",
  "formato": {"largura": 1080, "altura": 1920, "fps": 30, "lufs": -14},
  "midia": {"bruto": "midia/bruto.mp4", "kanye": "midia/kanye.mp4", "vineboom": "midia/vineboom.mp3"},
  "trechos": [
    {"midia": "bruto", "de": 8.433, "ate": 9.9, "volume": 1.35},
    {"midia": "bruto", "de": 17.5, "ate": 24.7, "faixas": [{"faixa": 0, "volume": 1.5}, {"faixa": 1, "volume": 1.0}]},
    {"midia": "kanye", "de": 1.25, "ate": 2.75, "encaixe": "borrado", "mudo": true}
  ],
  "legendas": [{"de": 0, "ate": 1.47, "en": "What's up?", "pt": "E aí?"}],
  "balao": {"texto": "o gangster mais perigoso da Irlanda 🇮🇪", "de": 0, "ate": 3.5},
  "sfx": [{"midia": "vineboom", "em": 1.07, "volume": 1.0}],
  "musica": {"midia": "beat", "volume": 0.15, "de": 0},
  "estilo": {"legenda": {"y": 960, "tam_en": 40, "tam_pt": 48}, "balao": {"y": 40, "tam": 40}}
}
```

- `trechos`: entram em ordem, um depois do outro. `de`/`ate` = tempo NO ORIGINAL.
  - `encaixe`: `cobrir` (padrão, preenche a tela), `borrado` (inteiro com fundo borrado, bom pra meme 16:9), `conter` (faixas pretas).
  - `zoom` (1 = normal), `volume` (1 = 100%; com `faixas`, multiplica o volume de cada faixa), `mudo`, `faixas` (mistura de faixas de áudio do arquivo, ex.: faixa do gringo + faixa do mic).
  - imagem como trecho: `{"midia": "foto", "duracao": 1.5}`.
- `legendas`, `balao`, `sfx`: tempo NO VÍDEO FINAL (use `--info` pra ver onde cada trecho cai).
  - legenda: `en` (branca, em cima da divisória) e/ou `pt` (amarela, caixa alta, embaixo). Override por legenda: `"estilo": {"cor_pt": "#7fd4ff"}` (ex.: outra pessoa falando).
  - `sfx`: `duracao` opcional corta o som.
- `estilo.legenda`: `fonte_en`, `fonte_pt` (nome do Google Fonts, tem que estar em `motor/baixar_fontes.py`), `y` (divisória entre EN e PT), `largura`, `tam_en`, `tam_pt`, `cor_en`, `cor_pt`, `contorno_en`, `contorno_pt`, `espaco`.
- `estilo.balao`: `fonte`, `y`, `tam`, `largura`, `fundo`, `cor`; `"quebra": true` no balão deixa o texto quebrar linha.

## Um comando do roteiro até a prévia (v7.3+): COMECE POR AQUI
```
python3 motor/fazer.py pasta/roteiro.json          # projeto.json + prévia 540x960 + quadros + conferir.md + post.txt
python3 motor/fazer.py pasta/roteiro.json --final  # depois de revisar o projeto.json: vídeo final 1080x1920
```
- O roteiro vem do content creator no formato de `exemplos/roteiro_modelo.json` (cortes em segundos do bruto, balão, fecho, traduções, legenda do post, regras).
- Usa só a transcrição do Zaza (`palavras`); nunca roda whisper.
- `conferir.md` lista o que resolver antes do final: regras quebradas (duração, mais de 4s sem fala nova, fecho não seco), trecho sem transcrição, legenda pra conferir de ouvido (confiança < 0.6) e legenda sem PT. Quadros: frame 0, começo de cada corte e fim.
- Pacote sempre atual (v7.9: fazer.py, render.py e rascunho.py, qualquer um que você rodar): olha todos os `editor-video-zaza*.zip` da pasta compartilhada do code guide, pega o de maior `motor/VERSAO` e, se for mais novo que `motor/VERSAO`, troca `motor/` e o LEIA-ME sozinho e roda de novo. Não precisa mais pegar zip com número.
- Medido em 10/11 (argentina, 4 cortes, 18 s): 35 s do roteiro até a prévia com quadros.

## Camadas, clipe por cima, corte do começo e ducking (v7.11, 10/11, pedido do Zaza)
- `pista` (0, 1, 2...) em legenda, sfx e `sobre`: itens em pistas diferentes podem tocar ao mesmo tempo (áudio sobre áudio, texto sobre texto). Na página: arrastar o bloco pra cima/baixo troca de pista; soltar em cima de outro na mesma pista sobe pra próxima livre. Texto em pista > 0 nasce com `estilo.y: 500` (altura própria, barra "Altura na tela").
- sfx `inicio`: de onde o som começa (borda esquerda do bloco corta o começo; o fim fica no lugar). `duracao` conta a partir do `inicio`.
- `sobre`: clipe por cima do vídeo principal. `{"midia": "kanye", "de": 0.2, "ate": 1.6, "em": 3.0, "largura": 600, "x": 240, "y": 150, "volume": 1, "mudo": false, "pista": 0}`. Imagem: `duracao` no lugar de de/ate. `largura`/`x`/`y` em pixels do quadro 1080x1920; sem `y` = centralizado na altura. Pista maior fica por cima; legenda e balão ficam por cima de tudo. Na página: aba Clipes, "+ por cima" na agulha.
- Ducking: efeitos, música e som dos clipes por cima abaixam sozinhos quando tem fala nos trechos (sidechaincompress com o áudio dos trechos). `"ducking": false` no projeto desliga.

## Muitos cortes (v7.14, 10/11)
- Mais de 6 trechos: cada trecho é renderizado sozinho antes (recorte, zoom, encaixe, volume, faixas), um de cada vez, e o grafo final lê todos como uma entrada só. Iraniana com 17 cortes: pico de memória 1,6 GB -> 280 MB, e 142 s -> 78 s. Não precisa mais do render_seco.
- Duração de cada trecho arredondada pra quadros inteiros: antes o áudio escorregava até 35 ms do vídeo com muitos cortes; agora fica a menos de 10 ms em qualquer ponto.
 (pacote v4; o fazer.py chama ele)
Em vez de cronometrar legenda por legenda, gere o projeto e só revise:
```
python3 motor/rascunho.py exemplos/X/midia/bruto.mp4 --cortes "20.4-28.25,112.7-115.95" \
    --idioma es --empilhar "0,34,560,498;680,34,560,498" --balao "texto do balão" --saida exemplos/X/projeto.json
```
- Cola o começo e o fim de cada corte nas palavras, cria as legendas no tempo do vídeo final com o texto original em `en` e `pt` vazio (você traduz), e marca `quem` (gringo/zaza) comparando as 2 faixas de áudio do OmeTV (faixa 0 = só gringo, faixa 1 = todo mundo).
- `confianca` < 0.6 = conferir de ouvido. Só essas, não todas.
- A transcrição fica em cache (`bruto.mp4.palavras.json`): rodar de novo com outros cortes é instantâneo. `--so-transcrever --de 100 --ate 160` só ouve e lista as falas (bom pra achar os cortes).
- Não tem mais plugin de transcrição: o whisper local (modelo small) é o caminho. Medido em 10/11: com a máquina livre ele ouve ~1 s de áudio por segundo (45 s de bruto em 77 s, pico 1,1 GB); com outros agentes rodando, até 3x mais lento. Por isso ele só ouve os cortes, não o bruto inteiro.
- Transcrição feita uma vez serve pra todos: quem já ouviu o bruto (ex.: o content creator procurando os cortes com `--so-transcrever`) compartilha o `bruto.mp4.palavras.json`, e quem edita passa `--palavras esse_arquivo.json`: não ouve de novo. Também aceita JSON de palavras de outra ferramenta (chaves word/start/end).
- Pressa e áudio limpo: `--modelo base` é 2,5x mais rápido, mas erra mais (no teste ouviu "Work your bitch" no lugar de "Fuck yo bitch").
- O script ouve em pedaços de até 20 s sozinho (bruto inteiro de uma vez estourava os 2 GB de memória).

## Eco do Zaza e uma transcrição por vez (pacote v7, 10/11)
- No OmeTV a voz do Zaza sai no alto-falante do gringo e volta pelo mic dele: a faixa 0 ("só o gringo") tem o Zaza de novo ~0,9 s depois (na argentina: 0,89 s, tão alto quanto a voz original). Antes do v7 toda fala do Zaza virava 2 legendas, a 2ª marcada "gringo", e o whisper embolava a fala nova com o eco da anterior.
- O v7 mede o atraso sozinho (guarda em `cache["eco"]`), o whisper ouve um mix com o eco abafado (pedaço marcado `sem_eco`) e cada palavra ganha `quem` = gringo | zaza | eco; eco sai das legendas (`--com-eco` mantém, pra conferir). Patch de quem fala/eco: editorzin; áudio limpo pro whisper e trava: code guide.
- Transcrição antiga (feita antes do v7) num bruto com eco é ouvida de novo, porque tem palavra embolada e tempo errado. Pressa: `--cache-com-eco` aceita a antiga.
- Só um whisper por vez na máquina toda (2 GB pra todos; dois juntos morrem com 137). O script trava em `/tmp/kingbot-whisper.lock`: o segundo espera o primeiro em vez de morrer.
- `--palavras` com o cache de outro agente agora respeita os pedaços que ele ouviu de verdade (antes o buraco entre dois cortes passava por "já ouvido" e saía sem legenda).

## Transcrição feita pelo Zaza no PC dele (v7.1, 10/11): caminho padrão
- Ninguém transcreve na máquina dos agentes: o Zaza transcreve e manda um JSON com `word`, `start`, `end` (lista, ou `{"words": [...]}`; `probability` opcional vira a confiança).
- `--palavras arquivo.json` usa direto (2-3 s) e NUNCA chama o whisper: corte sem transcrição no JSON sai sem legenda, com AVISO dizendo o trecho pra pedir ao Zaza. Se ele transcreveu só um trecho e os tempos começam do 0, `--palavras-de 155` (início do trecho no bruto).
- Vários trechos em arquivos separados: importe um de cada vez, um não apaga o outro.
- Quem fala e o eco continuam sendo calculados pelo áudio das 2 faixas.
- Sem `probability` no JSON, toda legenda sai com confiança 0.9: a lista "conferir de ouvido" fica vazia.

## Bruto cru do OmeTV (câmeras lado a lado): NÃO pré-renderize
Desde o pacote v3 o editor recorta e empilha as câmeras sozinho, só nos pedaços usados. Na `midia`, em vez do caminho, use:
```json
"bruto": {"arquivo": "midia/bruto_drive.mp4",
          "empilhar": [{"x": 0, "y": 34, "w": 560, "h": 498}, {"x": 680, "y": 34, "w": 560, "h": 498}]}
```
Cada recorte (em pixels do original) vira uma faixa de 1080 de largura, uma embaixo da outra (2 recortes = 2 x 960). A ordem da lista é a ordem de cima pra baixo.
Escolher a faixa de áudio do arquivo: no trecho, `"faixas": [{"faixa": 1}]` (equivale ao `-map 0:a:1`).
Isso evita recodificar o vídeo inteiro antes de começar (era o passo mais lento).

## Editor online no GitHub (v7.15, 11/11)
- Site: https://zaza-delas.github.io/editor-video/ (repo público `zaza-delas/editor-video`, só código). Projetos e mídia leve no repo PRIVADO `zaza-delas/editor-video-projetos`, em `projetos/<nome>/`.
- `python3 motor/nuvem.py subir pasta/projeto.json`: manda o projeto (e refaz a mídia leve só se os cortes/arquivos mudaram) e imprime o link `...?p=<nome>`. `--sem-midia` sobe só o json.
- `python3 motor/nuvem.py baixar pasta/projeto.json`: traz a edição que o Zaza fez no site (a local fica em `projeto.antes_nuvem.json`). Rode ANTES de renderizar o final.
- `python3 motor/nuvem.py lista` e `python3 motor/nuvem.py site` (publica `index.html` + `motor/` no repo público; o Pages leva ~1 min).
- A página salva sozinha 3 s depois de cada mudança e puxa o que mudou no repo a cada 10 s; se os dois mexeram ao mesmo tempo, ela pergunta antes de sobrescrever.
- Token: `EDITOR_GITHUB_TOKEN` ou `.segredos/github_token` numa pasta acima do motor. Só o code guide tem; nunca vai no pacote compartilhado.

## v7.16 (11/11, pedido do Zaza)
- Trecho e clipe por cima ganharam `"reduzir_ruido": 0-1` (força do RNNoise, `motor/modelos/cb.rnnn`, atraso de 10 ms já compensado) e `"realcar_voz": true` (highpass + EQ de presença + compressor + de-esser). Na página: barra "Reduzir ruído" e "Realçar voz" no trecho, no clipe e na seleção múltipla; a prévia usa o RNNoise no navegador (precisa de internet na 1ª vez).
- Página: arrastar trecho de vídeo pros lados reordena (legendas e efeitos andam junto com o conteúdo); arrastar trecho pra linha "Por cima" vira clipe por cima (levando as legendas dele); arrastar clipe por cima pra linha do vídeo vira trecho. Duplicar qualquer coisa (Ctrl+D / botão). Tela cheia (F / botão no player).
- Mídia do computador: botão Mídia, Ctrl+O ou arrastar arquivo pra página. Online, o arquivo sobe pro repo em `projetos/<nome>/midia/` (até 95 MB) e entra em `P.midia`; na lista de Clipes tem "+ no vídeo" (divide o trecho na agulha e entra no meio) e "+ por cima".
- Site: botão "<" volta pros projetos (salva antes); "Novo projeto" cria direto do site com um vídeo do PC (um trecho com o vídeo inteiro). Do lado do King Bot: `python3 motor/nuvem.py baixar <nome> [--pasta DIR]` traz o projeto e a mídia; `subir` depois gera a mídia leve.
- Corrigido: o som da prévia podia travar a página (laço sem fim) quando um trecho começava no fim exato de um pedaço da cópia leve.

## Fontes

Montserrat, Poppins, Oswald, Anton, Bebas Neue, Bangers, Luckiest Guy, Archivo Black, Lilita One, Permanent Marker. TTFs em `fontes/` (render) e as mesmas via Google Fonts na página. Fonte nova: pôr em `FONTES` do `motor/baixar_fontes.py`, rodar, e acrescentar no `<link>` e na lista `FONTES` do `editor_modelo.html`. Emoji: Noto Color Emoji nos dois (no Windows o Chrome não tem bandeira sem isso).

## Como funciona

- Legendas e balão: HTML + `motor/estilo.css` desenhados no Chromium (mesmo CSS da página do editor), viram uma camada PNG por cima.
- Vídeo e áudio: um único grafo ffmpeg; no fim, ganho fixo + limitador (-1,5 dBTP), medido e corrigido até ficar a ±0,3 do alvo (v7.7: o loudnorm linear do ffmpeg caía no modo dinâmico e entregava -16,4).
- Página (layout estilo CapCut, pedido do Zaza): arrastar e esticar blocos, split, volume em barra, desfazer/refazer (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y), seleção múltipla (Ctrl/Shift+clique, botão no celular, Ctrl+A), desativar item (H; campo `desativado: true` em legenda, sfx e balão, o render pula). Som da prévia é todo Web Audio (vídeo mudo), pra volume e SFX valerem no iPhone. `motor/editor_modelo.html` + cópia leve só dos pedaços usados (folga de 1,5 s de cada lado). Não tem rede: o Zaza baixa o projeto pelo botão (ou Ctrl+S) e manda o .json no chat. v7.10: a edição fica guardada no navegador sozinha (reabrir a mesma página volta onde parou) e fechar com edição não baixada pede confirmação. Salvar a página (HTML) NÃO leva a edição.
