from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional

import requests

ITEM_SEARCH_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
RANKING_ENDPOINT = "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601"

MORNING_KEYWORDS = ["水筒", "キッチン 収納", "タオル", "掃除", "文房具"]
AFTERNOON_KEYWORDS = ["収納", "キッチン", "インテリア", "日用品", "バッグ", "食品", "家電", "文房具", "タオル", "水筒"]

RISK_WORDS = [
    "医薬品", "サプリ", "健康食品", "ダイエット", "育毛", "aga", "精力",
    "コンタクト", "カラコン", "cbd", "電子タバコ", "vape", "ビール", "ワイン",
    "焼酎", "ウイスキー", "日本酒",
]

DYNAMIC_PROMO_WORDS = [
    "クーポン", "ポイント", "off", "sale", "セール", "半額", "今だけ", "本日",
    "期間限定", "ランキング", "楽天1位", "送料無料",
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
    params: Dict[str, Any] = {
        "applicationId": app_id,
        "format": "json",
        "formatVersion": 2,
    }
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
    p.update(
        {
            "keyword": keyword,
            "hits": 30,
            "sort": "standard",
            "availability": 1,
            "imageFlag": 1,
            "hasReviewFlag": 1,
            "carrier": 2,
        }
    )
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


def intro_for_source(source: str) -> str:
    if "食品" in source:
        return "食卓やストック候補として気になる"
    if any(x in source for x in ["キッチン", "収納", "インテリア"]):
        return "暮らしを整える候補として気になる"
    if any(x in source for x in ["タオル", "掃除", "日用品"]):
        return "毎日使うものとして気になる"
    if "文房具" in source:
        return "仕事や家まわりで使いやすそうな候補として気になる"
    if "水筒" in source:
        return "日常使いの候補として気になる"
    if "バッグ" in source:
        return "普段使いの候補として気になる"
    if "家電" in source:
        return "暮らしの道具として気になる"
    return "気になる楽天アイテム"


def room_draft(item: Dict[str, Any]) -> str:
    title = short_name(clean_title_for_draft(item["itemName"]), 38)
    facts: List[str] = []
    if item["reviewCount"]:
        facts.append(f"レビュー{item['reviewCount']:,}件")
    if item["reviewAverage"]:
        facts.append(f"評価★{item['reviewAverage']:.1f}")
    if item["itemPrice"]:
        facts.append(f"取得時 約{item['itemPrice']:,}円")
    lines = [f"{intro_for_source(item['source'])}👀 {title}"]
    if facts:
        lines.append(" / ".join(facts[:3]))
    lines.append("条件を比べながらチェック中。価格・在庫・キャンペーンは変わるので、投稿前に商品ページで最新情報を確認します。")
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
        status = "hold"
        notes = blockers + notes
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
        if source_counts.get(source, 0) >= 3:
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
