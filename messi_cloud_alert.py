#!/usr/bin/env python3
"""
Kiểm tra 1 lần tỉ lệ Messi thắng World Cup Golden Boot trên Polymarket,
gửi email qua Resend API nếu tỉ lệ "Yes" >= ngưỡng (mặc định 63%).

Thiết kế để chạy trên GitHub Actions cron (không cần treo máy local).
Dùng state.json để CHỐNG SPAM: chỉ email khi tỉ lệ VƯỢT ngưỡng lần đầu
(edge trigger), không email lặp lại khi vẫn đang trên ngưỡng.

Biến môi trường (đặt trong GitHub Secrets):
    RESEND_API_KEY   - API key của Resend (bắt buộc để gửi thật)
    ALERT_EMAIL_TO   - email nhận, cách nhau bởi dấu phẩy nếu nhiều
    ALERT_EMAIL_FROM - (tùy chọn) mặc định "Polymarket Alert <onboarding@resend.dev>"
    ALERT_THRESHOLD  - (tùy chọn) ngưỡng 0-1, mặc định 0.63

Chạy thử local (không có RESEND_API_KEY -> chế độ DRY-RUN, chỉ in ra):
    ALERT_EMAIL_TO=you@example.com python3 messi_cloud_alert.py
"""

import json
import os
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# dùng lại logic fetch & quyết định cảnh báo đã kiểm chứng trong file monitor
from messi_goldenboot_monitor import (
    EVENT_SLUG,
    decide_alert,
    find_messi_market,
    get_probability,
    level_of,
)

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
RESEND_ENDPOINT = "https://api.resend.com/emails"
EVENT_URL = f"https://polymarket.com/event/{EVENT_SLUG}"


# --------------------------------------------------------------------------- #
# State (chống spam email)
# --------------------------------------------------------------------------- #
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"above": False, "peak_level": 0, "below_low": False, "last_alert_iso": None}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# --------------------------------------------------------------------------- #
# Email qua Resend
# --------------------------------------------------------------------------- #
def _alert_texts(kind, prob, threshold, low_threshold=None):
    """Trả (subject, dòng mô tả HTML) theo loại cảnh báo."""
    p = f"{prob * 100:.1f}%"
    th = f"{threshold * 100:.0f}%"
    lo = f"{(low_threshold or 0) * 100:.0f}%"
    lvl = level_of(prob)
    if kind == "cross":
        return (f"🚨 Messi VƯỢT {th} — hiện {p}",
                f"Tỉ lệ <b>Messi</b> thắng Golden Boot vừa <b>vượt ngưỡng {th}</b>, hiện <b>{p}</b>.")
    if kind == "rise":
        return (f"📈 Messi lập mốc {lvl}% — hiện {p}",
                f"Tỉ lệ <b>Messi</b> tiếp tục <b>tăng</b>, đạt mốc mới <b>{lvl}%</b> (hiện {p}).")
    if kind == "drop":
        return (f"🔻 Messi rớt khỏi mốc {th} — hiện {p}",
                f"Tỉ lệ <b>Messi</b> vừa <b>rớt khỏi mốc {th}</b>, hiện <b>{p}</b>.")
    if kind == "fall":
        return (f"⚠️ Messi TỤT sâu dưới {lo} — hiện {p}",
                f"Tỉ lệ <b>Messi</b> vừa <b>tụt sâu xuống dưới {lo}</b>, hiện <b>{p}</b>.")
    return (f"Messi {p}", f"Tỉ lệ Messi hiện <b>{p}</b>.")


def send_email_resend(kind, prob, threshold, low_threshold=None):
    api_key = os.environ.get("RESEND_API_KEY")
    to_addr = os.environ.get("ALERT_EMAIL_TO", "")
    # dùng `or` để secret bị bỏ trống (biến = "") vẫn rơi về mặc định
    from_addr = os.environ.get("ALERT_EMAIL_FROM") or "Polymarket Alert <onboarding@resend.dev>"

    subject, lead = _alert_texts(kind, prob, threshold, low_threshold)
    html = f"""
        <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;color:#111">
          <h2 style="margin:0 0 8px">{subject.split(' — ')[0]}</h2>
          <p>{lead}</p>
          <p><a href="{EVENT_URL}" style="color:#2563eb">Mở thị trường trên Polymarket →</a></p>
          <p style="color:#888;font-size:12px">
             Thời điểm: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>
        </div>
    """.strip()

    recipients = [a.strip() for a in to_addr.split(",") if a.strip()]

    if not api_key:
        print("[DRY-RUN] Chưa có RESEND_API_KEY — email KHÔNG gửi, nội dung dự kiến:")
        print(f"  From:    {from_addr}")
        print(f"  To:      {recipients}")
        print(f"  Subject: {subject}")
        return

    if not recipients:
        raise RuntimeError("Thiếu ALERT_EMAIL_TO")

    payload = json.dumps({
        "from": from_addr,
        "to": recipients,
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = Request(
        RESEND_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # BẮT BUỘC: thiếu UA -> Cloudflare trước Resend chặn với lỗi 403 code 1010
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        },
    )
    try:
        with urlopen(req, timeout=20) as r:
            resp = json.loads(r.read().decode("utf-8"))
        print(f"Đã gửi email tới {recipients} (Resend id: {resp.get('id')})")
    except HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"Resend trả lỗi {e.code}: {body}") from e


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    threshold = float(os.environ.get("ALERT_THRESHOLD", "0.60"))
    # ngưỡng dưới: để trống/xóa env -> None -> tắt cảnh báo 'fall'
    low_raw = os.environ.get("ALERT_LOW_THRESHOLD", "0.57")
    low_threshold = float(low_raw) if low_raw.strip() else None

    market = find_messi_market()
    prob, source = get_probability(market)
    now = datetime.now(timezone.utc)
    low_txt = f"{low_threshold * 100:.0f}%" if low_threshold is not None else "tắt"
    print(f"[{now.isoformat(timespec='seconds')}] Messi = {prob * 100:.2f}% "
          f"(nguồn: {source}) | ngưỡng trên {threshold * 100:.0f}% | ngưỡng dưới {low_txt}")

    # TEST_MODE: gửi 1 email thử để kiểm tra đường email, KHÔNG đọc/ghi state
    if os.environ.get("TEST_MODE"):
        print("** TEST MODE ** gửi email thử nghiệm (bỏ qua state, không lưu).")
        send_email_resend("cross", prob, threshold, low_threshold)
        return

    state = load_state()
    kinds, new_state, changed = decide_alert(prob, threshold, state, low_threshold)

    if kinds:
        reason = {"cross": "vừa vượt ngưỡng trên", "rise": "lập mốc % mới",
                  "drop": "rớt khỏi ngưỡng trên", "fall": "tụt sâu dưới ngưỡng dưới"}
        for kind in kinds:
            send_email_resend(kind, prob, threshold, low_threshold)
            print(f"-> Gửi cảnh báo [{kind}] ({reason.get(kind, '')}).")
        new_state["last_alert_iso"] = now.isoformat()
    else:
        new_state["last_alert_iso"] = state.get("last_alert_iso")
        print("-> Không có thay đổi đáng báo, bỏ qua.")

    # ghi state (lần đầu tạo file, hoặc khi có thay đổi)
    if changed or not os.path.exists(STATE_FILE):
        save_state(new_state)

    # cho workflow biết có cần commit lại state không
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"state_changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        sys.exit(1)
