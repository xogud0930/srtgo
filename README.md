# SRTgo: K-Train (KTX, SRT) Reservation Assistant
[![Upload Python Package](https://github.com/lapis42/srtgo/actions/workflows/python-publish.yml/badge.svg)](https://github.com/lapis42/srtgo/actions/workflows/python-publish.yml)
[![Downloads](https://static.pepy.tech/badge/srtgo)](https://pepy.tech/project/srtgo)
[![Downloads](https://static.pepy.tech/badge/srtgo/month)](https://pepy.tech/project/srtgo)
[![Python version](https://img.shields.io/pypi/pyversions/srtgo)](https://pypistats.org/packages/srtgo)

> [!NOTE]
> 공정한 예매 문화 조성을 위해 본 프로젝트의 개발 및 지원을 중단하기로 결정했습니다. 양해 부탁드립니다.

> [!WARNING]
> 본 프로그램의 모든 상업적, 영리적 이용을 엄격히 금지합니다. 본 프로그램 사용에 따른 민형사상 책임을 포함한 모든 책임은 사용자에게 있으며, 본 프로그램의 개발자는 민형사상 책임을 포함한 어떠한 책임도 부담하지 않습니다. 본 프로그램을 내려받음으로써 모든 사용자는 위 사항에 이의 없이 동의하는 것으로 간주됩니다.

---
> [!NOTE]
> I have decided to discontinue the development and support for this project. Thank you for your understanding.

> [!WARNING]
> All commercial and profit-making use of this program is strictly prohibited. Use of this program is at your own risk, and the developers of this program shall not be liable for any liability, including civil or criminal liability. By downloading this program, all users are deemed to agree to the above terms without any objection.

## 이 포크에서 변경한 것

코레일(KTX) API가 막혀서 동작하지 않던 것을 되살린 포크입니다. SRT 쪽은 원본 그대로입니다.

### 증상

`smart.letskorail.com`으로 보내는 모든 요청이 아래 응답으로 차단됩니다. 로그인부터 실패합니다.

```json
{"strResult": "FAIL", "h_msg_cd": "MACRO ERROR",
 "h_msg_txt": "원활한 서비스 이용을 위해 앱을 최신 버전으로 업데이트한 뒤 재실행 후 안정적인 환경에서 사용해 주시기 바랍니다."}
```

### 원인

에러 문구와 달리 **앱 버전(`Version`) 값 문제가 아닙니다.** 코레일이 요청에 서명 토큰을 요구하도록 바꿨고, `x-dynapath-m-token` 헤더와 `Sid` 파라미터가 없으면 무조건 차단됩니다.

같은 길을 다시 걷지 않도록, 확인해 본 것과 결과를 적어둡니다.

| 시도 | 결과 |
| --- | --- |
| 앱 버전 8종 (`190617001` ~ `260523001`) | 전부 동일하게 MACRO ERROR |
| curl_cffi TLS 위장 (`chrome131_android`) | 실패 (원본에 이미 적용돼 있었음) |
| 평범한 requests / okhttp UA / gzip 헤더 제거 | 전부 실패 |
| `txtDeviceId`, `Sid` 등 파라미터 추가 | 전부 실패 |
| `code.do` (로그인 전 암호화 키 조회) | **성공** — 서버는 살아 있음 |
| **Dynapath 토큰 적용** | **성공** |

### 해결

[jinizest/SuperK](https://github.com/jinizest/SuperK)의 `DynaPathMasterEngine`을 `srtgo/ktx.py`에 이식했습니다. 원본과 클래스·메서드·시그니처가 모두 같아서 `srtgo.py`는 손대지 않아도 그대로 동작합니다.

```
이전:  MACRO ERROR  원활한 서비스 이용을 위해 앱을 최신 버전으로...
이후:  WRT200320    잘못 입력하셨습니다(회원번호)   ← 서버가 정상 처리
```

로그인, 조회, 예약, 카드결제까지 실계정으로 확인했습니다.

### 화면

`srtgo` 를 치면 전체화면 대시보드가 뜬다. 조건이 화면에 떠 있어서, 같은 구간을
다시 잡을 때는 `Enter` 만 누르면 된다. 예전 메뉴는 `srtgo --classic`.

```
 srtgo                                           KTX | 홍길동

  구간  포항 -> 영덕           날짜  09/25 금
  시각  09시 이후              승객  어른 1
  좌석  특실만                 결제  카드 자동

-- 대상 열차 -------------------------------------------------
 [*] [KTX-이음 751] 09/25 09:20~10:05 포항~영덕 특실 예약가능
 [ ] [KTX-이음 753] 09/25 15:28~16:13 포항~영덕 특실 예약가능

-- 상태 ------------------------------------------------------
  예매 대기 중 -   시도 342회   경과 00:17:04   간격 3.0초
  마지막  15:42:09  매진

  [p] 일시정지  [q] 중지
```

| 키 | 하는 일 |
| --- | --- |
| `Enter` | 조회 → 열차 고르고 다시 `Enter` 로 예매 대기 시작 |
| `Space` | 대상 열차 선택/해제 (여러 편 동시 가능) |
| `e` `F2` | 조건 — 구간, 날짜, 시각, 승객, 좌석 종류, 카드 결제 |
| `s` `F3` | 설정 — 로그인, 카드, 역 목록, 예매 옵션, 조회 간격, 텔레그램 |
| `t` `F4` | SRT ↔ KTX |
| `r` `F5` | 다시 조회 |
| `p` `F6` | 일시정지 / 재개 |
| `q` `Esc` | 한 단계 뒤로. 대시보드에서 아무것도 없을 때만 종료 |

`q` / `Esc` 는 되돌리기다. 예매 대기 중이면 중지, 열차를 고르는 중이면 취소,
결과 화면이면 닫기, 그러고 나서 한 번 더 누르면 종료된다. 조회했다가 마음이
바뀌어도 프로그램이 통째로 꺼지지 않는다.

**한글 IME 를 켜둔 채로 쓴다면 F키와 `Esc` 를 쓰는 게 확실하다.** 한글 입력기는
자음 하나를 조합 버퍼에 물고 있다가 다음 글자가 와야 흘려보내기 때문에, `q` 를
눌러도 터미널까지 아예 도착하지 않는 경우가 있다. 화살표, `Enter`, `Space`,
`Esc`, F키, `Ctrl` 조합은 조합 대상이 아니라 IME 상태와 무관하게 항상 먹는다.

자모 자리도 별칭으로 걸어두긴 했다 (`ㅂ`=q, `ㄷ`=e, `ㄴ`=s, `ㅅ`=t, `ㄱ`=r,
`ㅔ`=p). IME 가 자모를 바로 흘려보내는 환경이라면 이쪽도 동작한다.

조건·설정 화면에서는 `좌우` 로 값을 바로 바꾸고, 역이나 날짜처럼 목록이 긴 항목은
`Enter` 로 목록을 연다. 설정은 `--classic` 메뉴와 같은 keyring 키를 쓴다.

### 함께 바뀐 것

- **조회 간격 3초** (기존 1.25초, 분당 48회). 코레일 매크로 탐지가 적극적이라 늦췄습니다. 취소표 경쟁이 심하면 `SRTGO_INTERVAL=1.5 srtgo`로 조절하세요.
- **apkpure 버전 스크래핑 제거.** 로그인마다 외부요청 2건인데 결과는 0개였고, `\d{9}` 정규식이라 페이지의 아무 9자리 숫자나 후보 맨 앞에 꽂히는 구조였습니다. 버전 고정이 필요하면 `KORAIL_VERSION` 환경변수를 쓰세요.
- **전체화면 TUI.** 위 화면이 기본이고, 예전 inquirer 메뉴는 `--classic`.
- **`set_login` 예외 처리.** `SRTError`만 잡고 있어서 코레일 로그인 실패가 크래시로 이어지던 것을 고쳤습니다.

### 다시 막힌다면

`srtgo/ktx.py`의 `DynaPathMasterEngine` 상수(`table`, `i8` / `i9` / `i10`, `AS_VALUE`)가 갱신 대상입니다. 코레일이 서명 방식을 바꾸면 이 값들이 달라집니다.

## Acknowledgments
- This project includes code from [SRT](https://github.com/ryanking13/SRT) by ryanking13, licensed under the MIT License, and [korail2](https://github.com/carpedm20/korail2) by carpedm20, licensed under the BSD License.
- Dynapath bypass ported from [SuperK](https://github.com/jinizest/SuperK) by jinizest.
