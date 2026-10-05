# 0.3.0-testfix1

2026-10-06. 운영 버전은 0.3.0이며 main.py, 관찰·재시작 정책, 이벤트 형식은 바꾸지 않았다.

## 수정 이유

Windows venv에서 Popen.pid와 실제 Python worker의 os.getpid()가 다를 수 있다. 기존 시험 부모는 Popen PID로 release 파일을 만들었고 worker는 자신의 PID로 찾았다. 서로 다른 파일을 기다려 ONESHOT 종료 대기에서 5초 timeout이 발생했다. 실제 탐지기의 종료 코드 판정 오류가 아니라 시험 도구의 제어 경로 문제였다.

시험 worker가 실행별 control_id와 PID·생성 시각, 부모 PID·생성 시각을 ready 기록에 남기도록 바꿨다. 부모는 자신이 시작한 프로세스와 일치하는 ready 기록을 확인한 뒤 control_id 경로로 종료 코드를 전달한다. PID 재사용과 이전 실행 기록을 구분한다. 5초 제한은 늘리지 않았다.

시험 종료 시 실제 worker가 남는지도 확인한다. 남아 있으면 시험은 실패로 기록하고, 임시 시험 폴더의 PID·생성 시각이 일치하는 worker만 정리한다. 이 정리는 운영 워치독 기능이 아니다.

## 재검사

레포 루트에서 실행한다. 일반 Python과 실제 사용할 venv Python으로 각각 실행하되 결과 파일명을 다르게 지정한다. 테스트는 새로운 임시 등록부와 자체 생성 프로세스만 사용한다.

```powershell
python -m unittest discover -s client/SelfDefense/watchdog/tests -p 'test*.py' -v
python client/SelfDefense/watchdog/tests/launcher_integration.py --launcher-dir client/Launcher --shared-root . --reference-commit 31dc8332a770f51f2f50856637dcf8a1a4cee102 --report watchdog-integration-new.json
```

위 커밋은 해당 사본을 사용할 때만 적는다. 다른 코드면 실제 확인한 커밋을 지정한다. 시험 결과에는 사용한 Launcher·shared 해시와 Python 경로가 남는다. 기존 결과 파일은 덮어쓰지 않는다. assert 검사를 사용하는 시험이므로 -O/PYTHONOPTIMIZE를 사용하지 않는다.

검증 결과는 [PACKAGE-VALIDATION](PACKAGE-VALIDATION.md)과 [INTEGRATION-RESULT](INTEGRATION-RESULT.json)에 있다. 전체 Launcher.main, 실제 탐지기·게임·중앙 HTTPS·화면 검증과는 구분한다.
