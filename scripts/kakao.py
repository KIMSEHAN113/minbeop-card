"""카카오톡 '나에게 보내기'로 오늘의 민법 카드를 보낸다.

  python scripts/kakao.py          오늘 카드 발송 (data/days.json 에 오늘 날짜가 없으면 건너뜀)
  python scripts/kakao.py setup    최초 1회: 인가 코드로 토큰을 받아 GitHub Secret 에 저장

환경 변수 (GitHub Secrets)
  KAKAO_REST_KEY        카카오 앱 REST API 키
  KAKAO_CLIENT_SECRET   (선택) 앱에서 Client Secret 을 켰다면
  KAKAO_REFRESH_TOKEN   setup 이 저장하는 리프레시 토큰
  GH_PAT                토큰이 갱신될 때 Secret 을 자동으로 바꾸기 위한 GitHub 토큰
  CARD_URL              카드 페이지 주소 (예: https://kimsehan113.github.io/minbeop-card/)
  SEND_DATE             (선택) 이 날짜(YYYY-MM-DD) 카드를 보냄. 테스트용

토큰 값은 절대 출력하지 않는다. 공개 저장소의 Actions 로그는 누구나 볼 수 있다.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAYS = ROOT / "data" / "days.json"
KST = timezone(timedelta(hours=9))


def post(url, data, token=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded;charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            j = json.loads(body)
            msg = j.get("error_description") or j.get("msg") or body
        except Exception:
            msg = body
        raise SystemExit(f"카카오 API 오류 {e.code}: {msg[:200]}")


def mask(value):
    if value and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{value}")


def save_secret(name, value):
    """GH_PAT 가 있으면 GitHub Secret 을 갱신한다. 성공 여부를 돌려준다."""
    repo, pat = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GH_PAT")
    if not (repo and pat):
        return False
    r = subprocess.run(["gh", "secret", "set", name, "--repo", repo, "--body", value],
                       env={**os.environ, "GH_TOKEN": pat}, capture_output=True, text=True)
    if r.returncode:
        print(f"  ! Secret {name} 저장 실패: {r.stderr.strip()[:200]}")
    return r.returncode == 0


def token_request(extra):
    # 복사할 때 딸려 온 공백·줄바꿈은 떼고 쓴다
    data = {"client_id": os.environ["KAKAO_REST_KEY"].strip(), **extra}
    if os.environ.get("KAKAO_CLIENT_SECRET", "").strip():
        data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"].strip()
    j = post("https://kauth.kakao.com/oauth/token", data)
    mask(j.get("access_token"))
    mask(j.get("refresh_token"))
    return j


def send_memo(access, tpl):
    r = post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
             {"template_object": json.dumps(tpl, ensure_ascii=False)}, access)
    print("카카오톡 발송 완료" if r.get("result_code") == 0 else f"발송 응답: {r}")


def setup():
    code = os.environ.get("KAKAO_AUTH_CODE", "").strip()
    if not code and os.environ.get("GITHUB_EVENT_PATH"):  # 워크플로 입력은 이벤트 파일에서 읽어 로그에 안 남긴다
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        code = str((event.get("inputs") or {}).get("code", "")).strip()
    if "code=" in code:  # 주소창 전체를 붙여넣어도 된다
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("code", [""])[0]
    mask(code)
    if not code:
        raise SystemExit("인가 코드가 비어 있어요.")
    j = token_request({"grant_type": "authorization_code", "code": code,
                       "redirect_uri": os.environ["CARD_URL"]})
    if not save_secret("KAKAO_REFRESH_TOKEN", j["refresh_token"]):
        raise SystemExit("리프레시 토큰을 Secret 에 저장하지 못했어요. GH_PAT 설정을 확인해 주세요.")
    days = int(j.get("refresh_token_expires_in", 0)) // 86400
    print(f"완료: 리프레시 토큰을 저장했어요 (유효 {days}일, 사용할 때마다 자동 갱신).")
    url = os.environ["CARD_URL"]
    send_memo(j["access_token"], {
        "object_type": "text",
        "text": "민법 아침카드 연결이 끝났어요.\n내일 아침 7시쯤부터 이 채팅으로 오늘의 민법 카드가 와요.",
        "link": {"web_url": url, "mobile_web_url": url}, "button_title": "카드 미리 보기"})


def send():
    today = os.environ.get("SEND_DATE", "").strip() or datetime.now(KST).date().isoformat()
    data = json.loads(DAYS.read_text(encoding="utf-8"))
    day = next((d for d in data["days"] if d["date"] == today), None)
    if not day:
        print(f"{today} 카드가 아직 없어 건너뛰어요.")
        return
    j = token_request({"grant_type": "refresh_token", "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"]})
    if j.get("refresh_token"):  # 만료 한 달 전부터 새 리프레시 토큰이 온다
        print("리프레시 토큰 갱신 완료" if save_secret("KAKAO_REFRESH_TOKEN", j["refresh_token"])
              else "  ! 리프레시 토큰 갱신 실패 (GH_PAT 확인 필요)")
    url = os.environ["CARD_URL"].rstrip("/") + f"/?d={day['date']}"
    exams = " · ".join(day["exams"])
    text = (f"📘 {day['dateLabel']} 오늘의 민법\n"
            f"주제: {day['topic']}\n\n"
            f"{exams} 기출 OX 5문\n조문 3개 · 판례 1개")
    send_memo(j["access_token"], {
        "object_type": "text", "text": text[:200],
        "link": {"web_url": url, "mobile_web_url": url}, "button_title": "오늘 문제 풀기"})


if __name__ == "__main__":
    {"setup": setup}.get((sys.argv[1:] or [""])[0], send)()
