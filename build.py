"""Steam の人気・売れ筋を 4 ページの掲示画像にまとめる (VRChat 壁新聞形式)。

出力 (site/):
  board.png     2048x2048。1024x1024 のページを 2x2 に並べたもの (左上から 1,2 / 3,4)
  data.json     掲示と同じ内容の文字データ。rows はページごとの行の位置 (ワールド側で押した行を引く)
  history.json  日ごとの同時接続数。急上昇の計算用に前回公開分へ追記していく

データ元: Steam Web API (ISteamChartsService / IStoreBrowseService) と Steam ストアの公開 JSON。
"""
import datetime
import io
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
ASSET_URL = "https://shared.akamai.steamstatic.com/store_item_assets/"
JST = datetime.timezone(datetime.timedelta(hours=9))
PAGE = 1024
HISTORY_DAYS = 60
# (標準, 太字)。可変フォントは同じファイルを太さ違いで開く。見た目の決まりは DESIGN.md
FONTS = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Actions (fonts-noto-cjk)
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("C:/Windows/Fonts/NotoSansJP-VF.ttf", "C:/Windows/Fonts/NotoSansJP-VF.ttf"),
]
PAPER, PAPER_ALT, INK, SUB, RULE = "#F5F2EA", "#ECE7DB", "#1E2428", "#5E676E", "#D5CEBF"
ACCENT, UP, DOWN = "#0A6F78", "#1F6B2C", "#B63B28"
MARGIN, HEAD_H, HEAD_RULE, SECTION_H, FOOT_H, RANK_W, PAD = 40, 112, 6, 56, 104, 84, 16
TITLE_PX, PAGENO_PX, SECTION_PX, NOTE_PX, MARK_PX, SOURCE_PX, TM_PX = 50, 32, 32, 32, 30, 28, 26
ICON_INSET = 6  # 見出し画像の上下の余白
RANK_ROWS, RANK_H = 10, 72        # ページ 1・2
STORE_ROWS, STORE_H, STORE_GAP = 5, 64, 24  # ページ 3・4


def get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def app_info(appids):
    """appid ごとの日本語名と、小さい見出し画像 (231x87) の URL"""
    ids = [{"appid": a} for a in appids]
    ctx = {"language": "japanese", "country_code": "JP"}
    d = get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
            {"input_json": json.dumps({"ids": ids, "context": ctx, "data_request": {"include_assets": True}})})
    names, capsules = {}, {}
    for i in d["response"].get("store_items", []):
        if i.get("name"):
            names[i["appid"]] = i["name"]
        a = i.get("assets", {})
        if a.get("small_capsule"):
            capsules[i["appid"]] = ASSET_URL + a["asset_url_format"].replace("${FILENAME}", a["small_capsule"])
    return names, capsules


def fetch_image(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def yen(v):
    return "無料" if not v else f"¥{v // 100:,}"


def collect():
    cc = get("https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/")["response"]["ranks"]
    mp = get("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/")["response"]["ranks"]
    fc = get("https://store.steampowered.com/api/featuredcategories", {"cc": "jp", "l": "japanese"})
    wl = get("https://store.steampowered.com/search/results/",
             {"json": 1, "filter": "popularwishlist", "cc": "jp", "l": "japanese"})["items"]
    online = get("https://store.steampowered.com/stats/userdata.json")[0]["data"][-1][1]

    ids = {r["appid"] for r in cc[:15] + mp[:15]}
    ids |= {i["id"] for k in ("top_sellers", "specials", "new_releases") for i in fc[k]["items"]}
    ids |= {int(m.group(1)) for i in wl[:8] if (m := re.search(r"/apps/(\d+)/", i["logo"]))}
    names, capsules = app_info(sorted(ids))
    name = lambda a: names.get(a, f"appid {a}")

    def store(items, n):
        seen, out = set(), []
        for i in items:  # 同じ作品の版違いが並ぶことがあるので名前で 1 件に絞る
            if i["name"] not in seen and len(out) < n:
                seen.add(i["name"])
                # type 1 はセット商品 (id は appid でなく sub の番号)
                out.append({"appid": i["id"], "sub": i.get("type") == 1, "name": i["name"], "price": i["final_price"],
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
        "_capsules": capsules,
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


def font(paths, px, bold):
    f = ImageFont.truetype(paths[bold], px)
    if paths[0] == paths[1]:  # 可変フォント
        f.set_variation_by_axes([700 if bold else 400])
    return f


class Page:
    def __init__(self, fonts, title, pageno, generated, icons):
        self.img = Image.new("RGB", (PAGE, PAGE), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.f = lambda s, bold=False: font(fonts, s, bold)
        # 見出しは帯にせず紙の上に墨で書き、下に太い罫を引く (隣の壁新聞と同じ質感にする)
        self.d.text((MARGIN, HEAD_H // 2), title, font=self.f(TITLE_PX, True), fill=INK, anchor="lm")
        self.d.text((PAGE - MARGIN, HEAD_H // 2), f"{pageno} / 4", font=self.f(PAGENO_PX, True), fill=SUB, anchor="rm")
        self.d.rectangle((MARGIN, HEAD_H - HEAD_RULE, PAGE - MARGIN, HEAD_H - 1), fill=INK)
        top = PAGE - FOOT_H
        self.d.line((MARGIN, top, PAGE - MARGIN, top), fill=RULE, width=2)
        src = f"データ: Steam (store.steampowered.com)  更新 {generated[:16].replace('T', ' ')} JST"
        self.d.text((MARGIN, top + FOOT_H // 3), src, font=self.f(SOURCE_PX), fill=SUB, anchor="lm")
        self.d.text((MARGIN, top + FOOT_H * 3 // 4), "Steam は Valve Corporation の商標。本掲示は Valve と無関係の非公式のもの",
                    font=self.f(TM_PX), fill=SUB, anchor="lm")
        self.y = HEAD_H + PAD // 2
        self.n = 0
        self.icons = icons
        self.rows = []

    def fit(self, text, font, width):
        if self.d.textlength(text, font=font) <= width:
            return text
        while text and self.d.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text + "…"

    def section(self, label, note="", note_color=SUB):
        cy = self.y + SECTION_H // 2
        self.d.text((MARGIN, cy), label, font=self.f(SECTION_PX, True), fill=ACCENT, anchor="lm")
        if note:
            self.d.text((PAGE - MARGIN, cy), note, font=self.f(NOTE_PX), fill=note_color, anchor="rm")
        self.y += SECTION_H
        self.d.line((MARGIN, self.y - 1, PAGE - MARGIN, self.y - 1), fill=RULE, width=2)
        self.n = 0

    def row(self, rank, name, right, right_color=INK, mark="", mark_color=SUB, h=RANK_H, ref=None, appid=None):
        # ref = (data.json の一覧の名前, 番号)。ワールド側で行を押したときに詳細を引けるよう、行の位置と一緒に書き出す
        if ref:
            self.rows.append({"y0": self.y, "y1": self.y + h, "list": ref[0], "index": ref[1]})
        self.n += 1
        if self.n % 2 == 0:
            self.d.rectangle((MARGIN, self.y, PAGE - MARGIN, self.y + h - 1), fill=PAPER_ALT)
        cy = self.y + h // 2
        name_x = MARGIN + RANK_W + PAD
        self.d.text((MARGIN + RANK_W, cy), rank, font=self.f(h * 62 // 100, True), fill=ACCENT, anchor="rm")
        fr = self.f(h * 52 // 100, True)
        x = PAGE - MARGIN - PAD
        self.d.text((x, cy), right, font=fr, fill=right_color, anchor="rm")
        x -= self.d.textlength(right, font=fr)
        if mark:
            fm = self.f(MARK_PX, True)
            x -= PAD * 2
            self.d.text((x, cy), mark, font=fm, fill=mark_color, anchor="rm")
            x -= self.d.textlength(mark, font=fm)
        # 見出し画像の枠は画像が無い行 (セット商品など) も空けて、作品名の頭をそろえる
        ih = h - ICON_INSET * 2
        iw = ih * 231 // 87
        icon = self.icons.get(appid)
        if icon:
            self.img.paste(icon.resize((iw, ih), Image.LANCZOS), (name_x, self.y + ICON_INSET))
        name_x += iw + PAD
        fn = self.f(h * 56 // 100, True)
        self.d.text((name_x, cy), self.fit(name, fn, x - PAD * 2 - name_x), font=fn, fill=INK, anchor="lm")
        self.y += h


def load_icons(capsules):
    icons = {}
    for appid, url in capsules.items():
        try:
            icons[appid] = fetch_image(url)
        except Exception as e:  # 画像が取れない作品は文字だけで出す
            print("icon", appid, e)
    return icons


def render(data, fonts, icons):
    g = data["generated"]
    pages = []

    p = Page(fonts, "Steam いま遊ばれているゲーム", 1, g, icons)
    p.section(f"同時接続数 上位 {RANK_ROWS}", f"Steam 全体 {data['online']:,} 人", note_color=INK)
    for i, r in enumerate(data["concurrent"][:RANK_ROWS]):
        p.row(str(r["rank"]), r["name"], f"{r['players']:,} 人", ref=("concurrent", i), appid=r["appid"])
    pages.append(p)

    p = Page(fonts, "Steam 週間プレイ人数ランキング", 2, g, icons)
    p.section("今週の順位", "先週からの変動")
    for i, r in enumerate(data["weekly"][:RANK_ROWS]):
        lw = r["last_week"]
        if not lw:
            mark, color = "初登場", ACCENT
        elif lw > r["rank"]:
            mark, color = f"▲{lw - r['rank']}", UP
        elif lw < r["rank"]:
            mark, color = f"▼{r['rank'] - lw}", DOWN
        else:
            mark, color = "同", INK
        p.row(str(r["rank"]), r["name"], mark, right_color=color, ref=("weekly", i), appid=r["appid"])
    pages.append(p)

    p = Page(fonts, "Steam 売れ筋とセール", 3, g, icons)
    p.section(f"売れ筋 上位 {STORE_ROWS}")
    for i, s in enumerate(data["top_sellers"][:STORE_ROWS], 1):
        p.row(str(i), s["name"], yen(s["price"]), h=STORE_H, ref=("top_sellers", i - 1), appid=None if s.get("sub") else s["appid"])
    p.y += STORE_GAP
    p.section("セール中")
    for i, s in enumerate(data["specials"][:STORE_ROWS], 1):
        p.row(str(i), s["name"], yen(s["price"]), mark=f"-{s['discount']}%", mark_color=UP, h=STORE_H,
              ref=("specials", i - 1), appid=None if s.get("sub") else s["appid"])
    pages.append(p)

    p = Page(fonts, "Steam 新作とウィッシュリスト", 4, g, icons)
    p.section("新作")
    for i, s in enumerate(data["new_releases"][:STORE_ROWS], 1):
        p.row(str(i), s["name"], yen(s["price"]), h=STORE_H, ref=("new_releases", i - 1), appid=None if s.get("sub") else s["appid"])
    p.y += STORE_GAP
    p.section("ウィッシュリスト上位")
    for i, s in enumerate(data["wishlist"][:STORE_ROWS], 1):
        p.row(str(i), s["name"], "", h=STORE_H, ref=("wishlist", i - 1), appid=None if s.get("sub") else s["appid"])
    pages.append(p)

    board = Image.new("RGB", (PAGE * 2, PAGE * 2))
    data["rows"] = []  # ページごとの行の位置 (ページ内の画素、上が 0)
    for k, pg in enumerate(pages):
        board.paste(pg.img, (k % 2 * PAGE, k // 2 * PAGE))
        data["rows"].append(pg.rows)
    return board


def main():
    fonts = next(f for f in FONTS if os.path.exists(f[0]))
    data = collect()
    hist = update_history(data)
    OUT.mkdir(exist_ok=True)
    icons = load_icons(data.pop("_capsules"))
    render(data, fonts, icons).save(OUT / "board.png", optimize=True)
    (OUT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "history.json").write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    print(f"{data['generated']} online {data['online']:,} / history {len(hist)} days / font {fonts[0]}")


if __name__ == "__main__":
    main()
