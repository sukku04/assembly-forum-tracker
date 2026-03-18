"""
국회토론회 일정 수집기
- 과거 1년 + 미래 60일 전체 데이터 수집
- 페이지네이션으로 전체 데이터 가져오기
- 결과를 data/forums.json 으로 저장 → 웹페이지가 직접 읽음
- 신규 토론회 감지 시 Teams + 이메일 알림
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

API_KEY           = os.environ.get("ASSEMBLY_API_KEY", "")
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
DATA_FILE         = Path("data/seen_forums.json")
FORUMS_JSON       = Path("data/forums.json")
BASE_URL          = "https://open.assembly.go.kr/portal/openapi/nfcoioopazrwmjrgs"

TODAY     = datetime.today()
DATE_FROM = (TODAY - timedelta(days=365)).strftime("%Y-%m-%d")  # 과거 1년
DATE_TO   = (TODAY + timedelta(days=60)).strftime("%Y-%m-%d")   # 미래 60일


# ────────────────────────────────────────────────
# API 호출 (전체 페이지 수집)
# ────────────────────────────────────────────────
def fetch_all_forums() -> list[dict]:
    """전체 데이터를 페이지네이션으로 모두 가져옴"""
    all_rows = []
    page = 1
    page_size = 100

    while True:
        params = {
            "KEY":    API_KEY,
            "Type":   "json",
            "pIndex": page,
            "pSize":  page_size,
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            root = data.get("nfcoioopazrwmjrgs", [])

            # 전체 건수 확인 (첫 페이지에서만)
            if page == 1:
                for block in root:
                    if "head" in block:
                        total = int(block["head"][0].get("list_total_count", 0))
                        print(f"전체 데이터: {total}건")
                        break

            # row 추출
            rows = []
            for block in root:
                if "row" in block:
                    r = block["row"]
                    rows = r if isinstance(r, list) else [r]
                    break

            if not rows:
                print(f"페이지 {page}: 데이터 없음 — 수집 완료")
                break

            all_rows.extend(rows)
            print(f"페이지 {page}: {len(rows)}건 수집 (누적 {len(all_rows)}건)")

            # 마지막 페이지 확인
            if len(rows) < page_size:
                break

            # 날짜 범위 벗어나면 중단 (API가 최신순 정렬인 경우)
            oldest = min((r.get("SDATE","") for r in rows), default="")
            if oldest and oldest < DATE_FROM:
                print(f"날짜 범위({DATE_FROM}) 이전 데이터 도달 — 수집 중단")
                break

            page += 1

            # 안전장치: 최대 30페이지 (3000건)
            if page > 30:
                print("[WARN] 최대 페이지 도달")
                break

        except Exception as e:
            print(f"[ERROR] 페이지 {page} 호출 실패: {e}")
            break

    return all_rows


# ────────────────────────────────────────────────
# 날짜 필터
# ────────────────────────────────────────────────
def filter_by_date(forums: list[dict]) -> list[dict]:
    return [f for f in forums if DATE_FROM <= f.get("SDATE", "") <= DATE_TO]


# ────────────────────────────────────────────────
# 이미 본 토론회 관리
# ────────────────────────────────────────────────
def load_seen() -> set[str]:
    if DATA_FILE.exists():
        return set(json.loads(DATA_FILE.read_text(encoding="utf-8")))
    return set()

def save_seen(seen: set[str]):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def find_new_forums(forums, seen):
    new = []
    for f in forums:
        if f.get("SDATE", "") < TODAY.strftime("%Y-%m-%d"):
            continue  # 과거 데이터는 신규 알림 제외
        fid = f.get("LINK") or f.get("TITLE", "") + f.get("SDATE", "")
        if fid and fid not in seen:
            new.append(f)
            seen.add(fid)
    return new, seen


# ────────────────────────────────────────────────
# 웹페이지용 JSON 저장
# ────────────────────────────────────────────────
def save_forums_json(forums: list[dict]):
    FORUMS_JSON.parent.mkdir(parents=True, exist_ok=True)

    # 날짜순 정렬
    sorted_forums = sorted(forums, key=lambda x: x.get("SDATE", ""))

    output = {
        "updated_at": TODAY.strftime("%Y-%m-%d %H:%M"),
        "date_from":  DATE_FROM,
        "date_to":    DATE_TO,
        "count":      len(sorted_forums),
        "forums":     sorted_forums
    }
    FORUMS_JSON.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[OK] data/forums.json 저장 완료 ({len(sorted_forums)}건)")


# ────────────────────────────────────────────────
# Teams 알림
# ────────────────────────────────────────────────
def send_teams_alert(new_forums: list[dict]):
    if not TEAMS_WEBHOOK_URL:
        print("[SKIP] TEAMS_WEBHOOK_URL 미설정")
        return

    facts = []
    for f in new_forums[:10]:  # 최대 10건만
        facts.append({
            "type": "TextBlock",
            "text": f"🆕 **[{f.get('TITLE','')}]({f.get('LINK','')})**  \n"
                    f"📅 {f.get('SDATE','')} {f.get('STIME','')}  \n"
                    f"📍 {f.get('LOCATION','')}",
            "wrap": True, "spacing": "Medium"
        })

    card = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": [
                    {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                     "text": f"🚨 신규 토론회 {len(new_forums)}건 등록!", "wrap": True},
                    {"type": "separator"},
                    *facts
                ]
            }
        }]
    }
    try:
        requests.post(TEAMS_WEBHOOK_URL, json=card, timeout=10).raise_for_status()
        print("[OK] Teams 알림 전송 완료")
    except Exception as e:
        print(f"[ERROR] Teams 전송 실패: {e}")


# ────────────────────────────────────────────────
# 이메일 알림
# ────────────────────────────────────────────────
def send_email_alert(new_forums: list[dict]):
    import smtplib
    from email.mime.text import MIMEText
    sender   = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_PASS")
    receiver = os.environ.get("ALERT_EMAIL", sender)
    if not (sender and password):
        print("[SKIP] 이메일 환경변수 미설정")
        return
    body = f"신규 토론회 {len(new_forums)}건\n\n" + "\n\n".join(
        f"{f.get('TITLE','')}\n{f.get('SDATE','')} {f.get('STIME','')}\n"
        f"{f.get('LOCATION','')}\n{f.get('LINK','')}"
        for f in new_forums
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[국회토론회] 새 일정 {len(new_forums)}건"
    msg["From"] = sender
    msg["To"]   = receiver
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print("[OK] 이메일 발송 완료")
    except Exception as e:
        print(f"[ERROR] 이메일 발송 실패: {e}")


# ────────────────────────────────────────────────
# GitHub Actions Step Summary
# ────────────────────────────────────────────────
def write_github_summary(new_forums, all_forums):
    sf = os.environ.get("GITHUB_STEP_SUMMARY")
    if not sf:
        return
    upcoming = [f for f in all_forums if f.get("SDATE","") >= TODAY.strftime("%Y-%m-%d")]
    with open(sf, "a", encoding="utf-8") as f:
        f.write("## 🏛️ 국회토론회 일정 업데이트\n\n")
        f.write(f"- 수집 기간: `{DATE_FROM}` ~ `{DATE_TO}`\n")
        f.write(f"- 전체: **{len(all_forums)}건** / 예정: **{len(upcoming)}건** / 신규: **{len(new_forums)}건**\n\n")
        if upcoming[:20]:
            f.write("### 📅 향후 일정 (최대 20건)\n\n")
            f.write("| 날짜 | 시간 | 제목 | 장소 |\n|------|------|------|------|\n")
            for fo in upcoming[:20]:
                f.write(f"| {fo.get('SDATE','')} | {fo.get('STIME','')} "
                        f"| [{fo.get('TITLE','')}]({fo.get('LINK','')}) "
                        f"| {fo.get('LOCATION','')} |\n")


# ────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────
def main():
    print(f"[{TODAY.strftime('%Y-%m-%d %H:%M')}] 조회 시작")
    print(f"수집 기간: {DATE_FROM} ~ {DATE_TO}")

    # 전체 페이지 수집
    all_raw = fetch_all_forums()
    print(f"\nAPI 전체 응답: {len(all_raw)}건")

    if not all_raw:
        print("[WARN] 결과 없음 — API 키를 확인하세요.")
        return

    # 날짜 필터
    filtered = filter_by_date(all_raw)
    print(f"날짜 필터 후: {len(filtered)}건 ({DATE_FROM} ~ {DATE_TO})")

    # 웹페이지용 JSON 저장
    save_forums_json(filtered)

    # 신규 감지 (미래 일정만)
    seen = load_seen()
    new_forums, seen = find_new_forums(filtered, seen)
    print(f"신규 (미래): {len(new_forums)}건")

    if new_forums:
        save_seen(seen)
        send_teams_alert(new_forums)
        send_email_alert(new_forums)

    write_github_summary(new_forums, filtered)
    print("완료.")


if __name__ == "__main__":
    main()
