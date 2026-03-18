"""
국회토론회 일정 수집기
열린국회정보 API (nfcoioopazrwmjrgs) 를 호출해서
새 토론회 일정을 감지하고 이메일로 알립니다.

실제 API 응답 필드:
  TITLE      : 토론회 제목
  SDATE      : 날짜 (예: 2026-03-27)
  STIME      : 시간 (예: 14:00~16:00)
  LOCATION   : 장소
  NAME       : 주최자/의원실
  LINK       : 상세 링크
  DESCRIPTION: 상세 설명
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path


# ────────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────────
API_KEY   = os.environ.get("ASSEMBLY_API_KEY", "YOUR_API_KEY_HERE")
DATA_FILE = Path("data/seen_forums.json")
BASE_URL  = "https://open.assembly.go.kr/portal/openapi/nfcoioopazrwmjrgs"

TODAY     = datetime.today()
DATE_FROM = TODAY.strftime("%Y-%m-%d")
DATE_TO   = (TODAY + timedelta(days=30)).strftime("%Y-%m-%d")


# ────────────────────────────────────────────────
# API 호출
# ────────────────────────────────────────────────
def fetch_forums(page: int = 1, page_size: int = 100) -> list[dict]:
    params = {
        "KEY":    API_KEY,
        "Type":   "json",
        "pIndex": page,
        "pSize":  page_size,
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] API 호출 실패: {e}")
        return []

    try:
        data = resp.json()
        root = data.get("nfcoioopazrwmjrgs", [])
        for block in root:
            if "row" in block:
                r = block["row"]
                return r if isinstance(r, list) else [r]
        return []
    except Exception as e:
        print(f"[ERROR] JSON 파싱 실패: {e}")
        print("응답 원문:", resp.text[:300])
        return []


# ────────────────────────────────────────────────
# 날짜 필터 (오늘 ~ 30일 후)
# ────────────────────────────────────────────────
def filter_upcoming(forums: list[dict]) -> list[dict]:
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


# ────────────────────────────────────────────────
# 신규 토론회 감지
# ────────────────────────────────────────────────
def find_new_forums(forums: list[dict], seen: set[str]) -> tuple[list[dict], set[str]]:
    new = []
    for f in forums:
        fid = f.get("LINK") or f.get("TITLE", "") + f.get("SDATE", "")
        if fid and fid not in seen:
            new.append(f)
            seen.add(fid)
    return new, seen


# ────────────────────────────────────────────────
# 출력 포맷
# ────────────────────────────────────────────────
def format_forum(f: dict) -> str:
    title = f.get("TITLE", "(제목 없음)")
    date_ = f.get("SDATE", "")
    time_ = f.get("STIME", "")
    place = f.get("LOCATION", "")
    host  = f.get("NAME", "")
    link  = f.get("LINK", "")
    return (
        f"📋 {title}\n"
        f"   📅 일시: {date_} {time_}\n"
        f"   📍 장소: {place}\n"
        f"   🏛️  주최: {host}\n"
        f"   🔗 링크: {link}"
    )


# ────────────────────────────────────────────────
# GitHub Actions Step Summary
# ────────────────────────────────────────────────
def write_github_summary(new_forums: list[dict], upcoming: list[dict]):
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    with open(summary_file, "a", encoding="utf-8") as f:
        f.write("## 🏛️ 국회토론회 일정 업데이트\n\n")
        f.write(f"- 조회 기간: `{DATE_FROM}` ~ `{DATE_TO}`\n")
        f.write(f"- 예정 토론회: **{len(upcoming)}건**\n")
        f.write(f"- 신규 발견: **{len(new_forums)}건**\n\n")

        if new_forums:
            f.write("### 🆕 새로 등록된 토론회\n\n")
            f.write("| 제목 | 날짜 | 시간 | 장소 | 주최 |\n")
            f.write("|------|------|------|------|------|\n")
            for forum in new_forums:
                title = forum.get("TITLE", "")
                date_ = forum.get("SDATE", "")
                time_ = forum.get("STIME", "")
                place = forum.get("LOCATION", "")
                host  = forum.get("NAME", "")
                link  = forum.get("LINK", "")
                f.write(f"| [{title}]({link}) | {date_} | {time_} | {place} | {host} |\n")
        else:
            f.write("_신규 토론회 없음_\n")

        if upcoming:
            f.write("\n### 📅 향후 30일 전체 일정\n\n")
            f.write("| 제목 | 날짜 | 시간 | 장소 |\n")
            f.write("|------|------|------|------|\n")
            for forum in sorted(upcoming, key=lambda x: x.get("SDATE", "")):
                title = forum.get("TITLE", "")
                date_ = forum.get("SDATE", "")
                time_ = forum.get("STIME", "")
                place = forum.get("LOCATION", "")
                link  = forum.get("LINK", "")
                f.write(f"| [{title}]({link}) | {date_} | {time_} | {place} |\n")


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
        print("[SKIP] 이메일 환경변수 미설정 — 알림 건너뜀")
        return

    body  = f"국회토론회 신규 일정 {len(new_forums)}건이 등록되었습니다.\n\n"
    body += "=" * 50 + "\n\n"
    body += "\n\n".join(format_forum(f) for f in new_forums)
    body += f"\n\n조회일시: {TODAY.strftime('%Y-%m-%d %H:%M')}"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[국회토론회] 새 일정 {len(new_forums)}건 ({DATE_FROM} ~ {DATE_TO})"
    msg["From"]    = sender
    msg["To"]      = receiver

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print(f"[OK] 이메일 발송 완료 → {receiver}")
    except Exception as e:
        print(f"[ERROR] 이메일 발송 실패: {e}")


# ────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────
def main():
    print(f"[{TODAY.strftime('%Y-%m-%d %H:%M')}] 국회토론회 API 조회 시작")
    print(f"필터 기간: {DATE_FROM} ~ {DATE_TO}")

    all_forums = fetch_forums(page=1, page_size=100)
    print(f"API 응답: {len(all_forums)}건 (전체)")

    if not all_forums:
        print("[WARN] 조회 결과 없음 — API 키를 확인하세요.")
        return

    upcoming = filter_upcoming(all_forums)
    print(f"향후 30일 이내 토론회: {len(upcoming)}건")

    seen = load_seen()
    new_forums, seen = find_new_forums(upcoming, seen)
    print(f"신규 발견: {len(new_forums)}건")

    if upcoming:
        print("\n── 향후 30일 토론회 일정 ──")
        for f in sorted(upcoming, key=lambda x: x.get("SDATE", "")):
            print(format_forum(f))
            print()

    if new_forums:
        save_seen(seen)
        send_email_alert(new_forums)
    else:
        print("신규 토론회 없음 (이미 알림 보낸 것들)")

    write_github_summary(new_forums, upcoming)
    print("완료.")


if __name__ == "__main__":
    main()
