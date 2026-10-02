# MECCHA CHAMELEON Central Server

MECCHA CHAMELEON 안티치트 중앙 서버 통합 실행 문서입니다.

현재 C 통합 단계에서는 heartbeat 수신과 서버 health 확인까지 연결되어 있습니다.
Detection receiver와 scoring 통합 내용은 각 담당 코드가 준비된 뒤 추가합니다.

## 현재 연결된 기능

### Health Check

중앙 서버 프로세스의 실행 상태를 확인합니다.

- Method: `GET`
- Path: `/health`

정상 응답:

```json
{
  "status": "ok"
}