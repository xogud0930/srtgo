"""전체화면 대시보드 UI.

조건, 대상 열차, 대기 상태를 한 화면에 두고 Enter 로 굴린다.
조건과 설정도 이 안에서 고친다(e / s). 예매 로직만 srtgo.py 것을 그대로 쓰고
keyring 키도 같아서, --classic 메뉴와 설정을 공유한다.
"""

import threading
import time
from datetime import datetime, timedelta
from random import gammavariate

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from . import srtgo as core


STYLE = Style.from_dict({
    "title": "bold #ffffff bg:#005f87",
    "label": "#8a8a8a",
    "value": "bold #ffffff",
    "head": "bold #00afaf",
    "ok": "bold #00d700",
    "warn": "bold #ffaf00",
    "err": "bold #ff5f5f",
    "dim": "#6c6c6c",
    "cursor": "bold #ffffff bg:#5f5faf",
    "picked": "bold #00d700",
    "key": "bold #ffaf00",
})

PHASE_TEXT = {
    "idle": ("dim", "대기"),
    "login": ("warn", "로그인 중"),
    "search": ("warn", "조회 중"),
    "picking": ("head", "열차 선택"),
    "running": ("warn", "예매 대기 중"),
    "paused": ("dim", "일시정지"),
    "done": ("ok", "예매 완료"),
    "error": ("err", "오류"),
}

SPINNER = "|/-\\"

WIDTH = 62  # 화면 폭(칸). 한글은 2칸이라 len() 이 아니라 get_cwidth() 로 센다.


def pad(text, width):
    """표시 폭 기준 왼쪽 정렬."""
    return text + " " * max(0, width - get_cwidth(text))


def rpad(text, width):
    return " " * max(0, width - get_cwidth(text)) + text


def rule(title):
    """구분선. 한글만 2칸이고 나머지는 ASCII 라 어느 터미널에서나 폭이 같다.

    ponytail: 박스 문자(U+2500)나 화살표는 East Asian Ambiguous 라
    한국어 터미널에서 2칸으로 그려져 정렬이 깨진다. 그래서 ASCII 만 쓴다.
    """
    head = f"-- {title} "
    return head + "-" * max(0, WIDTH - get_cwidth(head))


class State:
    def __init__(self, rail_type, debug=False):
        self.rail_type = rail_type
        self.debug = debug
        self.rail = None
        self.user = ""
        self.phase = "idle"
        self.trains = []
        self.cursor = 0
        self.picked = set()
        self.attempts = 0
        self.started_at = None
        self.last_msg = ""
        self.last_at = ""
        self.result = ""
        self.stop = threading.Event()
        self.pause = threading.Event()
        # 화면 전환: dash(대시보드) / form(항목 목록) / pick(값 고르기) / edit(문자 입력)
        self.screen = "dash"
        self.form_title = ""
        self.fields = []
        self.fcur = 0
        self.pick_opts = []
        self.pick_cur = 0
        self.pick_sel = set()
        self.pick_multi = False
        self.pick_field = None
        self.buf = ""
        self.notice = ""

    # --- 저장된 조건 (srtgo.py 와 같은 keyring 키를 읽고 쓴다) ---
    def get(self, key, default=""):
        return core.keyring.get_password(self.rail_type, key) or default

    @property
    def options(self):
        return core.get_options()

    def counts(self):
        """승객 종류별 인원수. 옵션에서 켠 것만."""
        out = {"adult": int(self.get("adult", "1") or 1)}
        for key in ("child", "senior", "disability1to3", "disability4to6"):
            if key in self.options:
                out[key] = int(self.get(key, "0") or 0)
        return {k: v for k, v in out.items() if v > 0}

    def passengers(self):
        is_srt = self.rail_type == "SRT"
        cls = {
            "adult": core.Adult if is_srt else core.AdultPassenger,
            "child": core.Child if is_srt else core.ChildPassenger,
            "senior": core.Senior if is_srt else core.SeniorPassenger,
            "disability1to3": (core.Disability1To3 if is_srt
                               else core.Disability1To3Passenger),
            "disability4to6": (core.Disability4To6 if is_srt
                               else core.Disability4To6Passenger),
        }
        return [cls[k](v) for k, v in self.counts().items()]

    def search_params(self):
        is_srt = self.rail_type == "SRT"
        total = sum(self.counts().values())
        adult = core.Adult if is_srt else core.AdultPassenger
        params = {
            "dep": self.get("departure"),
            "arr": self.get("arrival"),
            "date": self.get("date"),
            "time": self.get("time", "000000"),
            "passengers": [adult(total)],
        }
        if is_srt:
            params["available_only"] = False
        else:
            params["include_no_seats"] = True
            if "ktx" in self.options:
                params["train_type"] = core.TrainType.KTX
        return params

    def ready(self):
        return bool(self.get("departure") and self.get("arrival") and self.get("date"))


# --------------------------------------------------------------------------
# 화면


def _fmt_elapsed(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _seat_label(state):
    seat = state.get("seat_type")
    return {
        "GENERAL_FIRST": "일반실 우선",
        "GENERAL_ONLY": "일반실만",
        "SPECIAL_FIRST": "특실 우선",
        "SPECIAL_ONLY": "특실만",
    }.get(seat, "일반실 우선")


def _date_label(state):
    raw = state.get("date")
    if len(raw) != 8:
        return "-"
    d = datetime.strptime(raw, "%Y%m%d")
    return d.strftime("%m/%d ") + "월화수목금토일"[d.weekday()]


def render(state):
    out = []

    def line(*parts):
        out.extend(parts)
        out.append(("", "\n"))

    if state.screen == "pick":
        render_pick(state, line)
        return out
    if state.screen == "form":
        render_form(state, line)
        return out
    if state.screen == "edit":
        render_edit(state, line)
        return out

    def field(label, value, style="class:value"):
        return [("class:label", f"  {label}  "), (style, pad(str(value), 21))]

    who = f"{state.rail_type} | {state.user}" if state.user else state.rail_type
    line(("class:title", " srtgo " + rpad(who + " ", WIDTH - 7)))
    line()

    if not state.ready():
        line(("class:warn", "  조건이 비어 있습니다. "),
             ("class:key", "e"), ("class:warn", " 를 눌러 구간과 날짜를 정하세요."))
        line()
    else:
        line(*field("구간", f"{state.get('departure')} -> {state.get('arrival')}"),
             *field("날짜", _date_label(state)))
        line(*field("시각", f"{state.get('time', '000000')[:2]}시 이후"),
             *field("승객", ", ".join(f"{core.PASSENGER_LABEL.get(k, k)} {v}"
                                      for k, v in state.counts().items())))
        line(*field("좌석", _seat_label(state)),
             *field("결제", "카드 자동" if state.get("pay") == "1" else "안 함"))
        line()

    # 대상 열차
    line(("class:head", rule("대상 열차")))
    if not state.trains:
        line(("class:dim", "  조회 전입니다. "), ("class:key", "Enter"),
             ("class:dim", " 를 누르면 열차를 가져옵니다."))
    else:
        for i, train in enumerate(state.trains):
            mark = "[*]" if i in state.picked else "[ ]"
            style = "class:cursor" if i == state.cursor else (
                "class:picked" if i in state.picked else "")
            text = f" {mark} {core.strip_color(str(train))}"
            line((style, pad(text, WIDTH)))
    line()

    # 상태
    line(("class:head", rule("상태")))
    style, label = PHASE_TEXT[state.phase]
    if state.phase in ("running", "paused"):
        elapsed = time.time() - (state.started_at or time.time())
        spin = SPINNER[state.attempts & 3] if state.phase == "running" else " "
        line(("class:label", "  "), (f"class:{style}", f"{label} {spin}"),
             ("class:label", "   시도 "), ("class:value", f"{state.attempts}회"),
             ("class:label", "   경과 "), ("class:value", _fmt_elapsed(elapsed)),
             ("class:label", "   간격 "), ("class:value", f"{core.get_interval()}초"))
    else:
        line(("class:label", "  "), (f"class:{style}", label))

    if state.last_msg:
        line(("class:label", "  마지막  "), ("class:dim", f"{state.last_at}  "),
             ("class:value", state.last_msg[:52]))
    if state.result:
        for row in state.result.split("\n"):
            line(("class:ok", f"  {row[:60]}"))
    line()

    # 키 안내
    hints = {
        "idle": [("Enter", "조회"), ("e", "조건"), ("s", "설정"),
                 ("t", "SRT/KTX"), ("q", "종료")],
        "picking": [("up/dn", "이동"), ("Space", "선택"), ("Enter", "시작"),
                    ("r", "재조회"), ("q", "취소")],
        "running": [("p", "일시정지"), ("q", "중지")],
        "paused": [("p", "재개"), ("q", "중지")],
        "done": [("Enter", "처음으로"), ("q", "종료")],
        "error": [("Enter", "처음으로"), ("s", "설정"), ("q", "종료")],
    }.get(state.phase, [("q", "종료")])
    parts = []
    for key, desc in hints:
        parts += [("class:key", f"  [{key}]"), ("class:label", f" {desc}")]
    line(*parts)
    return out



# --------------------------------------------------------------------------
# 항목 편집 (조건 / 설정)


SEAT_CHOICES = [
    ("일반실 우선", "GENERAL_FIRST"),
    ("일반실만", "GENERAL_ONLY"),
    ("특실 우선", "SPECIAL_FIRST"),
    ("특실만", "SPECIAL_ONLY"),
]

OPTION_CHOICES = [
    ("어린이", "child"),
    ("경로우대", "senior"),
    ("중증장애인", "disability1to3"),
    ("경증장애인", "disability4to6"),
    ("KTX만", "ktx"),
]


class Field:
    """keyring 값 하나를 화면에서 고치기 위한 항목.

    kind: pick(하나 고르기) / multi(여러 개) / num(증감) / text(입력) / bool(토글)
    """

    def __init__(self, service, key, label, kind, choices=None, default="",
                 lo=0, hi=9, step=1, mask=False, note=""):
        self.service = service
        self.key = key
        self.label = label
        self.kind = kind
        self.choices = choices or []
        self.default = default
        self.lo, self.hi, self.step = lo, hi, step
        self.mask = mask
        self.note = note

    def raw(self):
        return core.keyring.get_password(self.service, self.key) or self.default

    def save(self, value):
        core.keyring.set_password(self.service, self.key, str(value))

    def display(self):
        raw = self.raw()
        if self.kind == "bool":
            return "예" if raw == "1" else "아니오"
        if self.kind == "text":
            if not raw:
                return "(없음)"
            if self.mask == "tail":  # 카드번호: 뒤 4자리만
                return "*" * max(0, len(raw) - 4) + raw[-4:]
            return "*" * len(raw) if self.mask else raw
        if self.kind == "multi":
            picked = [v for v in raw.split(",") if v]
            labels = [lab for lab, val in self.choices if val in picked]
            return ", ".join(labels) if labels else "(없음)"
        if self.kind == "pick":
            for lab, val in self.choices:
                if val == raw:
                    return lab
            return raw or "(없음)"
        return raw

    def nudge(self, delta):
        """좌우키로 바로 바꿀 수 있는 것들."""
        if self.kind == "bool":
            self.save("0" if self.raw() == "1" else "1")
            return True
        if self.kind == "num":
            try:
                cur = float(self.raw() or self.lo)
            except ValueError:
                cur = self.lo
            cur = min(max(cur + delta * self.step, self.lo), self.hi)
            self.save(f"{cur:g}")
            return True
        if self.kind == "pick" and self.choices:
            values = [v for _, v in self.choices]
            try:
                i = values.index(self.raw())
            except ValueError:
                i = 0
            self.save(values[(i + delta) % len(values)])
            return True
        return False


def _saved_stations(rail_type):
    return [(n, n) for n in core.get_station(rail_type)[1]]


def _all_stations(rail_type):
    return [(n, n) for n in core.STATIONS[rail_type]]


def _date_choices(rail_type):
    now = datetime.now() + timedelta(minutes=10)
    span = (30 if rail_type == "SRT" else 31) - (0 if now.hour >= 7 else 1)
    out = []
    for i in range(span + 1):
        d = now + timedelta(days=i)
        out.append((d.strftime("%m/%d ") + "월화수목금토일"[d.weekday()],
                    d.strftime("%Y%m%d")))
    return out


def condition_fields(state):
    rt = state.rail_type
    stations = _saved_stations(rt)
    dates = _date_choices(rt)
    fields = [
        Field(rt, "departure", "출발역", "pick", stations,
              default=stations[0][1] if stations else ""),
        Field(rt, "arrival", "도착역", "pick", stations,
              default=stations[-1][1] if stations else ""),
        Field(rt, "date", "출발 날짜", "pick", dates, default=dates[0][1]),
        Field(rt, "time", "이 시각 이후", "pick",
              [(f"{h:02d}시", f"{h:02d}0000") for h in range(24)], default="120000"),
        Field(rt, "adult", "어른", "num", default="1", lo=0, hi=9),
    ]
    for key in ("child", "senior", "disability1to3", "disability4to6"):
        if key in core.get_options():
            fields.append(Field(rt, key, core.PASSENGER_LABEL[key], "num",
                                default="0", lo=0, hi=9))
    fields += [
        Field(rt, "seat_type", "좌석 종류", "pick", SEAT_CHOICES,
              default="GENERAL_FIRST"),
        Field(rt, "pay", "카드 결제", "bool", default="0"),
    ]
    return fields


def setting_fields(state):
    rt = state.rail_type
    return [
        Field(rt, "id", f"{rt} 아이디", "text",
              note="멤버십 번호 / 이메일 / 전화번호(하이픈 포함)"),
        Field(rt, "pass", f"{rt} 비밀번호", "text", mask=True),
        Field("card", "number", "카드번호", "text", mask="tail", note="하이픈 없이"),
        Field("card", "password", "카드 비밀번호", "text", mask=True, note="앞 2자리"),
        Field("card", "birthday", "생년월일", "text", note="YYMMDD 또는 사업자번호"),
        Field("card", "expire", "카드 유효기간", "text", note="YYMM"),
        Field(rt, "station", "역 목록", "multi", _all_stations(rt),
              note="Space 로 여러 개 선택. 여기 고른 역만 조건 화면에 나온다"),
        Field("SRT", "options", "예매 옵션", "multi", OPTION_CHOICES),
        Field("SRT", "interval", "조회 간격(초)", "num",
              default=f"{core.RESERVE_INTERVAL_DEFAULT:g}",
              lo=core.RESERVE_INTERVAL_RANGE[0], hi=core.RESERVE_INTERVAL_RANGE[1],
              step=0.5, note="짧을수록 빨리 잡지만 차단 위험이 커짐"),
        Field("telegram", "token", "텔레그램 token", "text", mask=True),
        Field("telegram", "chat_id", "텔레그램 chat_id", "text"),
    ]


def after_save(state, field):
    """저장 뒤 따라와야 하는 것들 (ok 플래그, 세션 무효화)."""
    if field.service == state.rail_type and field.key in ("id", "pass"):
        state.rail = None
        state.user = ""
        core.keyring.set_password(state.rail_type, "ok", "1")
    elif field.service == "card":
        done = all(core.keyring.get_password("card", k)
                   for k in ("number", "password", "birthday", "expire"))
        core.keyring.set_password("card", "ok", "1" if done else "0")
    elif field.service == "telegram":
        done = all(core.keyring.get_password("telegram", k)
                   for k in ("token", "chat_id"))
        core.keyring.set_password("telegram", "ok", "1" if done else "0")
    elif field.key == "station":
        # 역 목록이 줄면 저장된 출발/도착역이 목록 밖으로 나갈 수 있다.
        names = [n for n in (field.raw() or "").split(",") if n]
        for key in ("departure", "arrival"):
            if names and core.keyring.get_password(state.rail_type, key) not in names:
                core.keyring.set_password(state.rail_type, key, names[0])


def open_form(state, title, fields):
    state.screen = "form"
    state.form_title = title
    state.fields = fields
    state.fcur = 0
    state.notice = ""


def open_picker(state, field):
    state.pick_field = field
    state.pick_opts = field.choices
    state.pick_multi = field.kind == "multi"
    raw = field.raw()
    if state.pick_multi:
        chosen = {v for v in raw.split(",") if v}
        state.pick_sel = {i for i, (_, v) in enumerate(field.choices) if v in chosen}
        state.pick_cur = min(state.pick_sel, default=0)
    else:
        state.pick_sel = set()
        state.pick_cur = next(
            (i for i, (_, v) in enumerate(field.choices) if v == raw), 0)
    state.screen = "pick"


def commit_picker(state):
    field = state.pick_field
    if state.pick_multi:
        values = [field.choices[i][1] for i in sorted(state.pick_sel)]
        field.save(",".join(values))
    else:
        field.save(field.choices[state.pick_cur][1])
    after_save(state, field)
    state.screen = "form"


PICK_ROWS = 12


def render_pick(state, line):
    field = state.pick_field
    line(("class:title", " " + pad(field.label, WIDTH - 1)))
    line()
    total = len(state.pick_opts)
    top = max(0, min(state.pick_cur - PICK_ROWS // 2, total - PICK_ROWS))
    for i in range(top, min(top + PICK_ROWS, total)):
        label = state.pick_opts[i][0]
        if state.pick_multi:
            mark = "[*]" if i in state.pick_sel else "[ ]"
        else:
            mark = " > " if i == state.pick_cur else "   "
        style = "class:cursor" if i == state.pick_cur else (
            "class:picked" if i in state.pick_sel else "")
        line((style, pad(f" {mark} {label}", WIDTH)))
    if total > PICK_ROWS:
        line(("class:dim", f"  {state.pick_cur + 1}/{total}"))
    line()
    hints = ([("up/dn", "이동"), ("Space", "선택"), ("Enter", "완료"), ("Esc", "취소")]
             if state.pick_multi else
             [("up/dn", "이동"), ("Enter", "선택"), ("Esc", "취소")])
    parts = []
    for key, desc in hints:
        parts += [("class:key", f"  [{key}]"), ("class:label", f" {desc}")]
    line(*parts)


def render_form(state, line):
    line(("class:title", " " + pad(state.form_title, WIDTH - 1)))
    line()
    for i, field in enumerate(state.fields):
        style = "class:cursor" if i == state.fcur else ""
        arrow = ">" if i == state.fcur else " "
        row = f" {arrow} {pad(field.label, 21)}{field.display()}"
        line((style, pad(row, WIDTH)))
    line()
    field = state.fields[state.fcur] if state.fields else None
    if field and field.note:
        line(("class:dim", f"  {field.note}"))
    if state.notice:
        line(("class:warn", f"  {state.notice}"))
    line()
    kind = field.kind if field else ""
    parts = [("class:key", "  [up/dn]"), ("class:label", " 이동")]
    if kind in ("num", "bool"):
        parts += [("class:key", "  [<-/->]"), ("class:label", " 조절")]
    elif kind == "pick":
        parts += [("class:key", "  [<-/->]"), ("class:label", " 조절"),
                  ("class:key", "  [Enter]"), ("class:label", " 목록")]
    elif kind in ("text", "multi"):
        parts += [("class:key", "  [Enter]"),
                  ("class:label", " 입력" if kind == "text" else " 목록")]
    parts += [("class:key", "  [Esc]"), ("class:label", " 돌아가기")]
    line(*parts)


def render_edit(state, line):
    field = state.fields[state.fcur]
    line(("class:title", " " + pad(field.label, WIDTH - 1)))
    line()
    shown = "*" * len(state.buf) if field.mask else state.buf
    line(("class:label", "  "), ("class:value", pad(shown + "_", WIDTH - 2)))
    line()
    if field.note:
        line(("class:dim", f"  {field.note}"))
    line()
    line(("class:key", "  [Enter]"), ("class:label", " 저장"),
         ("class:key", "  [Esc]"), ("class:label", " 취소"))


# --------------------------------------------------------------------------
# 동작


def _tick(app, state):
    """대기 중에도 경과 시간이 흐르도록 주기적으로 다시 그린다."""
    while not state.stop.is_set():
        if state.phase in ("running", "paused", "search", "login"):
            app.invalidate()
        time.sleep(0.4)


def _note(state, app, msg, phase=None):
    state.last_msg = msg
    state.last_at = datetime.now().strftime("%H:%M:%S")
    if phase:
        state.phase = phase
    app.invalidate()


def _ensure_login(state, app):
    if state.rail is not None and getattr(state.rail, "logined", False):
        return True
    _note(state, app, "로그인 중", "login")
    try:
        state.rail = core.login(state.rail_type, debug=state.debug)
    except Exception as ex:
        _note(state, app, f"로그인 실패: {ex}", "error")
        return False
    if not getattr(state.rail, "logined", False):
        _note(state, app, "로그인 실패 (로그인 설정 확인)", "error")
        return False
    state.user = getattr(state.rail, "name", "") or ""
    return True


def do_search(state, app):
    """조회만 하고 열차 선택 단계로."""
    if not state.ready():
        _note(state, app, "구간과 날짜를 먼저 정하세요", "error")
        return
    if not _ensure_login(state, app):
        return
    _note(state, app, "열차 조회 중", "search")
    try:
        trains = state.rail.search_train(**state.search_params())
    except Exception as ex:
        _note(state, app, f"조회 실패: {ex}", "error")
        return
    state.trains = trains
    state.cursor = 0
    state.picked = set()
    if not trains:
        _note(state, app, "조회된 열차가 없습니다", "error")
        return
    _note(state, app, f"{len(trains)}편 조회됨. Space 로 대상을 고르세요", "picking")


def do_reserve_loop(state, app):
    """선택한 열차 중 자리가 나는 것을 잡을 때까지 반복."""
    params = state.search_params()
    passengers = state.passengers()
    seat_type = state.get("seat_type") or "GENERAL_FIRST"
    want_pay = state.get("pay") == "1"
    targets = sorted(state.picked)

    state.attempts = 0
    state.started_at = time.time()
    _note(state, app, "시작", "running")

    while not state.stop.is_set():
        if state.pause.is_set():
            state.phase = "paused"
            app.invalidate()
            time.sleep(0.3)
            continue
        state.phase = "running"
        state.attempts += 1
        try:
            trains = state.rail.search_train(**params)
            state.trains = trains
            for i in targets:
                if i >= len(trains):
                    continue
                if core._is_seat_available(trains[i], seat_type, state.rail_type):
                    _finish(state, app, trains[i], passengers, seat_type, want_pay)
                    return
            _note(state, app, "매진")
        except Exception as ex:
            msg = getattr(ex, "msg", str(ex))
            if "Need to Login" in msg or "로그인 후 사용하십시오" in msg:
                state.rail = None
                if not _ensure_login(state, app):
                    return
                _note(state, app, "세션 재로그인")
            else:
                _note(state, app, msg[:60])
        _sleep_interruptible(state)


def _finish(state, app, train, passengers, seat_type, want_pay):
    try:
        rsv = state.rail.reserve(train, passengers=passengers, option=seat_type)
    except Exception as ex:
        _note(state, app, f"예매 실패: {getattr(ex, 'msg', ex)}")
        return
    lines = ["🎫 예매 성공", str(rsv)]
    lines += [str(t) for t in getattr(rsv, "tickets", None) or []]
    if want_pay and not getattr(rsv, "is_waiting", False):
        lines.append("💳 결제 성공" if core.pay_card(state.rail, rsv) else "결제 실패")
    state.result = "\n".join(lines)
    _note(state, app, "완료", "done")
    try:
        core.asyncio.run(core.get_telegram()("\n".join(lines)))
    except Exception:
        pass


def _sleep_interruptible(state):
    """gamma 분포 간격. 중지 요청이 오면 즉시 깬다."""
    mean = core.get_interval()
    floor = mean / 6
    delay = gammavariate(core.RESERVE_INTERVAL_SHAPE,
                         (mean - floor) / core.RESERVE_INTERVAL_SHAPE) + floor
    state.stop.wait(delay)


def _spawn(fn, state, app):
    threading.Thread(target=fn, args=(state, app), daemon=True).start()


# --------------------------------------------------------------------------


def build_app(state):
    """상태 하나에 화면과 키를 묶는다. 테스트에서도 이 함수를 쓴다."""
    kb = KeyBindings()

    control = FormattedTextControl(lambda: render(state), focusable=True)
    app = Application(
        layout=Layout(Window(control, always_hide_cursor=True)),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
        refresh_interval=0.5,
    )

    def field():
        return state.fields[state.fcur] if state.fields else None

    def move(delta):
        """현재 화면의 커서 이동."""
        if state.screen == "pick" and state.pick_opts:
            state.pick_cur = (state.pick_cur + delta) % len(state.pick_opts)
        elif state.screen == "form" and state.fields:
            state.fcur = (state.fcur + delta) % len(state.fields)
            state.notice = ""
        elif state.screen == "dash" and state.trains:
            state.cursor = (state.cursor + delta) % len(state.trains)

    @kb.add("up")
    def _(event):
        move(-1)

    @kb.add("down")
    def _(event):
        move(1)

    @kb.add("left")
    def _(event):
        if state.screen == "form" and field():
            field().nudge(-1)
            after_save(state, field())

    @kb.add("right")
    def _(event):
        if state.screen == "form" and field():
            field().nudge(1)
            after_save(state, field())

    @kb.add("escape", eager=True)
    def _(event):
        if state.screen == "edit":
            state.screen = "form"
        elif state.screen == "pick":
            state.screen = "form"
        elif state.screen == "form":
            state.screen = "dash"
            state.fields = []

    @kb.add("space")
    def _(event):
        if state.screen == "pick" and state.pick_multi:
            state.pick_sel ^= {state.pick_cur}
        elif state.screen == "dash" and state.phase == "picking" and state.trains:
            state.picked ^= {state.cursor}
        elif state.screen == "edit":
            state.buf += " "

    @kb.add("enter")
    def _(event):
        if state.screen == "pick":
            commit_picker(state)
            return
        if state.screen == "edit":
            field().save(state.buf)
            after_save(state, field())
            state.screen = "form"
            return
        if state.screen == "form":
            current = field()
            if current is None:
                return
            if current.kind in ("pick", "multi"):
                open_picker(state, current)
            elif current.kind == "text":
                state.buf = current.raw()
                state.screen = "edit"
            else:
                current.nudge(1)
                after_save(state, current)
            return
        # 대시보드
        if state.phase in ("idle", "error"):
            _spawn(do_search, state, app)
        elif state.phase == "picking":
            if not state.picked:
                _note(state, app, "Space 로 열차를 하나 이상 고르세요")
            else:
                _spawn(do_reserve_loop, state, app)
        elif state.phase == "done":
            state.result = ""
            state.trains = []
            state.phase = "idle"

    @kb.add("backspace")
    def _(event):
        if state.screen == "edit":
            state.buf = state.buf[:-1]

    @kb.add("<any>")
    def _(event):
        """문자 입력 화면에서만 글자를 받는다. 나머지 화면의 단축키는 아래에서 처리."""
        if state.screen != "edit":
            return
        text = event.data
        if text and text.isprintable():
            state.buf += text

    typing = Condition(lambda: state.screen == "edit")
    not_typing = ~typing

    def shortcut(key, handler):
        @kb.add(key, filter=not_typing)
        def _(event):
            if state.screen == "dash":
                handler(event)

    def on_q(event):
        if state.phase in ("running", "paused"):
            state.stop.set()
            state.pause.clear()
            _note(state, app, "중지됨", "idle")
            state.stop = threading.Event()
        else:
            state.stop.set()
            event.app.exit()

    @kb.add("q", filter=not_typing)
    @kb.add("c-c")
    def _(event):
        if state.screen == "edit":
            state.screen = "form"  # c-c 는 입력 취소
            return
        if state.screen in ("form", "pick"):
            state.screen = "form" if state.screen == "pick" else "dash"
            return
        on_q(event)

    shortcut("r", lambda event: (
        _spawn(do_search, state, app)
        if state.phase in ("picking", "idle", "error") else None))

    def on_p(event):
        if state.phase == "running":
            state.pause.set()
        elif state.phase == "paused":
            state.pause.clear()

    shortcut("p", on_p)

    def on_e(event):
        if state.phase in ("running", "paused"):
            return
        open_form(state, "예매 조건", condition_fields(state))

    shortcut("e", on_e)

    def on_s(event):
        if state.phase in ("running", "paused"):
            return
        open_form(state, "설정", setting_fields(state))

    shortcut("s", on_s)

    def on_t(event):
        if state.phase in ("running", "paused"):
            return
        state.rail_type = "SRT" if state.rail_type == "KTX" else "KTX"
        core.keyring.set_password("srtgo", "rail_type", state.rail_type)
        state.rail = None
        state.user = ""
        state.trains = []
        state.picked = set()
        _note(state, app, f"{state.rail_type} 로 전환", "idle")

    shortcut("t", on_t)

    threading.Thread(target=_tick, args=(app, state), daemon=True).start()
    return app


def run(rail_type="KTX", debug=False):
    build_app(State(rail_type, debug)).run()
