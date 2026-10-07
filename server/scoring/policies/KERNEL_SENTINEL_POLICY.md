# KernelSentinel provisional Scoring policy

Version: `kernel-source-provisional-v1`. This is a source-audited working policy
requested for integration testing, not a replay-calibrated operating threshold.
Reference patterns: Hide Anywhere's structured validity gates and snapshot model;
LocalGuard overlay/YARA evidence-dependent threshold/advisory/pending resolution;
existing B Aggregate/Fusion/FinalVerdict evidence-unit pipeline.

## Raw score and interpretation

| Existing raw | Source meaning | Provisional B classification |
|---:|---|---|
| 0 | Verified approved access or valid complete cycle without positives | INACTIVE; no active evidence in observed scope |
| 1 | Sensitive handle granted/restricted | UNRESOLVED while actor/rights scope is uncertain; preserve raw 1 |
| 2 | Configured name match, persistent callback probe missing, dispatch pointer outside listed modules | ADVISORY; observation alone does not establish cheat use |
| 3 | Diagnostics entry/dispatch change, executable-section change, persistent thread origin outside listed modules | ACTIVE only with valid, known, consistent structured source/cycle evidence |
| 4 | Configured exact file SHA-256 match | ACTIVE only with valid, known, consistent structured source/cycle evidence; presence does not prove activation |
| any | Explicit ERROR/OFFLINE/measurement_valid=false | UNAVAILABLE; preserve score/evidence and do not turn zero into normal |
| any | Missing validity/errors metadata, unknown reason, inconsistent score/evidence, unsupported legacy record | UNRESOLVED; no invented evidence |

Threshold is 3 for validated strong evidence; weak evidence is dynamically advisory
or pending rather than being treated as ordinary threshold failure. Audited bound
is 4 and raw is not clipped, converted to percentages, reweighted, or rewritten.
An out-of-range score stays OUT_OF_AUDITED_RANGE/UNRESOLVED.

Approval labels are checked against bounded reviewed module masks, with source
verification flags and actual requested/before/granted masks. ALL_ACCESS is not
made normal by a bare approval label. Cycle zero requires no positive reasons and
no unresolved accesses. Source/cycle metadata is still a producer report, not an
independent central inspection of Windows memory or process identity.

## Integration behavior

- Registered module/profile/handler: `kernel_sentinel`; snapshot bound 4.
- Keep original seven fields, local raw/common logs, t0 timestamps and Shared ACK
  contract unchanged. Optional `reasons` parameter on internal calibration resolver
  is backward compatible; existing non-kernel callers retain their behavior.
- A sensor cycle uses producer max, not sum. Repeated raw 3 records remain one
  current module evidence unit rather than 3+3+3 accumulated points.
- A later valid zero updates the current snapshot; no historical permanent cheat
  conclusion is introduced. A later error is UNAVAILABLE, not proof of recovery.
- No kernel cross-module overlap tags or automatic time-only deduction are added.
- Existing FinalVerdict labels are preserved: strong eligible signal yields
  SUSPICIOUS, pending or unavailable-only signal yields INCONCLUSIVE, valid inactive
  or advisory-only observation yields NO_ACTIVE_EVIDENCE. These do not mean CHEAT
  or an unconditional NORMAL verdict.
- A mixed positive and measurement error remains raw positive plus error in storage,
  but the existing B global measurement-validity gate classifies it UNAVAILABLE.
  Separately activating a valid subchannel would require a new reviewed channel
  validity contract; this patch does not bypass the current gate.

## Validation and limits

Performed on Linux using synthetic detector events:
- Scoring suite: 421 tests, 419 passed / 2 skipped.
- KernelSentinel suite: 98 passed, including actual local HTTP Shared queue/ACK,
  Receiver durable storage, Scoring and authenticated Dashboard events API.
- New Scoring tests cover valid zero, approved mask/excess request, errors, unknown
  metadata/reasons, threshold 3/4, weak advisory, pending handle access, structured
  individual diagnostics/hash evidence, unchanged raw/overlap, range violations,
  repeated snapshot nonaccumulation, recovery and actual Receiver/Dashboard API
  final-verdict mapping.

Not performed: Windows full Launcher/game/driver, actual normal/cheat replay
calibration, operational server deployment, browser UI, or empirical FP/FN and
latency measurement. Before team deployment, B owner should review this isolated
policy patch and actual normal/positive captures. Do not advertise the provisional
threshold as replay-v1, guaranteed zero false positives or proof of cheating.

## Applying

The delivery contains two Git patches on
`ce84650240861d4225496304a3d89a00ac022d98`:
1. local approved-access detector fix `6e1b72789827f29f51c45972536c372beac0e97f`;
2. this provisional server policy.

Apply both in order on a clean branch, or apply only patch 2 if patch 1 is already
installed. Restart the central server after review to load the profile/handler and
calibration. Deployment to a different main revision requires merging/review;
approved-access source pins must not be regenerated from unreviewed local files.
No Launcher or Shared files and no driver binary/security settings are changed.
