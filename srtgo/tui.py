"""전체화면 대시보드 UI.

조건, 대상 열차, 대기 상태를 한 화면에 두고 Enter 로 굴린다.
예매 로직은 srtgo.py 것을 그대로 쓰고, 설정 입력도 기존 inquirer 함수를
run_in_terminal 으로 잠깐 내려가서 재사용한다. 여기서 새로 만드는 건 화면뿐.
"""

import threading
import time
from datetime import datetime
from random import gammavariate

from prompt_toolkit.application import Application, run_in_terminal
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

    @kb.add("q")
    @kb.add("c-c")
    def _(event):
        if state.phase in ("running", "paused"):
            state.stop.set()
            state.pause.clear()
            _note(state, app, "중지됨", "idle")
            state.stop = threading.Event()
        else:
            state.stop.set()
            event.app.exit()

    @kb.add("enter")
    def _(event):
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

    @kb.add("r")
    def _(event):
        if state.phase in ("picking", "idle", "error"):
            _spawn(do_search, state, app)

    @kb.add("up")
    def _(event):
        if state.trains:
            state.cursor = (state.cursor - 1) % len(state.trains)

    @kb.add("down")
    def _(event):
        if state.trains:
            state.cursor = (state.cursor + 1) % len(state.trains)

    @kb.add("space")
    def _(event):
        if state.phase == "picking" and state.trains:
            state.picked ^= {state.cursor}

    @kb.add("p")
    def _(event):
        if state.phase == "running":
            state.pause.set()
        elif state.phase == "paused":
            state.pause.clear()

    @kb.add("e")
    def _(event):
        if state.phase in ("running", "paused"):
            return
        run_in_terminal(lambda: core.edit_conditions(state.rail_type))

    @kb.add("t")
    def _(event):
        """SRT <-> KTX 전환."""
        if state.phase in ("running", "paused"):
            return
        state.rail_type = "SRT" if state.rail_type == "KTX" else "KTX"
        core.keyring.set_password("srtgo", "rail_type", state.rail_type)
        state.rail = None
        state.user = ""
        state.trains = []
        state.picked = set()
        _note(state, app, f"{state.rail_type} 로 전환", "idle")

    @kb.add("s")
    def _(event):
        if state.phase in ("running", "paused"):
            return

        def go():
            changed = core.settings_menu(state.rail_type, state.debug)
            if changed == "login":
                state.rail = None
                state.user = ""
        run_in_terminal(go)

    threading.Thread(target=_tick, args=(app, state), daemon=True).start()
    return app


def run(rail_type="KTX", debug=False):
    build_app(State(rail_type, debug)).run()
