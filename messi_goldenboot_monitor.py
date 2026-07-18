#!/usr/bin/env python3
"""
Theo dõi tỉ lệ Messi thắng World Cup Golden Boot trên Polymarket.
Alert khi tỉ lệ "Yes" (Messi là vua phá lưới) >= ngưỡng (mặc định 63%).

Chỉ dùng thư viện chuẩn của Python -> không cần cài gì thêm.

Cách dùng:
    python3 messi_goldenboot_monitor.py            # chạy liên tục, poll mỗi 5 phút
    python3 messi_goldenboot_monitor.py --once      # kiểm tra 1 lần rồi thoát (dùng cho cron)
    python3 messi_goldenboot_monitor.py --threshold 0.63 --interval 300
    SLACK_WEBHOOK_URL=https://hooks.slack.com/... python3 messi_goldenboot_monitor.py

Ngưỡng có thể chỉnh bằng --threshold (0-1) hoặc biến môi trường ALERT_THRESHOLD.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

EVENT_SLUG = "world-cup-golden-boot-winner"
PLAYER_KEYWORD = "messi"          # dùng để nhận diện market của Messi
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def http_get_json(url, timeout=20):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (messi-monitor)"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _maybe_json(value):
    """Gamma trả outcomes / outcomePrices dưới dạng chuỗi JSON -> parse ra list."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# --------------------------------------------------------------------------- #
# Lấy dữ liệu market
# --------------------------------------------------------------------------- #
def find_messi_market():
    """Tìm market của Messi trong event Golden Boot (theo slug, tự thích nghi nếu id đổi)."""
    events = http_get_json(f"{GAMMA_API}/events?slug={EVENT_SLUG}")
    if not events:
        raise RuntimeError(f"Không tìm thấy event '{EVENT_SLUG}'")
    for market in events[0].get("markets", []):
        label = ((market.get("groupItemTitle") or "") + " " +
                 (market.get("question") or "")).lower()
        if PLAYER_KEYWORD in label:
            return market
    raise RuntimeError("Không tìm thấy market của Messi trong event")


def clob_midpoint(token_id):
    """Midpoint (bid+ask)/2 realtime từ order book của CLOB. Trả None nếu lỗi."""
    try:
        data = http_get_json(f"{CLOB_API}/midpoint?token_id={token_id}")
        mid = data.get("mid")
        return float(mid) if mid is not None else None
    except (URLError, HTTPError, ValueError, KeyError):
        return None


def get_probability(market):
    """
    Ước lượng xác suất 'Yes' tốt nhất hiện có, ưu tiên realtime:
      1) midpoint từ CLOB order book (chuẩn nhất)
      2) (bestBid + bestAsk) / 2 từ Gamma
      3) lastTradePrice
      4) outcomePrices["Yes"]
    Trả (prob_float, nguồn_str).
    """
    outcomes = _maybe_json(market.get("outcomes")) or []
    prices = _maybe_json(market.get("outcomePrices")) or []
    yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0

    token_ids = _maybe_json(market.get("clobTokenIds")) or []
    if token_ids and yes_idx < len(token_ids):
        mid = clob_midpoint(token_ids[yes_idx])
        if mid is not None:
            return mid, "clob_midpoint"

    best_bid = market.get("bestBid")
    best_ask = market.get("bestAsk")
    if best_bid is not None and best_ask is not None:
        return (float(best_bid) + float(best_ask)) / 2, "gamma_bidask"

    if market.get("lastTradePrice") is not None:
        return float(market["lastTradePrice"]), "lastTradePrice"

    if yes_idx < len(prices):
        return float(prices[yes_idx]), "outcomePrices"

    raise RuntimeError("Không đọc được giá từ market")


# --------------------------------------------------------------------------- #
# Logic quyết định cảnh báo (dùng chung cho bản local & cloud)
# --------------------------------------------------------------------------- #
def level_of(prob):
    """Phần trăm nguyên, làm tròn xuống, có epsilon chống sai số float.
    0.6399 -> 63 ; 0.64 -> 64 ; 0.65 -> 65."""
    return int(prob * 100 + 1e-9)


def decide_alert(prob, threshold, state, low_threshold=None):
    """
    Quyết định cảnh báo:
      - 'cross' : vừa VƯỢT ngưỡng trên lần đầu
      - 'rise'  : đang trên ngưỡng trên và lập MỐC % nguyên mới cao hơn (mỗi +1%)
      - 'drop'  : vừa TỤT từ trên ngưỡng trên xuống dưới ngưỡng trên
      - 'fall'  : vừa TỤT xuống dưới NGƯỠNG DƯỚI (low_threshold), nếu có đặt

    state = {"above": bool, "peak_level": int, "below_low": bool}
    Trả (kinds_list, new_state, changed_bool). kinds_list có thể rỗng/1/nhiều phần tử
    (vd nhảy vọt xuyên cả 2 mốc trong 1 lần kiểm tra -> ['drop', 'fall']).
    """
    above0 = bool(state.get("above", False))
    peak0 = int(state.get("peak_level", 0))
    below0 = bool(state.get("below_low", False))

    above, peak, below = above0, peak0, below0
    kinds = []

    hit = prob >= threshold
    level = level_of(prob)

    # --- Ngưỡng trên ---
    if hit and not above:                         # vượt ngưỡng trên lần đầu
        kinds.append("cross"); above = True; peak = level
    elif hit and above and level > peak:          # lập mốc % mới cao hơn
        kinds.append("rise"); peak = level
    elif not hit and above:                       # rớt khỏi ngưỡng trên
        kinds.append("drop"); above = False; peak = 0

    # --- Ngưỡng dưới (edge trigger, reset khi lên lại >= low_threshold) ---
    if low_threshold is not None:
        if prob < low_threshold and not below:
            kinds.append("fall"); below = True
        elif prob >= low_threshold and below:
            below = False

    new_state = {"above": above, "peak_level": peak, "below_low": below}
    changed = (above, peak, below) != (above0, peak0, below0)
    return kinds, new_state, changed


# --------------------------------------------------------------------------- #
# Alerting
# --------------------------------------------------------------------------- #
def notify_macos(title, message):
    """Thông báo desktop trên macOS + âm thanh."""
    try:
        safe_msg = message.replace('"', "'")
        safe_title = title.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}" sound name "Glass"'],
            check=False,
        )
    except Exception:
        pass


def notify_slack(message):
    """Gửi Slack nếu có SLACK_WEBHOOK_URL."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return
    try:
        payload = json.dumps({"text": message}).encode("utf-8")
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=15)
    except Exception as e:
        print(f"  (không gửi được Slack: {e})", file=sys.stderr)


def alert_message(kind, prob, threshold, low_threshold=None):
    p = f"{prob * 100:.1f}%"
    th = f"{threshold * 100:.0f}%"
    lo = f"{(low_threshold or 0) * 100:.0f}%"
    if kind == "cross":
        return f"🚨 Messi VƯỢT {th} — hiện {p}"
    if kind == "rise":
        return f"📈 Messi lập mốc mới {level_of(prob)}% — hiện {p}"
    if kind == "drop":
        return f"🔻 Messi rớt khỏi mốc {th} — hiện {p}"
    if kind == "fall":
        return f"⚠️ Messi TỤT sâu xuống dưới {lo} — hiện {p}"
    return f"Messi {p}"


def fire_alert(kind, prob, threshold, low_threshold=None):
    body = alert_message(kind, prob, threshold, low_threshold)
    print(f"{body}  |  polymarket.com/event/{EVENT_SLUG}")
    notify_macos("Polymarket Alert", body)
    notify_slack(f"{body}  |  polymarket.com/event/{EVENT_SLUG}")


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def check_once(threshold):
    market = find_messi_market()
    prob, source = get_probability(market)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Messi Golden Boot: {prob * 100:.2f}%  (nguồn: {source})")
    return prob


def main():
    parser = argparse.ArgumentParser(description="Theo dõi tỉ lệ Messi thắng Golden Boot trên Polymarket")
    parser.add_argument("--threshold", type=float,
                        default=float(os.environ.get("ALERT_THRESHOLD", "0.60")),
                        help="Ngưỡng trên (0-1), mặc định 0.60 = 60%%")
    parser.add_argument("--low-threshold", type=float,
                        default=float(os.environ.get("ALERT_LOW_THRESHOLD", "0.57")),
                        help="Ngưỡng dưới (0-1) -> alert 'fall' khi tụt xuống dưới, mặc định 0.57")
    parser.add_argument("--interval", type=int, default=300,
                        help="Khoảng cách giữa các lần kiểm tra (giây), mặc định 300")
    parser.add_argument("--once", action="store_true",
                        help="Kiểm tra 1 lần rồi thoát (hợp với cron/launchd)")
    args = parser.parse_args()

    print(f"Theo dõi Messi @ Golden Boot | ngưỡng trên = {args.threshold * 100:.0f}% "
          f"| ngưỡng dưới = {args.low_threshold * 100:.0f}%")

    if args.once:
        try:
            prob = check_once(args.threshold)
            hit = prob >= args.threshold
            if hit:
                fire_alert("cross", prob, args.threshold)  # --once không giữ state -> báo nếu đang trên ngưỡng
            elif prob < args.low_threshold:
                fire_alert("fall", prob, args.threshold, args.low_threshold)
            sys.exit(10 if hit else 0)  # exit 10 = đang trên ngưỡng trên
        except Exception as e:
            print(f"Lỗi: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"Chạy liên tục, poll mỗi {args.interval}s. Nhấn Ctrl+C để dừng.\n")
    state = {"above": False, "peak_level": 0, "below_low": False}
    try:
        while True:
            try:
                prob = check_once(args.threshold)
                kinds, state, _ = decide_alert(prob, args.threshold, state, args.low_threshold)
                for kind in kinds:
                    fire_alert(kind, prob, args.threshold, args.low_threshold)
            except Exception as e:
                print(f"Lỗi (sẽ thử lại): {e}", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nĐã dừng.")


if __name__ == "__main__":
    main()
