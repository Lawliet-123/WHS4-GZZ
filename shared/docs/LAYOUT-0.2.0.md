# Shared 0.2.0 업데이트·경로 안내

이번 파일명은 `GZZ-Shared-0.2.0-repo-layout.zip`이다. 압축 내부 최상위는 `shared/` 하나다. 구형 0.2.0 ZIP과 라이브러리 버전·런타임 코드는 같고 배치와 문서·테스트 import만 정리했다.

## 0.1.0에서 바뀐 기능

- 일시 실패를 횟수 제한 없이 재시도하는 `persistent` 옵션 추가. 기본은 기존 `bounded`.
- persistent 모드에서 새 Event가 들어와도 sender의 재시도 대기시간을 우회하지 않음.
- 대기시각을 outbox에 보관해 프로그램 재실행 후에도 유지.
- 직접 발생한 TLS 인증서 검증 오류도 영구 거절로 처리.
- `evidence.status`, 관측 회차·구간, SelfDefense 운영 보고의 의미를 문서화. 선택 필드의 값 목록을 강제하거나 자동 삽입하지 않음.

기존 공개 함수·7필드 Event·HTTP v1·DB 테이블 버전은 유지한다. persistent로 바꿔도 기존 failed는 자동 재개하지 않는다. 인증·형식·인증서 오류는 해당 Event의 재시도를 중단하며, 큐 용량 제한도 유지된다. 상세 설정은 [README](../README.md)를 참고한다.

## 업로드 순서

1. 팀 레포의 기존 변경 사항을 확인하고, 작업 중인 모듈·서버는 업데이트 전에 정상 종료한다. 저장 파일과 설정은 보존한다.
2. ZIP을 레포 밖의 별도 폴더에 푼다. 나온 `shared/`를 레포 최상위의 `shared/`와 비교해 반영한다. `shared/shared/` 또는 `shared/GZZ-Shared-.../`를 만들지 않는다.
3. 아래 표의 구형 shared 전용 문서·예제·테스트는 새 위치로 반영한 뒤 이전 경로에서 정리한다. 팀원이 수정한 내용은 비교 후 병합한다.
4. 팀의 CI·실행 안내가 구형 경로를 사용하면 아래 새 명령으로 바꾼다.
5. 레포 루트에서 import 위치·버전, 테스트 48개, 로컬 데모를 확인한다.

| 이전 경로 | 새 위치·처리 |
| --- | --- |
| `shared/*.py` | 동일한 `shared/*.py`를 0.2.0으로 업데이트 |
| `shared/README.md` | 사용법·배치·문서 안내를 합친 새 README |
| `docs/shared/*.md` | `shared/docs/*.md` |
| `examples/shared_demo.py` | `shared/examples/shared_demo.py` |
| shared 배포본의 `examples/__init__.py` | `shared/examples/__init__.py`; 기존 파일을 다른 예제가 사용하면 보존 |
| `tests/test_shared.py` | `shared/tests/test_shared.py` |
| `tests/test_shared_updates.py` (기존 0.2.0인 경우) | `shared/tests/test_shared_updates.py` |
| `SHARED_README.md` | 내용을 `shared/README.md`에 통합; 다른 문서의 링크를 고친 뒤 구형 안내 정리 |
| 프로젝트 최상위 `README.md` | 프로젝트 전체 문서로 유지. 이 ZIP은 최상위 README를 포함하지 않음 |

`docs/`, `examples/`, `tests/` 전체를 삭제하지 않는다. 구형 shared 파일만 정리하고 다른 담당자 파일은 보존한다. 구형 테스트를 양쪽에 남기면 전체 테스트 탐색에서 중복 실행될 수 있다. 레포 밖 경로에 설치된 shared가 먼저 import되지 않는지도 확인한다.

기존 outbox DB·writer ledger·JSONL·환경 설정·접속 키는 교체 대상이 아니다. ZIP에 포함하지 않았다. 같은 서버 endpoint와 기존 outbox 경로를 유지해야 미전송 자료를 이어받는다. 경로 정리와 함께 서버 주소를 바꾸는 작업은 별도로 검토한다.

## 새 실행 명령

`shared/`가 보이는 레포 루트에서 실행한다.

```powershell
py -c "import shared; print(shared.__file__, shared.__version__)"
py -m unittest discover -s shared/tests -t . -p "test_shared*.py" -v
py -m shared.examples.shared_demo
```

기존 `py -m examples.shared_demo`는 새 경로에서 사용하지 않는다. shared 전용 테스트를 실행할 때는 `-s shared/tests`를 지정한다. 프로젝트 전체 테스트·다른 모듈 테스트 실행 방식은 각 담당자와 맞춘다.

`from shared.logger import ...`, `from shared.config import ...`는 변경하지 않는다. 런처가 자식 프로세스를 실행할 때 shared 부모인 레포 루트를 import 경로에 제공하는 기존 조건도 같다. 문서·예제·테스트는 개발 자료이며 실제 배포에 사용할 런타임 파일은 `shared/` 바로 아래의 모든 Python 구현 파일이다.

## 범위

이 ZIP에는 SelfDefense 본체·하트비트·FastAPI receiver·scoring·게임/치트 실행 파일이 없다. 관련 설명은 연동 계약 안내다. 실제 중앙 HTTPS 수신, Dashboard 연결, 다른 Python 버전의 실행 검증은 별도다. 이번 ZIP 생성 작업은 GitHub 파일 삭제·업로드·PR 생성을 수행하지 않는다.
