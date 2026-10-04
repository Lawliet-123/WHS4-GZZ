import { useEffect, useMemo, useRef, useState } from "react";
import { formatElapsed, humanizeModule } from "../domain";
import type { DashboardEvent } from "../types";
import { Icon } from "./Icon";
import { StatusBadge } from "./StatusBadge";

export interface EvidenceDrawerProps {
  event: DashboardEvent | null;
  loading?: boolean;
  /** `undefined` means the overview capability has not been supplied. */
  evidenceImagesAvailable?: boolean;
  onClose: () => void;
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

export function EvidenceDrawer({
  event,
  loading = false,
  evidenceImagesAvailable,
  onClose,
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

  const payload = useMemo(() => event ? {
    session_id: event.session_id,
    player_id: event.player_id,
    module: event.module,
    timestamp_ms: event.timestamp_ms,
    evidence: event.evidence,
    reasons: event.reasons,
    raw_score: event.raw_score,
  } : null, [event]);

  if (!event || !payload) return null;
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
          <div><span className="section-kicker">이벤트 #{event.sequence}</span><h2 id="evidence-title">탐지 상세</h2></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="상세 패널 닫기"><Icon name="close" /></button>
        </div>

        <div className="drawer-body">
          {loading && <div className="drawer-loading"><span className="spinner" /> 상세 정보를 불러오는 중</div>}
          <div className="drawer-summary">
            <div className="drawer-badges">
              <StatusBadge tone={operational ? "info" : event.raw_score > 0 ? "danger" : "neutral"}>{operational ? "운영 상태" : "탐지 이벤트"}</StatusBadge>
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
            <h4 id="reason-title">탐지 이유</h4>
            {event.reasons.length > 0
              ? <ul className="reason-list">{event.reasons.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}</ul>
              : <div className="empty-inline">기록된 이유 없음</div>}
          </section>

          <section className="detail-section" aria-labelledby="evidence-fields-title">
            <h4 id="evidence-fields-title">Evidence</h4>
            {Object.keys(event.evidence).length > 0 ? (
              <div className="evidence-fields">
                {Object.entries(event.evidence).map(([key, value]) => <div key={key}><span>{key}</span><strong>{renderValue(value)}</strong></div>)}
              </div>
            ) : <div className="empty-inline">Evidence 필드 없음</div>}
          </section>

          {showEvidenceImage && (
            <section className="detail-section" aria-labelledby="evidence-image-title">
              <h4 id="evidence-image-title">증거 이미지</h4>
              {evidenceImagesAvailable === false ? (
                <div className="empty-inline">서버 미지원</div>
              ) : event.evidence_image === null ? (
                <div className="empty-inline">첨부 없음</div>
              ) : evidenceImageUrl === null ? (
                <div className="empty-inline">안전하지 않은 주소</div>
              ) : (
                <figure className="evidence-image">
                  {!imageFailed
                    ? <img src={evidenceImageUrl} alt={`${event.player_id} 탐지 증거`} loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} />
                    : <div className="empty-inline">이미지 로드 실패</div>}
                  <figcaption>
                    <a href={evidenceImageUrl} target="_blank" rel="noopener noreferrer">원본 열기</a>
                  </figcaption>
                </figure>
              )}
            </section>
          )}

          {event.log_excerpt && <section className="detail-section" aria-labelledby="log-title"><h4 id="log-title">로그</h4><pre className="log-excerpt">{event.log_excerpt}</pre></section>}

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
