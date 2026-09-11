"""TUI 자체 점검. 네트워크도 계정도 안 쓴다.

    python test_tui.py

렌더링이 폭을 넘지 않는지, 키 입력으로 상태가 옮겨가고 q 로 빠져나오는지만 본다.
"""

import threading
import time

from prompt_toolkit.application import create_app_session
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from srtgo import tui


class FakeTrain:
    def __str__(self):
        return "[KTX-이음 751] 09/25 09:20~10:05 포항~영덕 특실 예약가능"


def test_render_fits_width():
    """어느 단계에서도 줄이 화면 폭을 크게 넘지 않아야 한다."""
    state = tui.State("KTX")
    state.user = "홍길동"
    for phase in tui.PHASE_TEXT:
        state.phase = phase
        state.trains = [] if phase in ("idle", "login") else [FakeTrain()]
        state.picked = {0} if state.trains else set()
        state.started_at = time.time() - 61
        state.last_msg = "매진"
        state.result = "예매 성공\n7호차 3A" if phase == "done" else ""
        text = "".join(t for _, t in to_formatted_text(tui.render(state)))
        widest = max(get_cwidth(line) for line in text.split("\n"))
        assert widest <= tui.WIDTH + 2, f"{phase}: {widest}칸으로 폭 초과"


def test_keys_move_state_and_quit():
    """space 로 고르고 q 로 빠져나오는 것까지."""
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
                state.phase = "idle"      # 예매 루프를 돌리지 않고 종료 경로만 확인
                pipe.send_text("q")

            threading.Thread(target=drive, daemon=True).start()
            app.run()

    assert state.picked == {0, 1}, f"선택 상태가 이상함: {state.picked}"
    assert state.cursor == 1


if __name__ == "__main__":
    test_render_fits_width()
    print("렌더링 폭 OK")
    test_keys_move_state_and_quit()
    print("키 입력/종료 OK")
