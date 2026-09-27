"""Steam の人気・売れ筋を 4 ページの掲示画像にまとめる (VRChat 壁新聞形式)。

出力 (site/):
  board.png     2048x2048。1024x1024 のページを 2x2 に並べたもの (左上から 1,2 / 3,4)
  data.json     掲示と同じ内容の文字データ
  history.json  日ごとの同時接続数。急上昇の計算用に前回公開分へ追記していく

データ元: Steam Web API (ISteamChartsService / IStoreBrowseService) と Steam ストアの公開 JSON。
"""
import datetime
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

UA = "vrc-steam-board/0.1"
OUT = Path(__file__).parent / "site"
PAGES_URL = "https://po6896.github.io/vrc-steam-board/"
JST = datetime.timezone(datetime.timedelta(hours=9))
PAGE = 1024
HISTORY_DAYS = 60
FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Actions (fonts-noto-cjk)
    "C:/Windows/Fonts/NotoSansJP-VF.ttf",
    "C:/Windows/Fonts/meiryo.ttc",
]
BG, FG, SUB, ACCENT, UP, DOWN = "#171a21", "#e6e6e6", "#8f98a0", "#66c0f4", "#a4d007", "#e05d44"


def get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def app_names(appids):
    ids = [{"appid": a} for a in appids]
    ctx = {"language": "japanese", "country_code": "JP"}
    d = get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
            {"input_json": json.dumps({"ids": ids, "context": ctx})})
    return {i["appid"]: i["name"] for i in d["response"].get("store_items", []) if i.get("name")}


def yen(v):
    return "無料" if not v else f"¥{v // 100:,}"


def collect():
    cc = get("https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/")["response"]["ranks"]
    mp = get("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/")["response"]["ranks"]
    fc = get("https://store.steampowered.com/api/featuredcategories", {"cc": "jp", "l": "japanese"})
    wl = get("https://store.steampowered.com/search/results/",
             {"json": 1, "filter": "popularwishlist", "cc": "jp", "l": "japanese"})["items"]
    online = get("https://store.steampowered.com/stats/userdata.json")[0]["data"][-1][1]

    names = app_names(sorted({r["appid"] for r in cc[:15] + mp[:15]}))
    name = lambda a: names.get(a, f"appid {a}")

    def store(items, n):
        seen, out = set(), []
        for i in items:  # 同じ作品の版違いが並ぶことがあるので名前で 1 件に絞る
            if i["name"] not in seen and len(out) < n:
                seen.add(i["name"])
                out.append({"appid": i["id"], "name": i["name"], "price": i["final_price"],
                            "discount": i.get("discount_percent", 0)})
        return out

    def wish(i):
        m = re.search(r"/apps/(\d+)/", i["logo"])
        return {"appid": int(m.group(1)) if m else None, "name": i["name"]}

    return {
        "generated": datetime.datetime.now(JST).isoformat(timespec="minutes"),
        "online": online,
        "concurrent": [{"rank": r["rank"], "appid": r["appid"], "name": name(r["appid"]),
                        "players": r["concurrent_in_game"], "peak": r["peak_in_game"]} for r in cc[:15]],
        "weekly": [{"rank": r["rank"], "appid": r["appid"], "name": name(r["appid"]),
                    "last_week": r.get("last_week_rank", 0), "peak": r["peak_in_game"]} for r in mp[:15]],
        "top_sellers": store(fc["top_sellers"]["items"], 8),
        "specials": store(fc["specials"]["items"], 8),
        "new_releases": store(fc["new_releases"]["items"], 8),
        "wishlist": [wish(i) for i in wl[:8]],
        "_all_concurrent": {str(r["appid"]): r["concurrent_in_game"] for r in cc},
    }


def update_history(data):
    try:
        hist = get(PAGES_URL + "history.json")
    except Exception as e:  # 初回や Pages 未公開時は空から始める
        print("history: start fresh", e)
        hist = []
    today = data["generated"][:10]
    hist = [h for h in hist if h["date"] != today]
    hist.append({"date": today, "online": data["online"], "players": data.pop("_all_concurrent")})
    return hist[-HISTORY_DAYS:]


class Page:
    def __init__(self, font_path, title, generated):
        self.img = Image.new("RGB", (PAGE, PAGE), BG)
        self.d = ImageDraw.Draw(self.img)
        self.f = lambda s: ImageFont.truetype(font_path, s)
        self.d.rectangle((0, 0, PAGE, 96), fill="#1b2838")
        self.d.text((36, 48), title, font=self.f(46), fill=FG, anchor="lm")
        foot = f"データ: Steam (store.steampowered.com)   更新 {generated[:16].replace('T', ' ')} JST"
        self.d.text((36, PAGE - 44), foot, font=self.f(20), fill=SUB, anchor="lm")
        self.d.text((36, PAGE - 18), "Steam は Valve Corporation の商標です。本掲示は Valve と無関係の非公式のものです",
                    font=self.f(16), fill=SUB, anchor="lm")
        self.y = 120

    def fit(self, text, font, width):
        if self.d.textlength(text, font=font) <= width:
            return text
        while text and self.d.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text + "…"

    def section(self, label, note=""):
        self.d.text((36, self.y + 20), label, font=self.f(32), fill=ACCENT, anchor="lm")
        if note:
            self.d.text((PAGE - 36, self.y + 20), note, font=self.f(24), fill=SUB, anchor="rm")
        self.y += 52

    def row(self, left, name, right, right_color=FG, mark="", mark_color=SUB, h=50):
        f = self.f(h * 56 // 100)
        cy = self.y + h // 2
        self.d.text((80, cy), left, font=f, fill=SUB, anchor="rm")
        right_w = self.d.textlength(right, font=f)
        self.d.text((PAGE - 36, cy), right, font=f, fill=right_color, anchor="rm")
        if mark:
            self.d.text((PAGE - 60 - right_w, cy), mark, font=self.f(h * 44 // 100), fill=mark_color, anchor="rm")
        width = PAGE - 36 - right_w - 100 - 100
        self.d.text((100, cy), self.fit(name, f, width), font=f, fill=FG, anchor="lm")
        self.d.line((36, self.y + h, PAGE - 36, self.y + h), fill="#2a3340")
        self.y += h


def render(data, font_path):
    g = data["generated"]
    pages = []

    p = Page(font_path, "Steam いま遊ばれているゲーム", g)
    p.section("同時接続数 上位 15", f"Steam 全体のオンライン {data['online']:,} 人")
    for r in data["concurrent"]:
        p.row(str(r["rank"]), r["name"], f"{r['players']:,} 人", mark=f"最大 {r['peak']:,}")
    pages.append(p)

    p = Page(font_path, "Steam 週間プレイ人数ランキング", g)
    p.section("先週からの順位変動つき 上位 15")
    for r in data["weekly"]:
        lw = r["last_week"]
        if not lw:
            mark, color = "初登場", ACCENT
        elif lw > r["rank"]:
            mark, color = f"▲{lw - r['rank']}", UP
        elif lw < r["rank"]:
            mark, color = f"▼{r['rank'] - lw}", DOWN
        else:
            mark, color = "―", SUB
        p.row(str(r["rank"]), r["name"], mark, right_color=color)
    pages.append(p)

    p = Page(font_path, "Steam 売れ筋とセール", g)
    p.section("売れ筋 上位 8")
    for i, s in enumerate(data["top_sellers"], 1):
        p.row(str(i), s["name"], yen(s["price"]), h=40)
    p.y += 14
    p.section("セール中")
    for i, s in enumerate(data["specials"], 1):
        p.row(str(i), s["name"], yen(s["price"]), mark=f"-{s['discount']}%", mark_color=UP, h=40)
    pages.append(p)

    p = Page(font_path, "Steam 新作と注目の発売予定", g)
    p.section("新作")
    for i, s in enumerate(data["new_releases"], 1):
        p.row(str(i), s["name"], yen(s["price"]), h=40)
    p.y += 14
    p.section("ウィッシュリスト上位")
    for i, s in enumerate(data["wishlist"], 1):
        p.row(str(i), s["name"], "", h=40)
    pages.append(p)

    board = Image.new("RGB", (PAGE * 2, PAGE * 2))
    for k, pg in enumerate(pages):
        board.paste(pg.img, (k % 2 * PAGE, k // 2 * PAGE))
    return board


def main():
    font_path = next(f for f in FONTS if os.path.exists(f))
    data = collect()
    hist = update_history(data)
    OUT.mkdir(exist_ok=True)
    render(data, font_path).save(OUT / "board.png", optimize=True)
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "history.json").write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    print(f"{data['generated']} online {data['online']:,} / history {len(hist)} days / font {font_path}")


if __name__ == "__main__":
    main()
