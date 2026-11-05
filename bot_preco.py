"""Bot de alerta de preço — RTX 5060 8GB (Kabum), com detecção de cupons.

Uso:
    python bot_preco.py                 # roda em loop, checando a cada N minutos
    python bot_preco.py --uma-vez       # checa uma vez e sai (GitHub Actions / Agendador)
    python bot_preco.py --teste-alerta  # dispara uma notificação de teste

Telegram: defina as variáveis de ambiente TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
(no GitHub, como Secrets) ou crie um segredos.json local com
{"telegram_bot_token": "...", "telegram_chat_id": "..."}.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
SEGREDOS = BASE / "segredos.json"
HISTORICO = BASE / "historico.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR"}
FUSO_BR = timezone(timedelta(hours=-3))
MAX_PAGINAS_CUPOM = 8  # quantos produtos com cupom abrir por checagem para ler o desconto


def agora():
    return datetime.now(FUSO_BR)


def log(msg):
    print(f"[{agora():%d/%m %H:%M}] {msg}", flush=True)


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def carregar_json(caminho, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def nome_curto(p):
    return p["nome"].split(",")[0][:80]


# ---------------------------------------------------------------- Kabum
def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("Kabum mudou o layout (sem __NEXT_DATA__) ou bloqueou o acesso")
    return json.loads(m.group(1))


def buscar_kabum(termo, paginas=2):
    """Lista de produtos da busca da Kabum."""
    slug = re.sub(r"\s+", "-", termo.strip().lower())
    produtos = []
    for pag in range(1, paginas + 1):
        url = f"https://www.kabum.com.br/busca/{slug}?page_number={pag}&page_size=100"
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        itens = next_data(r.text)["props"]["pageProps"]["data"]["catalogServer"].get("data") or []
        for p in itens:
            oferta = p.get("offer") or {}
            preco = oferta.get("priceWithDiscount") or p.get("priceWithDiscount") or p.get("price")
            if not preco:
                continue
            selo = p.get("stamps") or {}
            titulo_selo = (selo.get("title") or selo.get("name") or "").strip()
            cupom = None
            if titulo_selo.upper().startswith("CUPOM"):
                cupom = titulo_selo.split(None, 1)[1].strip() if " " in titulo_selo else titulo_selo
            produtos.append({
                "id": f"kabum-{p['code']}",
                "loja": "Kabum",
                "nome": p["name"],
                "marca": ((p.get("manufacturer") or {}).get("name") or "").strip() or None,
                "imagem": p.get("image") or p.get("thumbnail"),
                "categoria": p.get("category", ""),
                "preco": float(preco),              # à vista (Pix), já com oferta relâmpago
                "disponivel": bool(p.get("available")),
                "oferta": oferta.get("name"),
                "cupom": cupom,
                "cupom_desconto": None,             # preenchido depois: {"tipo": "%"|"R$", "valor": x}
                "preco_com_cupom": None,
                "url": f"https://www.kabum.com.br/produto/{p['code']}/{p.get('friendlyName', '')}",
            })
        if len(itens) < 100:
            break
    return produtos


def ler_desconto_cupom(p):
    """Abre a página do produto e lê o texto do cupom ('ganhe 10% OFF' / 'R$ 100 OFF')."""
    r = requests.get(p["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    # o texto do selo aparece no HTML e no JSON da página: busca direto no texto bruto
    texto = unescape(r.text)
    m = re.search(r"cupom\s+" + re.escape(p["cupom"]) + r"[^\"<]{0,80}?(\d+(?:[.,]\d+)?)\s*%\s*OFF",
                  texto, re.I)
    if m:
        return {"tipo": "%", "valor": float(m.group(1).replace(",", ".")),
                "texto": f"{m.group(1)}% OFF"}
    m = re.search(r"cupom\s+" + re.escape(p["cupom"]) + r"[^\"<]{0,80}?R\$\s*([\d.]+(?:,\d+)?)\s*OFF",
                  texto, re.I)
    if m:
        valor = float(m.group(1).replace(".", "").replace(",", "."))
        return {"tipo": "R$", "valor": valor, "texto": f"R$ {m.group(1)} OFF"}
    return None


def aplicar_cupons(produtos):
    """Lê o desconto dos cupons (dos produtos mais baratos) e calcula o preço estimado."""
    com_cupom = [p for p in produtos if p["cupom"]][:MAX_PAGINAS_CUPOM]
    cache = {}
    for p in com_cupom:
        try:
            if p["cupom"] not in cache or cache[p["cupom"]] is None:
                cache[p["cupom"]] = ler_desconto_cupom(p)
            desc = cache[p["cupom"]]
        except Exception as e:
            log(f"Não consegui ler o cupom {p['cupom']}: {e}")
            continue
        if not desc:
            continue
        p["cupom_desconto"] = desc
        if desc["tipo"] == "%":
            p["preco_com_cupom"] = round(p["preco"] * (1 - desc["valor"] / 100), 2)
        else:
            p["preco_com_cupom"] = round(max(p["preco"] - desc["valor"], 0), 2)


def preco_efetivo(p, cfg):
    if cfg.get("considerar_cupom_no_alvo", True) and p["preco_com_cupom"]:
        return p["preco_com_cupom"]
    return p["preco"]


def filtrar(produtos, cfg):
    obrig = [re.compile(x, re.I) for x in cfg["filtro_nome_obrigatorio"]]
    proib = [re.compile(x, re.I) for x in cfg["filtro_nome_proibido"]]
    cat = cfg.get("filtro_categoria", "").lower()
    out = []
    for p in produtos:
        if not p["disponivel"]:
            continue
        if cat and cat not in p["categoria"].lower():
            continue
        if not all(r.search(p["nome"]) for r in obrig):
            continue
        if any(r.search(p["nome"]) for r in proib):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p["preco"])


# ---------------------------------------------------------------- Alertas
def credenciais_telegram():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        s = carregar_json(SEGREDOS, {})
        token = token or s.get("telegram_bot_token")
        chat = chat or s.get("telegram_chat_id")
    return token, chat


def notificar_windows(titulo, texto):
    if os.name != "nt":
        return
    titulo = titulo.replace("'", "''")
    texto = texto.replace("'", "''")
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode('{titulo}')) > $null
$n.Item(1).AppendChild($t.CreateTextNode('{texto}')) > $null
$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20)
    except Exception as e:
        log(f"Falha na notificação do Windows: {e}")


def notificar_telegram(texto, imagem=None):
    """Envia foto com legenda; se não houver imagem ou a foto falhar, envia só o texto."""
    token, chat = credenciais_telegram()
    if not (token and chat):
        return
    api = f"https://api.telegram.org/bot{token}"
    try:
        if imagem:
            r = requests.post(f"{api}/sendPhoto", timeout=30,
                              data={"chat_id": chat, "photo": imagem, "caption": texto[:1024]})
            if r.ok:
                return
            log(f"Telegram recusou a foto ({r.status_code}); enviando só texto.")
        r = requests.post(f"{api}/sendMessage", timeout=20,
                          data={"chat_id": chat, "text": texto, "disable_web_page_preview": "true"})
        if not r.ok:
            log(f"Telegram recusou a mensagem: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log(f"Falha no Telegram: {e}")


def alertar(cfg, motivo, p):
    preco_txt = brl(p["preco"])
    if p.get("preco_com_cupom"):
        preco_txt = f"{brl(p['preco_com_cupom'])} com cupom ({brl(p['preco'])} sem)"
    titulo = f"{motivo}: {preco_txt}"
    log(f"ALERTA — {titulo} — {nome_curto(p)}")
    if cfg.get("notificacao_windows", True):
        marca = f"{p['marca']} · " if p.get("marca") else ""
        notificar_windows(titulo, f"{marca}{nome_curto(p)} ({p['loja']})")

    linhas = [f"🔔 {motivo}"]
    if p.get("marca"):
        linhas.append(f"🏷️ Marca: {p['marca'].upper()}")
    linhas += [p["nome"], "", f"💰 À vista (Pix): {brl(p['preco'])}"]
    if p.get("cupom"):
        desc = p.get("cupom_desconto")
        linhas.append(f"🎟️ Cupom: {p['cupom']}" + (f" ({desc['texto']})" if desc else ""))
        if p.get("preco_com_cupom"):
            linhas.append(f"✅ Estimado com cupom: {brl(p['preco_com_cupom'])}")
    if p.get("oferta"):
        linhas.append(f"⚡ Oferta: {p['oferta']}")
    linhas += [f"🏪 {p['loja']}", p["url"]]
    notificar_telegram("\n".join(linhas), p.get("imagem"))


# ---------------------------------------------------------------- Ciclo
def checar(cfg):
    hist = carregar_json(HISTORICO, {})
    hist.setdefault("minimo_historico", None)
    hist.setdefault("alertados", {})
    hist.setdefault("cupons_vistos", [])
    hist.setdefault("leituras", [])

    produtos = filtrar(buscar_kabum(cfg["busca"]), cfg)
    if not produtos:
        log("Nenhuma RTX 5060 8GB disponível encontrada.")
        return
    aplicar_cupons(produtos)
    produtos.sort(key=lambda p: preco_efetivo(p, cfg))

    mais_barato = produtos[0]
    efetivo_min = preco_efetivo(mais_barato, cfg)
    log(f"{len(produtos)} ofertas. Mais barata: {brl(efetivo_min)} — {nome_curto(mais_barato)}")
    for p in produtos[:5]:
        extra = f"  (cupom {p['cupom']}: {brl(p['preco_com_cupom'])})" if p["preco_com_cupom"] else (
            f"  (cupom {p['cupom']})" if p["cupom"] else "")
        log(f"   {brl(p['preco']):>13}  {nome_curto(p)[:60]}{extra}")

    alvo = float(cfg["preco_alvo"])
    alertados = hist["alertados"]
    ja_alertado = set()

    # 1) abaixo do preço-alvo (alerta de novo se aquele produto cair mais)
    for p in produtos:
        ef = preco_efetivo(p, cfg)
        if ef <= alvo and ef < alertados.get(p["id"], float("inf")):
            alertar(cfg, "Abaixo do alvo", p)
            alertados[p["id"]] = ef
            ja_alertado.add(p["id"])

    # 2) novo menor preço já visto pelo bot
    minimo = hist["minimo_historico"]
    if (minimo is not None and efetivo_min < minimo and cfg.get("alertar_quando_novo_minimo")
            and mais_barato["id"] not in ja_alertado):
        alertar(cfg, "Novo menor preço", mais_barato)
        ja_alertado.add(mais_barato["id"])
    if minimo is None or efetivo_min < minimo:
        hist["minimo_historico"] = efetivo_min

    # 3) cupom novo apareceu (avisa uma vez por código, mostrando a placa mais barata com ele)
    primeira_execucao = not hist["leituras"]
    for codigo in dict.fromkeys(p["cupom"] for p in produtos if p["cupom"]):
        if codigo in hist["cupons_vistos"]:
            continue
        hist["cupons_vistos"].append(codigo)
        if cfg.get("alertar_novo_cupom", True) and not primeira_execucao:
            p = next(x for x in produtos if x["cupom"] == codigo)
            if p["id"] not in ja_alertado:
                alertar(cfg, f"Cupom novo: {codigo}", p)
        elif primeira_execucao:
            log(f"Cupom ativo (primeira execução, sem alerta): {codigo}")
    hist["cupons_vistos"] = hist["cupons_vistos"][-100:]

    hist["leituras"].append({"quando": agora().isoformat(timespec="minutes"),
                             "preco": mais_barato["preco"],
                             "preco_com_cupom": mais_barato["preco_com_cupom"],
                             "cupom": mais_barato["cupom"],
                             "produto": mais_barato["nome"][:120],
                             "url": mais_barato["url"]})
    hist["leituras"] = hist["leituras"][-500:]
    HISTORICO.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = carregar_json(CONFIG, None)
    if cfg is None:
        sys.exit("config.json não encontrado ou inválido.")

    if "--teste-alerta" in sys.argv:
        token, chat = credenciais_telegram()
        log("Telegram configurado." if token and chat else "Telegram NÃO configurado.")
        # usa a placa mais barata de verdade, para a mensagem sair igual a um alerta real
        produtos = filtrar(buscar_kabum(cfg["busca"]), cfg)
        aplicar_cupons(produtos)
        produtos.sort(key=lambda p: preco_efetivo(p, cfg))
        alertar(cfg, "Teste do bot (mais barata agora)", produtos[0])
        return

    log(f"Monitorando '{cfg['busca']}' — alvo {brl(float(cfg['preco_alvo']))}")
    erro = False
    while True:
        try:
            checar(cfg)
        except Exception as e:
            erro = True
            log(f"Erro na checagem: {e}")
        if "--uma-vez" in sys.argv:
            sys.exit(1 if erro else 0)
        time.sleep(int(cfg["intervalo_minutos"]) * 60)


if __name__ == "__main__":
    main()
