"""
국회토론회 일정 수집기
열린국회정보 API (nfcoioopazrwmjrgs) 를 호출해서
새 토론회 일정을 감지하고 알림을 보냅니다.
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path


# ────────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────────
API_KEY    = os.environ.get("ASSEMBLY_API_KEY", "YOUR_API_KEY_HERE")
DATA_FILE  = Path("data/seen_forums.json")   # 이미 본 토론회 ID 저장
BASE_URL   = "https://open.assembly.go.kr/portal/openapi/nfcoioopazrwmjrgs"

# 조회 기간: 오늘부터 30일 후까지
TODAY      = datetime.today()
DATE_FROM  = TODAY.strftime("%Y%m%d")
DATE_TO    = (TODAY + timedelta(days=30)).strftime("%Y%m%d")


# ────────────────────────────────────────────────
# API 호출
# ────────────────────────────────────────────────
def fetch_forums(page: int = 1, page_size: int = 100) -> list[dict]:
    """국회토론회 API 호출 → 파싱된 dict 리스트 반환"""
    params = {
        "KEY":      API_KEY,
        "Type":     "json",
        "pIndex":   page,
        "pSize":    page_size,
        # 날짜 필터 (API가 지원하는 경우 활용)
        "FROM_DATE": DATE_FROM,
        "TO_DATE":   DATE_TO,
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] API 호출 실패: {e}")
        return []

    # ── JSON 응답 파싱 ──
    try:
        data = resp.json()
        # 열린국회 API는 최상위 키가 오퍼레이션명인 경우가 많음
        root = data.get("nfcoioopazrwmjrgs") or data.get("response") or data
        if isinstance(root, list):
            root = root[0]
        rows = root.get("row", [])
        # 단건일 때 dict로 올 수 있으므로 정규화
        if isinstance(rows, dict):
            rows = [rows]
        return rows

    except (ValueError, KeyError, TypeError):
        pass

    # ── XML 폴백 파싱 ──
    try:
        tree = ET.fromstring(resp.content)
        rows = []
        for item in tree.iter("row"):
            rows.append({child.tag: child.text for child in item})
        return rows
    except ET.ParseError as e:
        print(f"[ERROR] 응답 파싱 실패: {e}")
        print("응답 원문:", resp.text[:500])
        return []


# ────────────────────────────────────────────────
# 이미 본 토론회 관리
# ────────────────────────────────────────────────
def load_seen() -> set[str]:
    if DATA_FILE.exists():
        return set(json.loads(DATA_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set[str]):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


# ────────────────────────────────────────────────
# 신규 토론회 감지
# ────────────────────────────────────────────────
def find_new_forums(forums: list[dict], seen: set[str]) -> list[dict]:
    new = []
    for f in forums:
        # ID 필드: API 응답 구조에 따라 키 이름이 다를 수 있어 여러 후보를 시도
        fid = (
            f.get("FORUM_ID")
            or f.get("BLL_ID")
            or f.get("CONFER_SEQNO")
            or f.get("SEQNO")
            or str(f)  # fallback
        )
        if fid and fid not in seen:
            new.append(f)
    return new


# ────────────────────────────────────────────────
# 출력 포맷
# ────────────────────────────────────────────────
def format_forum(f: dict) -> str:
    title  = f.get("FORUM_NM") or f.get("TITLE") or f.get("CONFER_NM") or "(제목 없음)"
    date_  = f.get("FORUM_DT") or f.get("OPEN_DT") or f.get("DATE") or ""
    place  = f.get("PLACE_NM") or f.get("PLACE") or ""
    host   = f.get("HOST_NM") or f.get("COMMITTEE_NM") or ""
    return (
        f"📋 {title}\n"
        f"   📅 일시: {date_}\n"
        f"   📍 장소: {place}\n"
        f"   🏛️  주최: {host}"
    )


# ────────────────────────────────────────────────
# GitHub Actions 환경에서 요약을 Step Summary에 출력
# ────────────────────────────────────────────────
def write_github_summary(new_forums: list[dict], total: int):
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    with open(summary_file, "a", encoding="utf-8") as f:
        f.write(f"## 🏛️ 국회토론회 일정 업데이트\n\n")
        f.write(f"- 조회 기간: `{DATE_FROM}` ~ `{DATE_TO}`\n")
        f.write(f"- 전체 건수: **{total}건**\n")
        f.write(f"- 신규 발견: **{len(new_forums)}건**\n\n")

        if new_forums:
            f.write("### 🆕 새로 등록된 토론회\n\n")
            f.write("| 제목 | 일시 | 장소 | 주최 |\n")
            f.write("|------|------|------|------|\n")
            for forum in new_forums:
                title = forum.get("FORUM_NM") or forum.get("TITLE") or ""
                date_ = forum.get("FORUM_DT") or forum.get("OPEN_DT") or ""
                place = forum.get("PLACE_NM") or forum.get("PLACE") or ""
                host  = forum.get("HOST_NM") or forum.get("COMMITTEE_NM") or ""
                f.write(f"| {title} | {date_} | {place} | {host} |\n")
        else:
            f.write("_신규 토론회 없음_\n")


# ────────────────────────────────────────────────
# 이메일 알림 (선택 — Gmail SMTP 사용)
# ────────────────────────────────────────────────
def send_email_alert(new_forums: list[dict]):
    """
    환경변수 GMAIL_USER, GMAIL_PASS, ALERT_EMAIL 이 설정된 경우에만 동작합니다.
    Gmail → 설정 → 2단계 인증 → 앱 비밀번호를 GMAIL_PASS에 넣어주세요.
    """
    import smtplib
    from email.mime.text import MIMEText

    sender   = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_PASS")
    receiver = os.environ.get("ALERT_EMAIL", sender)

    if not (sender and password):
        print("[SKIP] 이메일 환경변수 미설정 — 알림 건너뜀")
        return

    body = f"국회토론회 신규 일정 {len(new_forums)}건이 등록되었습니다.\n\n"
    body += "\n\n".join(format_forum(f) for f in new_forums)
    body += f"\n\n조회일시: {TODAY.strftime('%Y-%m-%d %H:%M')}"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[국회토론회] 새 일정 {len(new_forums)}건 등록"
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
    print(f"조회 기간: {DATE_FROM} ~ {DATE_TO}")

    forums = fetch_forums()
    print(f"API 응답: {len(forums)}건")

    if not forums:
        print("[WARN] 조회 결과 없음 — API 키 또는 파라미터를 확인하세요.")
        return

    # 전체 목록 콘솔 출력
    print("\n── 전체 토론회 목록 ──")
    for f in forums:
        print(format_forum(f))
        print()

    # 신규 감지
    seen       = load_seen()
    new_forums = find_new_forums(forums, seen)
    print(f"\n[신규] {len(new_forums)}건 발견")

    if new_forums:
        print("\n── 신규 토론회 ──")
        for f in new_forums:
            print(format_forum(f))
            fid = (
                f.get("FORUM_ID") or f.get("BLL_ID")
                or f.get("CONFER_SEQNO") or f.get("SEQNO") or str(f)
            )
            seen.add(fid)
        save_seen(seen)
        send_email_alert(new_forums)
    else:
        print("신규 토론회 없음")

    write_github_summary(new_forums, len(forums))
    print("\n완료.")


if __name__ == "__main__":
    main()
