// Harmless test pattern. NOT a real-cheat signature. Never used by default.
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
