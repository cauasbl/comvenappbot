# EV CANTOS

Scanner de apostas de valor (+EV) em estilo arcade/synthwave. Compara as odds da **Betano** com a odd justa da
**Pinnacle** (já sem a margem da casa) em escanteios, cartões, gols mais/menos, resultado 1X2 e ambas marcam, e mostra
as oportunidades com a stake sugerida (¼ Kelly). Os dados vêm da API do [OddsPapi](https://oddspapi.io).

> **Aviso.** É uma ferramenta de análise, não uma recomendação de aposta e não garante lucro. O resultado de curto
> prazo é muito variável, as casas podem limitar contas, e as regras de cada mercado (por exemplo, como o cartão vermelho
> conta em "cartões") podem diferir entre casas: confira antes de apostar. Apenas maiores de 18 anos. Jogue com responsabilidade.

## Usar (Windows)
1. Baixe a pasta do programa (`app`) que foi compartilhada com você e abra `EvCantos.exe`.
2. Na primeira abertura, vá em **CONFIGURAÇÕES** e cole **a sua** chave do OddsPapi (cada pessoa usa a própria; ela fica
   salva só no seu computador, em `data\secrets.json`, e nunca vai para este repositório).
3. **INICIAR SCAN** só varre quando você manda. O custo máximo de cada varredura aparece na tela. O app **não**
   adivinha o saldo do seu plano: mostra só quantas consultas ele mesmo fez neste mês com a chave atual (chave nova =
   contagem nova). O saldo real está no painel da OddsPapi; se a cota acabar, a varredura mantém o que já achou.

## Rodar o código
```
python app.py        # abre no navegador (http://127.0.0.1:8765)
python desktop.py    # janela própria (precisa do pywebview: pip install pywebview)
python -m pytest     # testes
```
Requer Python 3.10+. O app usa só a biblioteca padrão (o pywebview é opcional).

## Gerar o .exe
```
powershell -ExecutionPolicy Bypass -File build.ps1
```
Cria um ambiente isolado em `%USERPROFILE%\EvCantosBuild`, gera o `EvCantos.exe`, instala em
`%USERPROFILE%\EvCantos\app` (dados em `%USERPROFILE%\EvCantos\data`) e cria os atalhos.
O código do app vai como arquivos ao lado do `.exe`, por isso **atualiza sem trocar o `.exe`**.

## Atualizações
Endereço que o app deve usar (tela **ATUALIZAÇÕES**):
```
https://raw.githubusercontent.com/cauasbl/comvenappbot/main/update/update.json
```
Para publicar uma versão nova:
1. Edite `version.py` (ex.: `VERSION = "0.2.0"`).
2. `python make_update.py --out update --notes "o que mudou"`
3. `git add -A && git commit -m "v0.2.0" && git push`

O app só baixa quando o usuário clica, confere o SHA-256 do pacote, e se a versão nova falhar ao abrir ele volta sozinho
para a anterior. Mudanças em `desktop.py`/`updater.py` (o "launcher") exigem gerar o `.exe` de novo.

## Segurança
- Chaves e dados pessoais ficam em `data\` e estão no `.gitignore`. Nunca coloque chaves no código.
- O pacote de atualização leva só o código e a interface, nunca `config.json`, `bot.db`, `secrets.json` ou músicas.
- Quem tiver acesso de escrita a este repositório controla o código que os usuários baixam: proteja a conta com
  verificação em duas etapas.
