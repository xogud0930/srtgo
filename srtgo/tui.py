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
from prompt_toolkit.application.current import get_app
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
    "sold": "#8a5f5f",
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

# 한글 IME 가 켜진 채로 단축키를 누르면 같은 자리의 자모가 들어온다.
# q 를 눌러도 터미널에는 "ㅂ" 이 도착하므로 두벌식 자리를 그대로 별칭으로 붙인다.
HANGUL_ALIAS = dict(zip(
    "qwertyuiopasdfghjklzxcvbnm",
    "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ",
))


# IME 가 한글이면 자음 하나는 조합 버퍼에 남아 터미널까지 아예 안 온다.
# 그래서 자모 별칭만으로는 부족하고, IME 를 타지 않는 키를 따로 준다.
# 화살표/Enter/Space/Esc/F키/Ctrl 조합은 조합 대상이 아니라 항상 그대로 도착한다.
FUNCTION_ALIAS = {"e": "f2", "s": "f3", "t": "f4", "r": "f5", "p": "f6",
                  "l": "f7"}


def key_aliases(key):
    """같은 동작에 묶을 키들. 영문 + 같은 자리 한글 자모 + F키."""
    keys = [key]
    alias = HANGUL_ALIAS.get(key)
    if alias:
        keys.append(alias)
    fkey = FUNCTION_ALIAS.get(key)
    if fkey:
        keys.append(fkey)
    return keys

# 화면 폭(칸). 한글은 2칸이라 len() 이 아니라 get_cwidth() 로 센다.
# 고정 폭으로 그리면 터미널이 그보다 좁을 때 줄이 접히고, 접힌 만큼 화면 계산이
# 어긋나 이전 프레임이 지워지지 않고 남는다. 매 렌더마다 실제 폭을 본다.
WIDTH = 72          # 넓은 터미널에서도 이 이상으로는 안 늘린다
MIN_WIDTH = 44


def width():
    try:
        columns = get_app().output.get_size().columns
    except Exception:      # 앱 밖에서 렌더할 때 (테스트 등)
        columns = WIDTH + 1
    return max(MIN_WIDTH, min(columns - 1, WIDTH))


def pad(text, width):
    """표시 폭 기준 왼쪽 정렬."""
    return text + " " * max(0, width - get_cwidth(text))


def rpad(text, width):
    return " " * max(0, width - get_cwidth(text)) + text


def clip(text, width):
    """표시 폭 기준으로 자른다. len() 으로 자르면 한글에서 어긋난다."""
    if get_cwidth(text) <= width:
        return text
    out = ""
    for ch in text:
        if get_cwidth(out + ch) > width:
            break
        out += ch
    return out


def emit_hints(line, hints):
    """[키] 설명 목록을 화면 폭에 맞춰 접어서 출력."""
    limit = width()
    parts, used = [], 0
    for key, desc in hints:
        chunk = [("class:key", f"  [{key}]"), ("class:label", f" {desc}")]
        size = get_cwidth(f"  [{key}] {desc}")
        if parts and used + size > limit:
            line(*parts)
            parts, used = [], 0
        parts += chunk
        used += size
    if parts:
        line(*parts)


def rule(title):
    """구분선. 한글만 2칸이고 나머지는 ASCII 라 어느 터미널에서나 폭이 같다.

    ponytail: 박스 문자(U+2500)나 화살표는 East Asian Ambiguous 라
    한국어 터미널에서 2칸으로 그려져 정렬이 깨진다. 그래서 ASCII 만 쓴다.
    """
    head = f"-- {title} "
    return head + "-" * max(0, width() - get_cwidth(head))


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
        self.rsv_items = []
        self.rsv_cur = 0
        self.pick_title = ""
        self.pick_commit = None

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


def seat_status(train, rail_type):
    """(일반, 특실, 예약대기) 가용 여부. 대기가 없는 열차면 None."""
    if rail_type == "SRT":
        return (train.general_seat_available(),
                train.special_seat_available(),
                train.reserve_standby_available() or None)
    waiting = None
    if getattr(train, "wait_reserve_flag", -1) >= 0:
        waiting = train.has_general_waiting_list()
    return train.has_general_seat(), train.has_special_seat(), waiting


def train_row(state, index, train):
    """한 줄을 조각으로 만든다. 가능/매진에만 색이 붙도록.

    행 전체에 배경색을 깔면 안쪽 색이 묻혀서, 커서는 왼쪽 > 로 표시한다.
    """
    name = getattr(train, "train_type_name", None) or getattr(train, "train_name", "")
    number = getattr(train, "train_no", None) or getattr(train, "train_number", "")
    dep, arr = train.dep_time, train.arr_time
    times = f"{dep[:2]}:{dep[2:4]}~{arr[:2]}:{arr[2:4]}"

    limit = width()
    # 좁으면 열차번호 칸을 버려서 일반/특실 배지 자리를 먼저 확보한다.
    # 시각이 같이 나오므로 번호가 없어도 어느 열차인지 헷갈리지 않는다.
    wide = limit >= 62
    head = " > " if index == state.cursor else "   "
    head += "[*] " if index in state.picked else "[ ] "
    head += pad(clip(str(name), 10), 11)
    if wide:
        head += pad(str(number), 5)
    head += times

    head_col = 36 if wide else 30
    style = "class:value" if index == state.cursor else "class:dim"
    row = [(style, pad(head, head_col))]
    used = max(head_col, get_cwidth(head))
    for label, ok in zip(("일반", "특실", "대기"),
                         seat_status(train, state.rail_type)):
        if ok is None:
            continue
        badge = f"{label} " + ("가능" if ok else "매진")
        if used + get_cwidth(badge) + 2 > limit:
            break          # 좁으면 뒤쪽 배지(대기)부터 버린다
        row.append(("class:label", f"{label} "))
        row.append(("class:ok" if ok else "class:sold",
                    pad("가능" if ok else "매진", 6)))
        used += get_cwidth(badge) + 2
    return row


def render(state):
    out = []

    def line(*parts):
        # 줄 끝까지 공백으로 채운다. 짧게 끝내면 이전 프레임의 글자가 그 자리에
        # 그대로 남는다 (구분선 꼬리가 열차 행 끝에 붙어 보이던 증상).
        out.extend(parts)
        filled = sum(get_cwidth(text) for _, text in parts)
        if filled < width():
            out.append(("", " " * (width() - filled)))
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
    if state.screen == "rsv":
        render_rsv(state, line)
        return out

    def field(label, value, style="class:value"):
        return [("class:label", f"  {label}  "), (style, pad(str(value), 21))]

    def field_row(*pairs):
        """넓으면 두 칸, 좁으면 한 칸씩."""
        if width() >= 62:
            parts = []
            for label, value in pairs:
                parts += field(label, value)
            line(*parts)
        else:
            for label, value in pairs:
                line(*field(label, value))

    who = f"{state.rail_type} | {state.user}" if state.user else state.rail_type
    line(("class:title", " srtgo " + rpad(who + " ", width() - 7)))
    line()

    if not state.ready():
        line(("class:warn", clip("  조건이 비어 있습니다. e 를 눌러 정하세요.",
                                 width())))
        line()
    else:
        field_row(("구간", f"{state.get('departure')} -> {state.get('arrival')}"),
                  ("날짜", _date_label(state)))
        field_row(("시각", f"{state.get('time', '000000')[:2]}시 이후"),
                  ("승객", ", ".join(f"{core.PASSENGER_LABEL.get(k, k)} {v}"
                                     for k, v in state.counts().items())))
        field_row(("좌석", _seat_label(state)),
                  ("결제", "카드 자동" if state.get("pay") == "1" else "안 함"))
        line()

    # 대상 열차
    line(("class:head", rule("대상 열차")))
    if not state.trains:
        line(("class:dim", clip("  Enter 를 누르면 열차를 조회합니다.", width())))
    else:
        for i, train in enumerate(state.trains):
            line(*train_row(state, i, train))
    line()

    # 상태
    line(("class:head", rule("상태")))
    style, label = PHASE_TEXT[state.phase]
    if state.phase in ("running", "paused"):
        elapsed = time.time() - (state.started_at or time.time())
        spin = SPINNER[state.attempts & 3] if state.phase == "running" else " "
        stats = [("class:label", "   시도 "),
                 ("class:value", f"{state.attempts}회"),
                 ("class:label", "   경과 "),
                 ("class:value", _fmt_elapsed(elapsed)),
                 ("class:label", "   간격 "),
                 ("class:value", f"{core.get_interval()}초")]
        header = [("class:label", "  "), (f"class:{style}", f"{label} {spin}")]
        if sum(get_cwidth(t) for _, t in header + stats) <= width():
            line(*(header + stats))
        else:
            line(*header)
            line(*stats)
    else:
        line(("class:label", "  "), (f"class:{style}", label))

    if state.last_msg:
        head = f"  마지막  {state.last_at}  "
        line(("class:label", "  마지막  "), ("class:dim", f"{state.last_at}  "),
             ("class:value", clip(state.last_msg, max(0, width() - len(head)))))
    if state.result:
        for row in state.result.split("\n"):
            line(("class:ok", clip(f"  {row}", width())))
    line()

    # 키 안내
    # 한글 IME 가 켜져 있으면 영문 단축키가 안 먹으므로 F키/Esc 를 같이 보여준다.
    hints = {
        "idle": [("Enter", "조회"), ("e/F2", "조건"), ("l/F7", "내역"),
                 ("s/F3", "설정"), ("t/F4", "전환"), ("Esc", "종료")],
        "picking": [("up/dn", "이동"), ("Space", "선택"), ("Enter", "시작"),
                    ("r/F5", "재조회"), ("Esc", "취소")],
        "running": [("p/F6", "일시정지"), ("Esc", "중지")],
        "paused": [("p/F6", "재개"), ("Esc", "중지")],
        "done": [("Enter", "처음으로"), ("Esc", "처음으로")],
        "error": [("Enter", "다시"), ("s/F3", "설정"), ("Esc", "처음으로")],
    }.get(state.phase, [("Esc", "종료")])
    emit_hints(line, hints)
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


def open_choices(state, title, options, on_choose):
    """Field 와 무관한 단발 선택 목록. 고르면 on_choose(value)."""
    state.pick_field = None
    state.pick_title = title
    state.pick_opts = options
    state.pick_multi = False
    state.pick_sel = set()
    state.pick_cur = 0
    state.pick_commit = on_choose
    state.screen = "pick"


def open_picker(state, field):
    state.pick_field = field
    state.pick_title = field.label
    state.pick_commit = None
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
    if state.pick_commit is not None:
        choose, state.pick_commit = state.pick_commit, None
        choose(state.pick_opts[state.pick_cur][1])
        return
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
    line(("class:title", " " + pad(clip(state.pick_title, width() - 1),
                                   width() - 1)))
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
        line((style, pad(clip(f" {mark} {label}", width()), width())))
    if total > PICK_ROWS:
        line(("class:dim", f"  {state.pick_cur + 1}/{total}"))
    line()
    hints = ([("up/dn", "이동"), ("Space", "선택"), ("Enter", "완료"), ("Esc", "취소")]
             if state.pick_multi else
             [("up/dn", "이동"), ("Enter", "선택"), ("Esc", "취소")])
    emit_hints(line, hints)


def render_form(state, line):
    line(("class:title", " " + pad(state.form_title, width() - 1)))
    line()
    for i, field in enumerate(state.fields):
        style = "class:cursor" if i == state.fcur else ""
        arrow = ">" if i == state.fcur else " "
        row = f" {arrow} {pad(clip(field.label, 21), 21)}{field.display()}"
        line((style, pad(clip(row, width()), width())))
    line()
    field = state.fields[state.fcur] if state.fields else None
    if field and field.note:
        line(("class:dim", clip(f"  {field.note}", width())))
    if state.notice:
        line(("class:warn", clip(f"  {state.notice}", width())))
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
    line(("class:title", " " + pad(field.label, width() - 1)))
    line()
    shown = "*" * len(state.buf) if field.mask else state.buf
    line(("class:label", "  "), ("class:value", pad(shown + "_", width() - 2)))
    line()
    if field.note:
        line(("class:dim", clip(f"  {field.note}", width())))
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


# --------------------------------------------------------------------------
# 예매 내역 (조회 / 결제 / 취소 / 환불)


def item_state(item):
    """(꼬리표, 결제 가능 여부). 발권 끝난 표와 대기표는 결제할 게 없다."""
    if getattr(item, "is_ticket", False):
        return "발권", False
    if getattr(item, "is_waiting", False):
        return "대기", False
    return "미결제", True


def load_reservations(state, app):
    """예약 + 발권 내역을 한 목록으로."""
    if not _ensure_login(state, app):
        return
    _note(state, app, "내역 조회 중", "search")
    try:
        if state.rail_type == "SRT":
            reservations, tickets = state.rail.get_reservations(), []
        else:
            reservations, tickets = state.rail.reservations(), state.rail.tickets()
    except Exception as ex:
        _note(state, app, f"조회 실패: {getattr(ex, 'msg', ex)}", "error")
        return

    items = []
    for ticket in tickets:
        ticket.is_ticket = True
        items.append(ticket)
    for rsv in reservations:
        rsv.is_ticket = bool(getattr(rsv, "paid", False))
        items.append(rsv)

    state.rsv_items = items
    state.rsv_cur = 0
    state.screen = "rsv"
    _note(state, app, f"{len(items)}건" if items else "예매 내역이 없습니다", "idle")
    app.invalidate()


def render_rsv(state, line):
    line(("class:title", " " + pad("예매 내역", width() - 1)))
    line()
    if not state.rsv_items:
        line(("class:dim", "  내역이 없습니다."))
    for i, item in enumerate(state.rsv_items):
        tag, _ = item_state(item)
        head = " > " if i == state.rsv_cur else "   "
        style = "class:value" if i == state.rsv_cur else "class:dim"
        tag_style = {"발권": "class:ok", "대기": "class:warn"}.get(tag, "class:sold")
        # [미결제] 는 8칸, [발권] 은 6칸이라 폭을 맞춰줘야 뒤가 안 밀린다.
        line((style, head), (tag_style, pad(f"[{tag}]", 9)),
             (style, clip(str(item), max(0, width() - 12))))
        for seat in getattr(item, "tickets", None) or []:
            line(("class:dim", clip(f"          {seat}", width())))
    line()
    if state.notice:
        line(("class:warn", clip(f"  {state.notice}", width())))
        line()
    emit_hints(line, [("up/dn", "이동"), ("Enter", "결제/취소"),
                      ("r/F5", "새로고침"), ("g", "텔레그램"), ("Esc", "돌아가기")])


def open_item_actions(state, app):
    if not state.rsv_items:
        return
    item = state.rsv_items[state.rsv_cur]
    tag, payable = item_state(item)

    def run(action):
        def work(_state, _app):
            try:
                if action == "pay":
                    ok = core.pay_card(state.rail, item)
                    _note(state, app, "결제 성공" if ok else "결제 실패")
                elif action == "refund":
                    state.rail.refund(item)
                    _note(state, app, "환불 요청함")
                else:
                    state.rail.cancel(item)
                    _note(state, app, "취소함")
            except Exception as ex:
                _note(state, app, f"실패: {getattr(ex, 'msg', ex)}")
                return
            load_reservations(state, app)
            state.screen = "rsv"
        _spawn(work, state, app)

    options = []
    if payable:
        options.append(("결제하기", "pay"))
    options.append(("환불하기" if tag == "발권" else "취소하기",
                    "refund" if tag == "발권" else "cancel"))
    options.append(("그만두기", None))

    def chosen(action):
        state.screen = "rsv"
        if action:
            run(action)

    open_choices(state, clip(str(item), width() - 2), options, chosen)


def send_to_telegram(state, app):
    if not state.rsv_items:
        return
    out = ["[ 예매 내역 ]"]
    for item in state.rsv_items:
        out.append(f"🚅{item}")
        out += [str(s) for s in getattr(item, "tickets", None) or []]
    try:
        core.asyncio.run(core.get_telegram()("\n".join(out)))
        state.notice = "텔레그램으로 보냈습니다"
    except Exception as ex:
        state.notice = f"텔레그램 실패: {ex}"
    app.invalidate()



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
        elif state.screen == "rsv" and state.rsv_items:
            state.rsv_cur = (state.rsv_cur + delta) % len(state.rsv_items)
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
        if state.screen == "rsv":
            state.screen = "dash"
        elif state.screen == "pick" and state.pick_field is None:
            state.screen = "rsv" if state.rsv_items else "dash"
            state.pick_commit = None
        elif state.screen in ("edit", "pick"):
            state.screen = "form"
        elif state.screen == "form":
            state.screen = "dash"
            state.fields = []
        else:
            on_back(event)

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
        if state.screen == "rsv":
            open_item_actions(state, app)
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
        def on_key(event):
            if state.screen == "dash":
                handler(event)
        for alias in key_aliases(key):
            kb.add(alias, filter=not_typing)(on_key)

    def on_q(event):
        """대시보드에서는 한 단계씩 물러난다. 더 물러날 데가 없을 때만 종료."""
        if state.phase in ("running", "paused"):
            state.stop.set()
            state.pause.clear()
            state.stop = threading.Event()
            _note(state, app, "중지됨", "idle")
        elif state.phase in ("picking", "done", "error", "search", "login"):
            state.trains = []
            state.picked = set()
            state.cursor = 0
            state.result = ""
            _note(state, app, "", "idle")
        else:
            state.stop.set()
            event.app.exit()

    def on_back(event):
        if state.screen == "edit":
            state.screen = "form"  # c-c 는 입력 취소
            return
        if state.screen == "rsv":
            state.screen = "dash"
            return
        if state.screen == "pick" and state.pick_field is None:
            state.pick_commit = None
            state.screen = "rsv" if state.rsv_items else "dash"
            return
        if state.screen in ("form", "pick"):
            state.screen = "form" if state.screen == "pick" else "dash"
            return
        on_q(event)

    for alias in key_aliases("q"):
        kb.add(alias, filter=not_typing)(on_back)
    kb.add("c-c")(on_back)
    # escape 는 위쪽에서 form/pick/edit 을 처리한다. 대시보드에서만 q 와 같게.

    def on_r(event):
        if state.phase in ("picking", "idle", "error"):
            _spawn(do_search, state, app)

    for alias in key_aliases("r"):
        @kb.add(alias, filter=not_typing)
        def _(event, _alias=alias):
            if state.screen == "rsv":
                _spawn(load_reservations, state, app)
            elif state.screen == "dash":
                on_r(event)

    for alias in key_aliases("g"):
        @kb.add(alias, filter=not_typing)
        def _(event, _alias=alias):
            if state.screen == "rsv":
                send_to_telegram(state, app)

    def on_l(event):
        if state.phase in ("running", "paused"):
            return
        _spawn(load_reservations, state, app)

    shortcut("l", on_l)

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
