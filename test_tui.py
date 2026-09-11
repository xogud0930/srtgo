"""TUI 자체 점검. 네트워크도 계정도 안 쓴다.

    python test_tui.py

실제 keyring 은 건드리지 않는다. 사용자의 계정/카드 설정을 덮어쓰지 않도록
메모리 저장소로 갈아끼운 뒤 돌린다.
"""

import threading
import time

from prompt_toolkit.application import create_app_session
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from srtgo import tui
from srtgo import srtgo as core


class FakeKeyring:
    """keyring 대역. 테스트가 실제 자격 증명 저장소를 못 건드리게 한다."""

    def __init__(self, seed=None):
        self.data = dict(seed or {})

    def get_password(self, service, key):
        return self.data.get((service, key))

    def set_password(self, service, key, value):
        self.data[(service, key)] = value

    def delete_password(self, service, key):
        self.data.pop((service, key), None)


ENTER = chr(13)
ESC = chr(27)
F2 = ESC + 'OQ'
F3 = ESC + 'OR'

SEED = {
    ("KTX", "departure"): "포항",
    ("KTX", "arrival"): "영덕",
    ("KTX", "date"): "20260925",
    ("KTX", "time"): "090000",
    ("KTX", "adult"): "1",
    ("KTX", "seat_type"): "SPECIAL_ONLY",
    ("KTX", "pay"): "1",
    ("KTX", "station"): "포항,영덕,영해,서울",
}


def fake_keyring(seed=None):
    fake = FakeKeyring(seed if seed is not None else SEED)
    core.keyring = fake
    return fake


class FakeTrain:
    """srtgo.ktx.Train 에서 화면이 실제로 읽는 것만 흉내낸다."""

    def __init__(self, name="KTX-이음", no="751", dep="092000", arr="100500",
                 general=True, special=False, waiting=None):
        self.train_type_name, self.train_no = name, no
        self.dep_time, self.arr_time = dep, arr
        self._general, self._special = general, special
        self.wait_reserve_flag = -1 if waiting is None else (9 if waiting else 0)

    def has_general_seat(self):
        return self._general

    def has_special_seat(self):
        return self._special

    def has_general_waiting_list(self):
        return self.wait_reserve_flag == 9

    def __str__(self):
        return f"[{self.train_type_name} {self.train_no}]"


def widest(state):
    text = "".join(t for _, t in to_formatted_text(tui.render(state)))
    return max(get_cwidth(line) for line in text.split("\n"))


def test_every_screen_fits_width():
    """대시보드/조건/설정/목록/입력 어디서도 줄이 화면 폭을 넘지 않아야 한다."""
    fake_keyring()
    state = tui.State("KTX")
    state.user = "홍길동"

    for phase in tui.PHASE_TEXT:
        state.phase = phase
        state.trains = [] if phase in ("idle", "login") else [FakeTrain()]
        state.picked = {0} if state.trains else set()
        state.started_at = time.time() - 61
        state.last_msg = "매진"
        state.result = "예매 성공\n7호차 3A" if phase == "done" else ""
        assert widest(state) <= tui.WIDTH + 2, f"{phase} 화면이 폭을 넘음"

    tui.open_form(state, "예매 조건", tui.condition_fields(state))
    assert widest(state) <= tui.WIDTH + 2, "조건 화면이 폭을 넘음"

    tui.open_form(state, "설정", tui.setting_fields(state))
    for i in range(len(state.fields)):
        state.fcur = i
        assert widest(state) <= tui.WIDTH + 2, f"설정 {i}번째 항목이 폭을 넘음"

    tui.open_picker(state, state.fields[6])   # 역 목록 (45개, 스크롤)
    assert widest(state) <= tui.WIDTH + 2, "목록 화면이 폭을 넘음"

    state.screen = "edit"
    state.fcur = 0
    state.buf = "x" * 40
    assert widest(state) <= tui.WIDTH + 2, "입력 화면이 폭을 넘음"


def test_availability_is_spelled_out_per_row():
    """행마다 일반/특실 가능·매진이 보이고, 대기는 있을 때만 나와야 한다."""
    fake_keyring()
    state = tui.State("KTX")
    state.phase = "picking"
    state.trains = [
        FakeTrain(general=True, special=False),
        FakeTrain(name="ITX-마음", no="1801", general=False, special=False,
                  waiting=True),
    ]

    rows = [tui.train_row(state, i, t) for i, t in enumerate(state.trains)]
    first = "".join(text for _, text in rows[0])
    second = "".join(text for _, text in rows[1])

    assert "일반 가능" in first and "특실 매진" in first, first
    assert "대기" not in first, "대기가 없는 열차인데 표시됨"
    assert "대기 가능" in second, second

    # 가능/매진이 서로 다른 스타일이어야 눈에 띈다
    styles = {text.strip(): style for style, text in rows[0]}
    assert styles["가능"] != styles["매진"], styles


def test_narrow_terminal_does_not_overflow():
    """터미널이 좁으면 줄이 접히고, 접힌 만큼 화면 계산이 어긋나 잔상이 남는다.

    어떤 폭에서도 줄이 그 폭을 넘지 않아야 한다.
    """
    fake_keyring()
    original = tui.width
    try:
        for columns in (44, 52, 62, 72):
            tui.width = lambda c=columns: c
            state = tui.State("KTX")
            state.user = "홍길동"
            state.phase = "picking"
            state.trains = [
                FakeTrain(general=True, special=False, waiting=True),
                FakeTrain(name="ITX-새마을", no="1021", general=False,
                          special=True, waiting=False),
            ]
            state.picked = {0}
            assert widest(state) <= columns, f"{columns}칸에서 넘침"

            tui.open_form(state, "설정", tui.setting_fields(state))
            for i in range(len(state.fields)):
                state.fcur = i
                assert widest(state) <= columns, f"{columns}칸 설정 {i}번째"

            tui.open_picker(state, state.fields[6])
            assert widest(state) <= columns, f"{columns}칸 목록"
    finally:
        tui.width = original


def test_every_line_fills_the_width():
    """줄이 짧게 끝나면 그 자리에 이전 프레임 글자가 남는다.

    구분선 꼬리가 열차 행 끝에 붙어 보이던 잔상의 원인이라, 모든 줄이
    화면 폭을 정확히 채워야 한다.
    """
    fake_keyring()
    original = tui.width
    try:
        for columns in (50, 62, 72):
            tui.width = lambda c=columns: c
            state = tui.State("KTX")
            state.user = "홍길동"
            for phase in ("idle", "picking", "running", "done"):
                state.phase = phase
                state.trains = [] if phase == "idle" else [
                    FakeTrain(general=True, special=False, waiting=True)]
                state.picked = {0} if state.trains else set()
                state.last_msg = "매진"
                state.result = "예매 성공" if phase == "done" else ""
                text = "".join(t for _, t in to_formatted_text(tui.render(state)))
                widths = {get_cwidth(line)
                          for line in text.split(chr(10))[:-1]}
                assert widths == {columns}, f"{columns}칸 {phase}: {sorted(widths)}"
    finally:
        tui.width = original


def test_card_number_is_masked():
    """설정 화면에 카드번호가 그대로 보이면 안 된다."""
    fake = fake_keyring()
    fake.set_password("card", "number", "4074073001796093")
    state = tui.State("KTX")
    number = next(f for f in tui.setting_fields(state)
                  if f.service == "card" and f.key == "number")
    shown = number.display()
    assert "4074073001" not in shown, f"카드번호가 노출됨: {shown}"
    assert shown.endswith("6093"), f"뒷자리는 보여야 함: {shown}"


def test_field_edits_persist():
    """좌우키 조절과 목록 선택이 저장소까지 반영되는지."""
    fake = fake_keyring()
    state = tui.State("KTX")
    fields = tui.condition_fields(state)

    pay = next(f for f in fields if f.key == "pay")
    assert pay.raw() == "1"
    pay.nudge(1)
    assert fake.get_password("KTX", "pay") == "0", "토글이 저장 안 됨"

    interval = next(f for f in tui.setting_fields(state) if f.key == "interval")
    interval.save("3")
    interval.nudge(-1)
    assert fake.get_password("SRT", "interval") == "2.5", "증감이 저장 안 됨"
    for _ in range(20):
        interval.nudge(-1)
    assert float(interval.raw()) >= tui.core.RESERVE_INTERVAL_RANGE[0], "하한을 넘음"

    state.fields = fields
    departure = next(f for f in fields if f.key == "departure")
    tui.open_picker(state, departure)
    state.pick_cur = 2                      # 저장된 역 목록의 3번째
    tui.commit_picker(state)
    assert fake.get_password("KTX", "departure") == "영해"
    assert state.screen == "form"


def test_shrinking_station_list_fixes_route():
    """역 목록에서 빼버린 역이 출발역으로 남아 있으면 안 된다."""
    fake = fake_keyring()
    state = tui.State("KTX")
    station = next(f for f in tui.setting_fields(state) if f.key == "station")
    station.save("서울,부산")             # 포항/영덕을 목록에서 제거
    tui.after_save(state, station)
    assert fake.get_password("KTX", "departure") == "서울"
    assert fake.get_password("KTX", "arrival") == "서울"


def test_keys_move_state_and_quit():
    """space 로 고르고 q 로 빠져나오는 것까지."""
    fake_keyring()
    state = tui.State("KTX")
    state.phase = "picking"
    state.trains = [FakeTrain(), FakeTrain()]

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            app = tui.build_app(state)

            def drive():
                time.sleep(0.2)
                pipe.send_text(" ")       # 0번 선택
                time.sleep(0.2)
                pipe.send_text("\x1b[B")  # 아래로
                time.sleep(0.2)
                pipe.send_text(" ")       # 1번 선택
                time.sleep(0.2)
                state.phase = "idle"      # 예매 루프는 돌리지 않고 종료 경로만
                pipe.send_text("q")

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    assert state.picked == {0, 1}, f"선택 상태가 이상함: {state.picked}"
    assert state.cursor == 1


def test_hangul_keys_work_as_shortcuts():
    """IME 가 켜져 있어도 같은 자리 키가 먹혀야 한다. q=ㅂ, e=ㄷ."""
    fake_keyring()
    state = tui.State("KTX")

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            app = tui.build_app(state)
            seen = {}

            def drive():
                time.sleep(0.2)
                pipe.send_text("ㄷ")          # e: 조건 화면 열기
                time.sleep(0.3)
                seen["after_d"] = state.screen
                pipe.send_text("ㅂ")          # q: 뒤로
                time.sleep(0.3)
                seen["after_b"] = state.screen
                pipe.send_text("ㅂ")          # q: 종료

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    assert seen["after_d"] == "form", f"ㄷ 로 조건 화면이 안 열림: {seen}"
    assert seen["after_b"] == "dash", f"ㅂ 로 뒤로가기가 안 됨: {seen}"


def test_cancel_backs_out_instead_of_quitting():
    """조회 후 취소하면 프로그램이 끝나는 게 아니라 대시보드로 돌아와야 한다."""
    fake_keyring()
    state = tui.State("KTX")
    state.phase = "picking"
    state.trains = [FakeTrain(), FakeTrain()]
    state.picked = {0}

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            app = tui.build_app(state)
            seen = {}

            def drive():
                time.sleep(0.2)
                pipe.send_text("q")           # 취소
                time.sleep(0.3)
                seen["after_cancel"] = (state.phase, len(state.trains), app.is_running)
                pipe.send_text("q")           # 이제서야 종료

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    phase, n_trains, alive = seen["after_cancel"]
    assert alive, "취소만 했는데 앱이 죽었다"
    assert phase == "idle", f"대시보드로 안 돌아옴: {phase}"
    assert n_trains == 0, "취소했는데 열차 목록이 남아 있다"
    assert state.picked == set()


def test_function_keys_bypass_ime():
    """IME 가 자모를 물고 있어도 F키/Esc 는 그대로 도착해야 한다."""
    fake_keyring()
    state = tui.State("KTX")

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            app = tui.build_app(state)
            seen = {}

            def drive():
                time.sleep(0.2)
                pipe.send_text(F2)            # 조건 화면
                time.sleep(0.3)
                seen["f2"] = (state.screen, state.form_title)
                pipe.send_text(ESC)           # 뒤로
                time.sleep(0.3)
                pipe.send_text(F3)            # 설정 화면
                time.sleep(0.3)
                seen["f3"] = (state.screen, state.form_title)
                pipe.send_text(ESC)           # 뒤로
                time.sleep(0.2)
                # 단독 ESC 는 이스케이프 시퀀스 시작일 수도 있어서 파서가 붙들고
                # 있다가 다음 입력이 와야 확정한다. 아무 키나 보내 흘려보낸다.
                pipe.send_text("z")           # 바인딩 없는 키
                time.sleep(0.3)
                seen["back"] = state.screen
                pipe.send_text("q")           # 종료

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    assert seen["f2"] == ("form", "예매 조건"), f"F2 가 조건을 못 엶: {seen}"
    assert seen["f3"] == ("form", "설정"), f"F3 가 설정을 못 엶: {seen}"
    assert seen["back"] == "dash", f"Esc 뒤로가기 실패: {seen}"


def test_hangul_typing_still_reaches_buffer():
    """입력 화면에서는 한글 자모도 단축키가 아니라 글자로 들어가야 한다."""
    fake_keyring()
    state = tui.State("KTX")
    state.fields = tui.setting_fields(state)
    state.fcur = 10               # 텔레그램 chat_id (마스킹 없는 text)
    state.screen = "edit"
    state.buf = ""

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            app = tui.build_app(state)

            def drive():
                time.sleep(0.2)
                pipe.send_text("ㅂㄷ가")
                time.sleep(0.3)
                pipe.send_text(ENTER)
                time.sleep(0.2)
                state.screen = "dash"
                pipe.send_text("q")

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    assert (tui.core.keyring.get_password("telegram", "chat_id")
            == "ㅂㄷ가"), "입력 화면에서 한글이 단축키로 새어나감"


def test_typing_goes_to_buffer_not_shortcuts():
    """입력 화면에서 s, q, e 를 치면 단축키가 아니라 글자로 들어가야 한다."""
    fake_keyring()
    state = tui.State("KTX")
    state.fields = tui.setting_fields(state)
    state.fcur = 0
    state.screen = "edit"
    state.buf = ""

    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            app = tui.build_app(state)

            def drive():
                time.sleep(0.2)
                pipe.send_text("seq")     # 전부 단축키로 쓰이는 글자
                time.sleep(0.3)
                pipe.send_text("\r")      # 저장하고 form 으로
                time.sleep(0.2)
                state.screen = "dash"
                pipe.send_text("q")

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    assert state.screen == "dash"
    assert tui.core.keyring.get_password("KTX", "id") == "seq", \
        "입력 화면에서 글자가 단축키로 새어나감"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"OK  {name}")
