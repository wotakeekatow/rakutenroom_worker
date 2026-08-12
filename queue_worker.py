from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional

import requests

ITEM_SEARCH_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
RANKING_ENDPOINT = "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601"

# Base demand + current ROOM search/feature themes (summer 2026).
# Trend terms are only used to discover related products; irrelevant tags are never added.
MORNING_KEYWORDS = [
    "水筒", "キッチン 収納", "タオル", "掃除", "文房具", "日傘",
    "ヤケーヌ", "スニーカーサンダル",
]
AFTERNOON_KEYWORDS = [
    "収納", "キッチン", "インテリア", "日用品", "バッグ", "食品", "家電",
    "文房具", "タオル", "水筒", "日傘", "ハンディファン", "サーキュレーター",
    "レンジ調理", "ヤケーヌ", "スニーカーサンダル", "シャーリングブラウス",
    "スンヌンタイ", "ファミクロ",
]

RISK_WORDS = [
    "医薬品", "サプリ", "健康食品", "ダイエット", "育毛", "aga", "精力",
    "コンタクト", "カラコン", "cbd", "電子タバコ", "vape", "ビール", "ワイン",
    "焼酎", "ウイスキー", "日本酒",
]

DYNAMIC_PROMO_WORDS = [
    "クーポン", "ポイント", "off", "sale", "セール", "半額", "今だけ", "本日",
    "期間限定", "ランキング", "楽天1位", "送料無料",
]

FEATURE_WORDS = [
    "コードレス", "軽量", "防水", "USB充電", "スリム", "完成品", "洗える",
    "折りたたみ", "晴雨兼用", "完全遮光", "マグネット", "伸縮", "日本製",
    "大容量", "キャップレス", "3倍長持ち", "2WAY", "時短", "省スペース",
    "食洗機対応", "レンジ対応", "冷凍", "抗菌",
]


def find_key(mapping: Dict[str, Any], wanted: str) -> Optional[str]:
    wanted_lower = wanted.lower()
    for key in mapping.keys():
        if str(key).lower() == wanted_lower:
            return str(key)
    return None


def getv(mapping: Dict[str, Any], wanted: str, default: Any = None) -> Any:
    key = find_key(mapping, wanted)
    return mapping.get(key, default) if key is not None else default


def extract_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = getv(data, "items", [])
    if not isinstance(raw_items, list):
        return []
    out: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        wrapped = getv(raw, "item")
        out.append(wrapped if isinstance(wrapped, dict) else raw)
    return out


def common_params(app_id: str, affiliate_id: str) -> Dict[str, Any]:
    params: Dict[str, Any] = {"applicationId": app_id, "format": "json", "formatVersion": 2}
    if affiliate_id:
        params["affiliateId"] = affiliate_id
    return params


def api_get(url: str, params: Dict[str, Any], access_key: str) -> Dict[str, Any]:
    r = requests.get(url, params=params, headers={"accessKey": access_key}, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Rakuten API HTTP {r.status_code}")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("Rakuten API returned unexpected data")
    api_error = getv(data, "error")
    if api_error:
        raise RuntimeError(str(getv(data, "error_description", api_error)))
    return data


def ranking_items(app_id: str, access_key: str, affiliate_id: str) -> List[Dict[str, Any]]:
    p = common_params(app_id, affiliate_id)
    p.update({"period": "realtime", "page": 1, "carrier": 0})
    items = extract_items(api_get(RANKING_ENDPOINT, p, access_key))
    for item in items:
        item["_source"] = "楽天総合ランキング"
    return items[:30]


def search_items(app_id: str, access_key: str, affiliate_id: str, keyword: str) -> List[Dict[str, Any]]:
    p = common_params(app_id, affiliate_id)
    p.update({
        "keyword": keyword,
        "hits": 30,
        "sort": "standard",
        "availability": 1,
        "imageFlag": 1,
        "hasReviewFlag": 1,
        "carrier": 2,
    })
    items = extract_items(api_get(ITEM_SEARCH_ENDPOINT, p, access_key))
    for item in items:
        item["_source"] = keyword
    return items


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def estimated_commission(price: float, affiliate_rate: float) -> int:
    if price <= 0 or affiliate_rate <= 0:
        return 0
    raw = price * affiliate_rate / 100.0
    if affiliate_rate <= 4.0:
        raw = min(raw, 1000.0)
    return int(round(raw))


def score_item(item: Dict[str, Any]) -> float:
    rank = max(0.0, num(getv(item, "rank"), 0))
    reviews = max(0.0, num(getv(item, "reviewCount"), 0))
    avg = min(5.0, max(0.0, num(getv(item, "reviewAverage"), 0)))
    affiliate = min(20.0, max(0.0, num(getv(item, "affiliateRate"), 0)))
    price = max(0.0, num(getv(item, "itemPrice"), 0))
    points = min(10.0, max(0.0, num(getv(item, "pointRate"), 0)))
    shipping = int(num(getv(item, "postageFlag"), 1))

    rank_score = max(0.0, 1.0 - ((rank - 1.0) / 29.0)) if rank > 0 else 0.0
    review_score = min(1.0, math.log10(reviews + 1.0) / 5.0)
    rating_score = avg / 5.0
    affiliate_score = affiliate / 20.0
    commission_score = min(1.0, estimated_commission(price, affiliate) / 1000.0)
    shipping_score = 1.0 if shipping == 0 else 0.0
    point_score = points / 10.0
    popularity_score = review_score if rank <= 0 else max(rank_score, review_score * 0.8)

    score = (
        popularity_score * 25
        + review_score * 20
        + rating_score * 15
        + affiliate_score * 15
        + commission_score * 15
        + shipping_score * 5
        + point_score * 5
    )
    return round(min(100.0, score), 1)


def risky(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in RISK_WORDS)


def normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    price = int(num(getv(item, "itemPrice"), 0))
    affiliate_rate = round(num(getv(item, "affiliateRate"), 0), 1)
    name = str(getv(item, "itemName", "") or "").strip()
    return {
        "score": score_item(item),
        "rank": int(num(getv(item, "rank"), 0)) or None,
        "itemName": name,
        "itemPrice": price,
        "reviewCount": int(num(getv(item, "reviewCount"), 0)),
        "reviewAverage": round(num(getv(item, "reviewAverage"), 0), 2),
        "affiliateRate": affiliate_rate,
        "estimatedCommission": estimated_commission(price, affiliate_rate),
        "pointRate": int(num(getv(item, "pointRate"), 0)),
        "postageFlag": int(num(getv(item, "postageFlag"), 1)),
        "shopName": str(getv(item, "shopName", "") or "").strip(),
        "itemCode": str(getv(item, "itemCode", "") or "").strip(),
        "url": getv(item, "affiliateUrl") or getv(item, "itemUrl") or "",
        "source": str(item.get("_source", "")),
    }


def short_name(name: str, max_len: int = 42) -> str:
    return name if len(name) <= max_len else name[: max_len - 1] + "…"


def clean_title_for_draft(name: str) -> str:
    cleaned = re.sub(r"【[^】]{1,80}】", " ", name)
    cleaned = re.sub(r"［[^］]{1,80}］", " ", cleaned)
    cleaned = re.sub(r"\[[^\]]{1,80}\]", " ", cleaned)
    cleaned = re.sub(r"＼[^／]{1,80}／", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/・")
    return cleaned or name


def _variant(item: Dict[str, Any], count: int) -> int:
    seed = str(item.get("itemCode") or item.get("itemName") or "")
    return sum(ord(ch) for ch in seed) % max(1, count)


def hook_for_item(item: Dict[str, Any]) -> str:
    name = str(item.get("itemName") or "")
    source = str(item.get("source") or "")
    text = f"{name} {source}".lower()

    groups = [
        (["ヤケーヌ", "日傘", "完全遮光", "晴雨兼用"], [
            "夏の一軍候補、こういうのを探してた☀️",
            "日差し対策は、毎日使いやすいものを選びたい👀",
            "暑い日の外出を少しラクにしたい日に☀️",
        ]),
        (["スニーカーサンダル", "サンダル"], [
            "歩きやすさも見た目も妥協したくない日に👟",
            "夏の足元、ラクさ重視ならこの系統が気になる👀",
            "サッと履ける一軍サンダル候補に👟",
        ]),
        (["ハンディファン", "サーキュレーター"], [
            "暑さ対策、今年も早めに揃えておきたい🌬️",
            "夏のプチストレスを減らしてくれそう🌬️",
            "暑い日の相棒候補をチェック👀",
        ]),
        (["水筒", "ボトル"], [
            "毎日持つものこそ、使いやすさで選びたい🥤",
            "通勤・お出かけの一軍ボトル候補に🥤",
            "水分補給まわり、そろそろ見直したい人へ👀",
        ]),
        (["収納", "チェスト", "ラック", "ファミクロ"], [
            "「ここ、もう少し整えたい」にハマりそう👀",
            "収納は増やすより、使いやすく整えたい🙌",
            "生活感をすっきりさせたい場所に👀",
        ]),
        (["キッチン", "レンジ", "調味料", "食器"], [
            "キッチンの小さな面倒を減らしたい日に🍳",
            "毎日の台所仕事、少しラクにできそう👀",
            "出番の多いキッチン道具こそ使いやすさ重視🍳",
        ]),
        (["掃除", "クリーナー", "ワイパー"], [
            "掃除のハードルを少し下げたい日に🧹",
            "気づいた時にサッと使える掃除道具が好き👀",
            "「あとで掃除しよう」を減らせそう🧹",
        ]),
        (["タオル"], [
            "毎日使うものこそ、ちょうどいいを選びたい🫧",
            "タオルのサイズ感、地味に暮らしやすさに効く👀",
            "洗い替えまで含めて使いやすそう🫧",
        ]),
        (["食品", "米", "餃子", "牛めし", "おせんべい", "チーズ"], [
            "忙しい日のストック候補に🍚",
            "食卓にあると助かりそうなものをチェック👀",
            "お取り寄せでラクしたい日に🍚",
        ]),
        (["文房具", "印鑑", "スタンプ"], [
            "地味だけど、毎日ラクになる系✍️",
            "仕事や家の書類まわりを少し快適に✍️",
            "こういう小さな便利、けっこう好き👀",
        ]),
        (["バッグ"], [
            "荷物が多い日の一軍バッグ候補に👜",
            "普段使いしやすいバッグを探している人へ👀",
            "毎日持つなら、使いやすさで選びたい👜",
        ]),
        (["家電", "冷凍庫"], [
            "家事の手間を減らせそうな家電、気になる👀",
            "暮らしのプチストレス対策に⚡",
            "毎日使う家電は、ラクさ重視で選びたい👀",
        ]),
        (["インテリア", "スンヌンタイ"], [
            "置くだけで暮らしの景色が変わりそう🪴",
            "部屋の雰囲気を少し変えたい時に👀",
            "インテリアの一軍候補をチェック🪴",
        ]),
        (["シャーリングブラウス"], [
            "今っぽさを一枚で足したい日に👚",
            "夏コーデの主役候補に👀",
            "一枚で雰囲気が出そうなブラウス👚",
        ]),
    ]
    for words, hooks in groups:
        if any(word.lower() in text for word in words):
            return hooks[_variant(item, len(hooks))]
    if "ランキング" in source:
        hooks = [
            "売れ筋の中で目に止まったアイテム👀",
            "ROOMの売れ筋から、気になるものをチェック👀",
            "みんなが見ている中で気になった一品👀",
        ]
        return hooks[_variant(item, len(hooks))]
    hooks = [
        "暮らしに取り入れやすそうで気になる👀",
        "これ、使いどころがありそう👀",
        "ちょっと気になってチェック中👀",
    ]
    return hooks[_variant(item, len(hooks))]


def feature_tokens(name: str) -> List[str]:
    found: List[str] = []
    lowered = name.lower()
    for word in FEATURE_WORDS:
        if word.lower() in lowered and word not in found:
            found.append(word)
        if len(found) >= 2:
            break
    return found


def tags_for_item(item: Dict[str, Any]) -> List[str]:
    name = str(item.get("itemName") or "")
    source = str(item.get("source") or "")
    text = f"{name} {source}"
    tags = ["#楽天ROOM"]

    if "ランキング" in source:
        tags.append("#売れ筋")
    if any(x in text for x in ["キッチン", "レンジ", "掃除", "ワイパー", "クリーナー"]):
        tags.append("#時短家事")
    if any(x in text for x in ["収納", "チェスト", "ラック", "インテリア", "ファミクロ"]):
        tags.append("#インテリア")
    if any(x in text for x in ["日傘", "ヤケーヌ", "ハンディファン", "サーキュレーター", "水筒"]):
        tags.append("#夏アイテム")
    if "ヤケーヌ" in text:
        tags.append("#ヤケーヌ")
    if "スニーカーサンダル" in text:
        tags.append("#スニーカーサンダル")
    if "シャーリングブラウス" in text:
        tags.append("#シャーリングブラウス")
    if "スンヌンタイ" in text:
        tags.append("#スンヌンタイ")
    if "食品" in source:
        tags.append("#お取り寄せ")
    if "文房具" in source:
        tags.append("#文房具")

    # Keep hashtags concise; never attach trend tags unrelated to the product.
    return list(dict.fromkeys(tags))[:3]


def room_draft(item: Dict[str, Any]) -> str:
    name = str(item.get("itemName") or "").strip()
    title = short_name(clean_title_for_draft(name), 36)
    features = feature_tokens(name)
    reviews = int(item.get("reviewCount") or 0)
    avg = float(item.get("reviewAverage") or 0)

    lines = [hook_for_item(item), title]

    if features:
        lines.append("・".join(features) + "タイプ。商品ページでサイズや条件を確認して選びたい。")
    elif reviews >= 1000 and avg >= 4.0:
        lines.append(f"レビュー{reviews:,}件・評価★{avg:.1f}。人気の理由を商品ページでチェック。")
    elif reviews >= 100 and avg >= 4.0:
        lines.append(f"レビュー{reviews:,}件・評価★{avg:.1f}。候補に入れて比較したい。")
    else:
        lines.append("商品ページで仕様やサイズ感を確認して、合うか見ておきたい。")

    lines.append(" ".join(tags_for_item(item)))
    return "\n".join(lines)


def automated_review(item: Dict[str, Any]) -> Dict[str, Any]:
    notes: List[str] = []
    lowered = item["itemName"].lower()
    if any(word in lowered for word in DYNAMIC_PROMO_WORDS):
        notes.append("商品名にキャンペーン・期間・ランキング表現あり。投稿前に最新条件を確認。")
    if item["itemPrice"] >= 30000:
        notes.append("高額商品。価格・送料・配送条件を投稿前に再確認。")
    if item["reviewCount"] < 20:
        notes.append("レビュー件数が少なめ。表現を控えめにする。")
    if item["reviewAverage"] and item["reviewAverage"] < 4.0:
        notes.append("評価4.0未満。おすすめ断定を避ける。")

    blockers: List[str] = []
    if not item["url"]:
        blockers.append("商品URLが取得できていない。")
    if not item["itemName"]:
        blockers.append("商品名が取得できていない。")
    if item["itemPrice"] <= 0:
        blockers.append("価格が取得できていない。")

    if blockers:
        status, notes = "hold", blockers + notes
    elif notes:
        status = "passed_with_notes"
    else:
        status = "passed"

    return {
        "status": status,
        "notes": notes,
        "checked_fields": ["商品名", "価格", "URL", "レビュー", "変動キャンペーン表現", "高リスク語"],
    }


def select_candidates(items: Iterable[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen = set()

    for item in items:
        row = normalize(item)
        key = row["itemCode"] or row["url"] or row["itemName"]
        if not key or key in seen or not row["itemName"] or risky(row["itemName"]):
            continue
        seen.add(key)
        normalized.append(row)

    normalized.sort(key=lambda x: (x["score"], x["reviewCount"]), reverse=True)

    selected: List[Dict[str, Any]] = []
    source_counts: Dict[str, int] = {}
    for row in normalized:
        source = row["source"] or "other"
        if source_counts.get(source, 0) >= 4:
            continue
        selected.append(row)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= count:
            break

    if len(selected) < count:
        selected_keys = {x["itemCode"] or x["url"] or x["itemName"] for x in selected}
        for row in normalized:
            key = row["itemCode"] or row["url"] or row["itemName"]
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            if len(selected) >= count:
                break

    final: List[Dict[str, Any]] = []
    for row in selected:
        row["draft"] = room_draft(row)
        row["review"] = automated_review(row)
        row["status"] = "ready_for_review" if row["review"]["status"] != "hold" else "hold"
        if row["status"] == "ready_for_review":
            final.append(row)

    if len(final) < count:
        final_keys = {x["itemCode"] or x["url"] or x["itemName"] for x in final}
        for row in normalized:
            key = row["itemCode"] or row["url"] or row["itemName"]
            if key in final_keys:
                continue
            row["draft"] = room_draft(row)
            row["review"] = automated_review(row)
            row["status"] = "ready_for_review" if row["review"]["status"] != "hold" else "hold"
            if row["status"] == "ready_for_review":
                final.append(row)
                final_keys.add(key)
            if len(final) >= count:
                break

    return final[:count]
