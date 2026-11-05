# Bot de alerta de preço — RTX 5060 8GB

O bot olha a busca da **Kabum** a cada 20 minutos e manda mensagem no **Telegram** quando:

- alguma RTX 5060 8GB (sem ser Ti) fica abaixo do seu `preco_alvo`, contando o cupom;
- aparece um preço menor do que o mais baixo que ele já tinha visto;
- aparece um **cupom novo** (ex.: `ZAFADINHO10`). Ele abre a página do produto, lê o desconto do cupom (por exemplo "10% OFF") e calcula o preço estimado com o cupom.

Ele roda de graça no **GitHub Actions**, então o computador pode ficar desligado.

---

## 1. Criar o bot do Telegram (5 min)

1. No Telegram, abra **@BotFather**, mande `/newbot`, escolha um nome e copie o **token** (algo como `123456:ABC-...`).
2. Abra a conversa com o seu bot novo e mande um "oi".
3. No navegador, abra `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` e copie o número que aparece em `"chat":{"id": ...}`. Esse é o **chat_id**.

## 2. Subir para o GitHub

1. Crie uma conta em github.com, se ainda não tiver.
2. Crie um repositório novo, por exemplo `bot-rtx5060`. Ele deve ser **público**: assim os minutos do Actions são ilimitados. Repositórios privados têm 2.000 min/mês, e rodar a cada 20 min gasta cerca de 2.200. O token do Telegram fica nos Secrets, que ninguém consegue ver, nem em repositório público.
3. Nesta pasta, rode:

```bash
git init -b main
```

```bash
git add . && git commit -m "bot de preço"
```

```bash
git remote add origin https://github.com/SEU_USUARIO/bot-rtx5060.git
```

```bash
git push -u origin main
```

## 3. Colocar o token do Telegram no GitHub

No repositório, vá em **Settings → Secrets and variables → Actions → New repository secret** e crie:

- `TELEGRAM_BOT_TOKEN`: o token do BotFather
- `TELEGRAM_CHAT_ID`: o número do chat

## 4. Ligar e testar

Abra a aba **Actions**, clique em **Bot de preço RTX 5060** e depois em **Run workflow**. Se a execução ficar verde, está funcionando. A partir daí ela roda sozinha a cada 20 minutos.
O GitHub pode atrasar o agendamento alguns minutos nos horários de pico.

Para mudar o preço-alvo, edite o `config.json` direto no site do GitHub (clique no lápis).

---

## Rodar no PC (opcional)

Crie um `segredos.json` (ele não vai para o GitHub):

```json
{"telegram_bot_token": "SEU_TOKEN", "telegram_chat_id": "SEU_CHAT_ID"}
```

```bash
python bot_preco.py --teste-alerta
```

```bash
python bot_preco.py
```

## Configuração (`config.json`)

| Campo | O que faz |
|---|---|
| `preco_alvo` | Preço que dispara o alerta |
| `considerar_cupom_no_alvo` | Usa o preço estimado com cupom para comparar com o alvo |
| `alertar_novo_cupom` | Avisa quando surge um código de cupom novo |
| `alertar_quando_novo_minimo` | Avisa quando o menor preço cai, mesmo acima do alvo |
| `intervalo_minutos` | Intervalo do modo loop no PC. No GitHub, quem manda é o `cron` em `.github/workflows/bot-preco.yml` |
| `filtro_*` | Garantem que é a placa RTX 5060 **8GB** e não Ti, notebook ou PC montado |

**Observações**

- O preço com cupom é uma **estimativa**: o bot aplica o desconto anunciado sobre o preço à vista. Confira no carrinho antes de comprar.
- Pichau, Terabyte, Mercado Livre e Amazon bloqueiam acesso automatizado, por isso o bot usa só a Kabum.
- O histórico fica em `historico.json`. O GitHub salva esse arquivo a cada execução, e isso também evita que o agendamento seja desativado por falta de atividade.
