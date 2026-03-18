"""
국회토론회 일정 수집기
- 매일 자동 실행 (GitHub Actions)
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
FORUMS_JSON       = Path("data/forums.json")   # 웹페이지용 전체 데이터
BASE_URL          = "https://open.assembly.go.kr/portal/openapi/nfcoioopazrwmjrgs"

TODAY     = datetime.today()
DATE_FROM = (TODAY - timedelta(days=365)).strftime("%Y-%m-%d")
DATE_TO   = (TODAY + timedelta(days=60)).strftime("%Y-%m-%d")


def fetch_forums(page=1, page_size=100):
    params = {"KEY": API_KEY, "Type": "json", "pIndex": page, "pSize": page_size}
    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        root = data.get("nfcoioopazrwmjrgs", [])
        for block in root:
            if "row" in block:
                r = block["row"]
                return r if isinstance(r, list) else [r]
        return []
    except Exception as e:
        print(f"[ERROR] API 호출 실패: {e}")
        return []


def filter_upcoming(forums):
    return [f for f in forums if DATE_FROM <= f.get("SDATE", "") <= DATE_TO]


def load_seen():
    if DATA_FILE.exists():
        return set(json.loads(DATA_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def save_forums_json(upcoming):
    """웹페이지가 읽을 JSON 파일 저장"""
    FORUMS_JSON.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "updated_at": TODAY.strftime("%Y-%m-%d %H:%M"),
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "count": len(upcoming),
        "forums": sorted(upcoming, key=lambda x: x.get("SDATE", ""))
    }
    FORUMS_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] data/forums.json 저장 완료 ({len(upcoming)}건)")


def find_new_forums(forums, seen):
    new = []
    for f in forums:
        fid = f.get("LINK") or f.get("TITLE", "") + f.get("SDATE", "")
        if fid and fid not in seen:
            new.append(f)
            seen.add(fid)
    return new, seen


def send_teams_alert(new_forums):
    if not TEAMS_WEBHOOK_URL:
        print("[SKIP] TEAMS_WEBHOOK_URL 미설정")
        return

    facts = []
    for f in new_forums:
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
        print(f"[OK] Teams 알림 전송 완료")
    except Exception as e:
        print(f"[ERROR] Teams 전송 실패: {e}")


def send_email_alert(new_forums):
    import smtplib
    from email.mime.text import MIMEText
    sender   = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_PASS")
    receiver = os.environ.get("ALERT_EMAIL", sender)
    if not (sender and password):
        print("[SKIP] 이메일 환경변수 미설정")
        return
    body = f"신규 토론회 {len(new_forums)}건\n\n" + "\n\n".join(
        f"{f.get('TITLE','')}\n{f.get('SDATE','')} {f.get('STIME','')}\n{f.get('LOCATION','')}\n{f.get('LINK','')}"
        for f in new_forums)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[국회토론회] 새 일정 {len(new_forums)}건"
    msg["From"] = sender
    msg["To"]   = receiver
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print(f"[OK] 이메일 발송 완료")
    except Exception as e:
        print(f"[ERROR] 이메일 발송 실패: {e}")


def write_github_summary(new_forums, upcoming):
    sf = os.environ.get("GITHUB_STEP_SUMMARY")
    if not sf:
        return
    with open(sf, "a", encoding="utf-8") as f:
        f.write(f"## 🏛️ 국회토론회 일정 업데이트\n\n")
        f.write(f"- 기간: `{DATE_FROM}` ~ `{DATE_TO}`\n")
        f.write(f"- 예정: **{len(upcoming)}건** / 신규: **{len(new_forums)}건**\n\n")
        if upcoming:
            f.write("| 날짜 | 시간 | 제목 | 장소 |\n|------|------|------|------|\n")
            for fo in upcoming:
                f.write(f"| {fo.get('SDATE','')} | {fo.get('STIME','')} | [{fo.get('TITLE','')}]({fo.get('LINK','')}) | {fo.get('LOCATION','')} |\n")


def main():
    print(f"[{TODAY.strftime('%Y-%m-%d %H:%M')}] 조회 시작 ({DATE_FROM} ~ {DATE_TO})")

    all_forums = fetch_forums(page=1, page_size=100)
    print(f"API 응답: {len(all_forums)}건")
    if not all_forums:
        print("[WARN] 결과 없음")
        return

    upcoming = filter_upcoming(all_forums)
    print(f"향후 60일 이내: {len(upcoming)}건")

    # 웹페이지용 JSON 저장 (항상 실행)
    save_forums_json(upcoming)

    # 신규 감지 및 알림
    seen = load_seen()
    new_forums, seen = find_new_forums(upcoming, seen)
    print(f"신규: {len(new_forums)}건")

    if new_forums:
        save_seen(seen)
        send_teams_alert(new_forums)
        send_email_alert(new_forums)

    write_github_summary(new_forums, upcoming)
    print("완료.")


if __name__ == "__main__":
    main()
