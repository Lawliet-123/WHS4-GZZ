# KernelSentinelValidation

KernelSentinel 커널 드라이버와 Python 수집기의 동작을 시험하는 Windows용 소스 저장소입니다. 검증기는 별도의 시험 프로세스와 DLL·드라이버를 만들어 관측 결과를 확인합니다. 실제 게임의 치트 여부를 자동 확정하거나 모든 커널 은닉 기법을 탐지하는 도구는 아닙니다.

## 구성

| 경로 | 내용 |
|---|---|
| [KernelSentinel](KernelSentinel/README.md) | 커널 드라이버, Python 수집기, 설정, 자체 테스트 |
| [validation](validation) | 독립 시험 대상, 판정 로직, 오프라인·실측 테스트 |
| [Run.ps1](Run.ps1) / [Build.ps1](Build.ps1) | 검증 실행 및 빌드 진입점 |
| [LiveEntry06.py](LiveEntry06.py) 등 | 06~10번 별도 실측 실행기 |
| [host-debug-bridge](host-debug-bridge/README.md) | 선택적 자동 커널 디버거 보조 프로그램의 소스 |
| [docs/coverage.md](docs/coverage.md) | 기능별 시험 범위와 한계 |

## 빌드와 검증

Windows x64, 64비트 Python 3.10 이상, Visual Studio C++ 빌드 도구가 필요합니다. 커널 드라이버를 빌드할 때는 호환되는 Windows SDK/WDK도 필요합니다. 라이브 커널 검증은 Windows 10 빌드 19045 x64의 격리된 테스트 VM에서 수행하도록 설계되었습니다.

```powershell
# 시험 DLL과 네이티브 테스트 프로그램 빌드
.\Build.ps1

# 빌드된 파일로 오프라인 검증
.\Run.ps1 -Mode offline

# WDK가 있을 때 드라이버 컴파일만 확인
.\Build.ps1 -Driver -KernelSentinel -Unsigned -CompileOnly
```

`observe`와 `enforce` 실측은 KernelSentinel 드라이버를 테스트 VM에 **별도로 빌드·서명·설치**한 뒤 관리자 PowerShell에서 실행합니다. [설치 스크립트](KernelSentinel/scripts/Install.ps1)는 드라이버 서비스만 등록·시작하며 서명 신뢰나 부팅 설정을 바꾸지 않습니다.

```powershell
.\Run.ps1 -Mode observe -Signatures
.\Run.ps1 -Mode enforce -Signatures
```

`-LiveEvidenceRoot .\runs`는 별도 시험의 **이전** 보고서를 가져옵니다. 새 시험을 실행했다는 뜻이 아닙니다. `-FreshValidation`은 [호스트 디버거 보조 프로그램](host-debug-bridge/README.md)이 실행 중이고 VM에 로컬 `bridge.json`이 있을 때에만 사용합니다. 이 자동 경로의 실제 VM 동작은 아직 완전히 확인되지 않았습니다.

## 결과 해석

`PASS`는 개별 시험의 기대 결과가 관측됐다는 뜻입니다. 메모리 읽기 성공이나 기존 핸들 사용 가능처럼 **기능 또는 한계를 확인하는 PASS**도 있으며, 모두 치트 탐지 성공을 뜻하지 않습니다. `NOT_RUN`·`INCONCLUSIVE`가 남으면 전체 결과는 부분 검증입니다. 특히 PSAPI와 AuxKlib 목록의 차이를 확인한 시험은 실제 커널 은닉 드라이버의 일반 탐지를 입증하지 않습니다.

06~08번의 일부 변경 시험은 커널 디버거로 만든 변화를 확인한 과거 별도 실측입니다. 과거 실측을 현재 빌드 검증으로 합산하려면 원시 이벤트 재검산과 드라이버·수집기 빌드 식별이 더 필요합니다. [수동 시험 설명](docs/manual/README.md)과 [기능 상태표](KernelSentinel/FEATURE_STATUS_v0.2.3.md)를 함께 보세요.

## 공개 저장소에 포함하지 않은 자료

제공된 원본 ZIP의 `bridge.json`, KDNET 연결 자료, 실측 `runs/`와 `results/`, 개인 경로가 들어 있는 로그, 빌드 산출물(`.sys`, `.exe`, `.dll`, PDB 등), 인증서 및 Python 캐시는 제외했습니다. 이 저장소는 **소스 전용**이며 원본 ZIP은 수정하지 않았습니다. 검증 보고서를 공개하려면 별도로 개인정보와 재현 가능성을 검토하세요.

라이선스 파일은 임의로 추가하지 않았습니다. 공개 저장소로 배포할 때 사용 조건은 저장소 소유자가 결정해야 합니다.
