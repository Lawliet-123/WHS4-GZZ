// 무해한 메모리 ON/OFF fixture 전용 문자열. 실제 핵 시그니처가 아니며
// 기본 규칙 파일에는 포함되지 않는다. test_only 메타데이터 덕분에 이
// 규칙을 사용한 세션을 실게임 정상/핵 비교 자료로 표시하지 않는다.
rule LocalGuard_Controlled_Memory_Marker
{
    meta:
        score = 3
        test_only = true
    strings:
        $marker = "LOCALGUARD_TEST_ONLY_7837A742_7C05_46AB_9E2B" ascii
    condition:
        $marker
}
