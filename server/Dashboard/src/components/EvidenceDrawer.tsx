import { useEffect, useMemo, useRef, useState } from "react";
import { formatElapsed, humanizeModule } from "../domain";
import type { DashboardEvent } from "../types";
import { Icon } from "./Icon";
import { StatusBadge } from "./StatusBadge";

export interface EvidenceDrawerProps {
  event: DashboardEvent | null;
  loading?: boolean;
  error?: string;
  /** `undefined` means the overview capability has not been supplied. */
  evidenceImagesAvailable?: boolean;
  onClose: () => void;
  onRetry?: () => void;
}

type CopyState = "idle" | "copied" | "error";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "details > summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function safeEvidenceImageUrl(value: string | null): string | null {
  const candidate = value?.trim();
  if (!candidate) return null;

  try {
    const base = new URL(
      typeof window !== "undefined" && /^https?:/i.test(window.location.href)
        ? window.location.href
        : "http://localhost/",
    );
    const url = new URL(candidate, base);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password) return null;
    if (url.origin !== base.origin) return null;
    return url.href;
  } catch {
    return null;
  }
}

const renderValue = (value: unknown): string => {
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const normalizeEvidenceKey = (key: string) => key
  .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
  .replace(/[^a-zA-Z0-9]+/g, "_")
  .replace(/^_+|_+$/g, "")
  .toLowerCase();

const isSensitiveEvidenceKey = (key: string): boolean => {
  const normalized = normalizeEvidenceKey(key);
  const isFingerprint = /(?:^|_)(?:id|hash|digest|fingerprint|sha1|sha224|sha256|sha384|sha512|md5)(?:_|$)/.test(normalized);
  if (isFingerprint) return false;

  if ([
    "username",
    "user_name",
    "computer_name",
    "hostname",
    "window_title",
    "full_path",
    "path",
    "source_image",
    "target_image",
    "process_image",
    "image",
    "executable",
  ].includes(normalized)) {
    return true;
  }
  return normalized.startsWith("path_") || normalized.endsWith("_path") || normalized.includes("_path_");
};

function collectSensitiveEvidenceStrings(value: unknown, values = new Set<string>()): Set<string> {
  if (Array.isArray(value)) {
    value.forEach((item) => collectSensitiveEvidenceStrings(item, values));
    return values;
  }
  if (value === null || typeof value !== "object") return values;

  for (const [key, nestedValue] of Object.entries(value as Record<string, unknown>)) {
    if (isSensitiveEvidenceKey(key)) {
      if (typeof nestedValue === "string" && nestedValue.trim().length >= 3) values.add(nestedValue.trim());
      continue;
    }
    collectSensitiveEvidenceStrings(nestedValue, values);
  }
  return values;
}

/** Redacts direct identifiers and absolute user paths from free-form server text. */
export function redactSensitiveText(value: string, sensitiveValues: Iterable<string> = []): string {
  let redacted = value
    .replace(/(["'])(?:(?:[A-Za-z]:[\\/]|\\\\|\/(?:Users|home)\/)[^"'<>|\r\n]*)\1/g, (_match, quote: string) => `${quote}[redacted-path]${quote}`)
    .replace(/(?:\b[A-Za-z]:[\\/]|\\\\)[^\s"'<>|,;)\]}]*/g, "[redacted-path]")
    .replace(/(^|[\s("'=])\/(?:Users|home)\/[^\s"'<>|,;)\]}]*/g, "$1[redacted-path]");
  for (const sensitiveValue of sensitiveValues) {
    redacted = redacted.replaceAll(sensitiveValue, "[redacted]");
  }
  redacted = redacted
    .replace(/\b(user(?:_?name)?|computer_?name|host_?name|window_?title)\s*([=:])\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, "$1$2[redacted]")
    .replace(/\bDESKTOP-[A-Z0-9-]+\b/gi, "[redacted]");
  return redacted;
}

interface SanitizedEvidence {
  evidence: Record<string, unknown>;
  hiddenCount: number;
}

/** Removes direct identifying fields before evidence is rendered or copied. */
export function sanitizeEvidence(evidence: Record<string, unknown>): SanitizedEvidence {
  let hiddenCount = 0;
  const sensitiveValues = collectSensitiveEvidenceStrings(evidence);

  const visit = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(visit);
    if (typeof value === "string") {
      const redacted = redactSensitiveText(value, sensitiveValues);
      if (redacted !== value) hiddenCount += 1;
      return redacted;
    }
    if (value === null || typeof value !== "object") return value;

    const sanitized: Record<string, unknown> = {};
    for (const [key, nestedValue] of Object.entries(value as Record<string, unknown>)) {
      if (isSensitiveEvidenceKey(key)) {
        hiddenCount += 1;
        continue;
      }
      sanitized[key] = visit(nestedValue);
    }
    return sanitized;
  };

  return {
    evidence: visit(evidence) as Record<string, unknown>,
    hiddenCount,
  };
}

export function EvidenceDrawer({
  event,
  loading = false,
  error,
  evidenceImagesAvailable,
  onClose,
  onRetry,
}: EvidenceDrawerProps) {
  const drawerRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const copyResetRef = useRef<number | null>(null);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!event) return;

    const returnFocusTo = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    const drawer = drawerRef.current;
    document.body.style.overflow = "hidden";
    drawer?.querySelector<HTMLElement>(focusableSelector)?.focus();

    const handleKeyDown = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === "Escape") {
        keyboardEvent.preventDefault();
        onCloseRef.current();
        return;
      }
      if (keyboardEvent.key !== "Tab" || !drawer) return;

      const focusable = [...drawer.querySelectorAll<HTMLElement>(focusableSelector)]
        .filter((element) => !element.hasAttribute("hidden") && element.getAttribute("aria-hidden") !== "true");
      if (focusable.length === 0) {
        keyboardEvent.preventDefault();
        drawer.focus();
        return;
      }

      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      const active = document.activeElement;
      if (keyboardEvent.shiftKey && (active === first || !drawer.contains(active))) {
        keyboardEvent.preventDefault();
        last.focus();
      } else if (!keyboardEvent.shiftKey && (active === last || !drawer.contains(active))) {
        keyboardEvent.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (returnFocusTo?.isConnected) returnFocusTo.focus();
    };
  }, [event?.id]);

  useEffect(() => {
    setCopyState("idle");
    setImageFailed(false);
  }, [event?.id, event?.evidence_image]);

  useEffect(() => () => {
    if (copyResetRef.current !== null) window.clearTimeout(copyResetRef.current);
  }, []);

  const sanitizedEvidence = useMemo(
    () => event ? sanitizeEvidence(event.evidence) : null,
    [event],
  );
  const sensitiveEvidenceValues = useMemo(
    () => event ? collectSensitiveEvidenceStrings(event.evidence) : new Set<string>(),
    [event],
  );
  const sanitizedReasons = useMemo(
    () => event ? event.reasons.map((reason) => redactSensitiveText(reason, sensitiveEvidenceValues)) : [],
    [event, sensitiveEvidenceValues],
  );
  const sanitizedLogExcerpt = useMemo(
    () => event?.log_excerpt ? redactSensitiveText(event.log_excerpt, sensitiveEvidenceValues) : null,
    [event, sensitiveEvidenceValues],
  );
  const sanitizedError = error ? redactSensitiveText(error, sensitiveEvidenceValues) : null;
  const payload = useMemo(() => event && sanitizedEvidence ? {
    session_id: event.session_id,
    player_id: event.player_id,
    module: event.module,
    timestamp_ms: event.timestamp_ms,
    evidence: sanitizedEvidence.evidence,
    reasons: sanitizedReasons,
    raw_score: event.raw_score,
  } : null, [event, sanitizedEvidence, sanitizedReasons]);

  if (!event || !payload || !sanitizedEvidence) return null;
  const operational = event.event_kind === "operational";
  const evidenceImageUrl = safeEvidenceImageUrl(event.evidence_image);
  const showEvidenceImage = evidenceImagesAvailable !== undefined || event.evidence_image !== null;

  const copy = async () => {
    if (copyResetRef.current !== null) window.clearTimeout(copyResetRef.current);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
    copyResetRef.current = window.setTimeout(() => setCopyState("idle"), 2_400);
  };

  return (
    <div className="drawer-layer" role="presentation" onMouseDown={(mouseEvent) => mouseEvent.target === mouseEvent.currentTarget && onClose()}>
      <aside ref={drawerRef} className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title" tabIndex={-1}>
        <div className="drawer-head">
          <div><span className="section-kicker">이벤트 #{event.sequence}</span><h2 id="evidence-title">이벤트 상세</h2></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="상세 패널 닫기"><Icon name="close" /></button>
        </div>

        <div className="drawer-body">
          {sanitizedError && (
            <div className="error-banner" role="alert">
              <Icon name="alert" />
              <span>{sanitizedError}</span>
              {onRetry && (
                <button type="button" onClick={onRetry} aria-label="상세 정보 다시 불러오기">
                  <Icon name="refresh" />
                </button>
              )}
            </div>
          )}
          {loading && <div className="drawer-loading"><span className="spinner" /> 상세 정보를 불러오는 중</div>}
          <div className="drawer-summary">
            <div className="drawer-badges">
              <StatusBadge tone={operational ? "info" : "neutral"}>{operational ? "운영 이벤트" : "관측 이벤트"}</StatusBadge>
              <span className="module-pill">{humanizeModule(event.module)}</span>
            </div>
            <h3>{event.player_id}</h3>
            <p>{event.session_id} · {formatElapsed(event.timestamp_ms)}</p>
          </div>

          <section className="detail-section" aria-labelledby="event-info-title">
            <h4 id="event-info-title">이벤트 정보</h4>
            <dl className="detail-grid">
              <div><dt>세션</dt><dd>{event.session_id}</dd></div>
              <div><dt>플레이어</dt><dd>{event.player_id}</dd></div>
              <div><dt>모듈</dt><dd>{humanizeModule(event.module)}</dd></div>
              <div><dt>세션 경과</dt><dd>{formatElapsed(event.timestamp_ms)}</dd></div>
              <div><dt>원시 점수</dt><dd>{event.raw_score}</dd></div>
              <div><dt>Event ID</dt><dd className="mono">{event.id}</dd></div>
            </dl>
          </section>

          <section className="detail-section" aria-labelledby="reason-title">
            <h4 id="reason-title">이벤트 이유</h4>
            {sanitizedReasons.length > 0
              ? <ul className={`reason-list ${operational ? "operational" : "observed"}`}>{sanitizedReasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul>
              : <div className="empty-inline">기록된 이유 없음</div>}
          </section>

          <section className="detail-section" aria-labelledby="evidence-fields-title">
            <h4 id="evidence-fields-title">Evidence</h4>
            {Object.keys(sanitizedEvidence.evidence).length > 0 ? (
              <div className="evidence-fields">
                {Object.entries(sanitizedEvidence.evidence).map(([key, value]) => <div key={key}><span>{key}</span><strong>{renderValue(value)}</strong></div>)}
              </div>
            ) : <div className="empty-inline">Evidence 필드 없음</div>}
            {sanitizedEvidence.hiddenCount > 0 && (
              <div className="empty-inline" role="status">민감 필드 {sanitizedEvidence.hiddenCount}개 숨김</div>
            )}
          </section>

          {showEvidenceImage && (
            <section className="detail-section" aria-labelledby="evidence-image-title">
              <h4 id="evidence-image-title">첨부 이미지</h4>
              {evidenceImagesAvailable === false ? (
                <div className="empty-inline">서버 미지원</div>
              ) : event.evidence_image === null ? (
                <div className="empty-inline">첨부 없음</div>
              ) : evidenceImageUrl === null ? (
                <div className="empty-inline">안전하지 않은 주소</div>
              ) : (
                <figure className="evidence-image">
                  {!imageFailed
                    ? <img src={evidenceImageUrl} alt={`${event.player_id} 이벤트 첨부`} loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} />
                    : <div className="empty-inline">이미지 로드 실패</div>}
                  <figcaption>
                    <a href={evidenceImageUrl} target="_blank" rel="noopener noreferrer">원본 열기</a>
                  </figcaption>
                </figure>
              )}
            </section>
          )}

          {sanitizedLogExcerpt && <section className="detail-section" aria-labelledby="log-title"><h4 id="log-title">로그</h4><pre className="log-excerpt">{sanitizedLogExcerpt}</pre></section>}

          <details className="raw-data"><summary>공통 이벤트 JSON</summary><pre>{JSON.stringify(payload, null, 2)}</pre></details>
        </div>

        <div className="drawer-footer">
          <span className="drawer-event-id mono">{event.id}</span>
          <div className="drawer-copy">
            <span className={`copy-feedback ${copyState === "error" ? "copy-error" : ""}`} role="status" aria-live="polite">
              {copyState === "copied" ? "복사 완료" : copyState === "error" ? "클립보드 복사 실패" : ""}
            </span>
            <button className="button button-quiet" type="button" onClick={copy}>
              <Icon name={copyState === "copied" ? "check" : "copy"} />
              {copyState === "copied" ? "복사됨" : copyState === "error" ? "다시 복사" : "JSON 복사"}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}
