// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DashboardEvent } from "../types";
import { EvidenceDrawer, safeEvidenceImageUrl } from "./EvidenceDrawer";

const event: DashboardEvent = {
  id: "event-1",
  sequence: 1,
  session_id: "esp_001",
  player_id: "player_042",
  module: "esp",
  timestamp_ms: 42_000,
  evidence: { submodule: "handle_sensor" },
  reasons: ["PROCESS_VM_READ"],
  raw_score: 3,
  event_kind: "detection",
  time_basis: "unknown",
  evidence_image: null,
  log_excerpt: null,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EvidenceDrawer accessibility", () => {
  it("presents a positive raw score as an observation, not a server verdict", () => {
    render(<EvidenceDrawer event={event} onClose={() => undefined} />);

    expect(screen.getByRole("dialog", { name: "이벤트 상세" })).toBeTruthy();
    const badge = screen.getByText("관측 이벤트");
    expect(badge.classList.contains("tone-neutral")).toBe(true);
    expect(badge.classList.contains("tone-danger")).toBe(false);
    expect(screen.queryByText("탐지 이벤트")).toBeNull();
  });

  it("uses event_kind, not raw_score, for operational presentation", () => {
    render(
      <EvidenceDrawer
        event={{ ...event, raw_score: 9, event_kind: "operational" }}
        onClose={() => undefined}
      />,
    );

    const badge = screen.getByText("운영 이벤트");
    expect(badge.classList.contains("tone-info")).toBe(true);
    expect(badge.classList.contains("tone-danger")).toBe(false);
  });

  it("moves focus into the dialog, closes with Escape and restores trigger focus", () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>상세 열기</button>
          <EvidenceDrawer event={open ? event : null} onClose={() => setOpen(false)} />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "상세 열기" });
    trigger.focus();
    fireEvent.click(trigger);

    expect(document.activeElement).toBe(screen.getByRole("button", { name: "상세 패널 닫기" }));
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("keeps Tab focus inside the drawer", () => {
    render(<EvidenceDrawer event={event} onClose={() => undefined} />);
    const close = screen.getByRole("button", { name: "상세 패널 닫기" });
    const copy = screen.getByRole("button", { name: "JSON 복사" });

    copy.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(close);

    close.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(copy);
  });

  it("reports clipboard failures and leaves a retry action", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<EvidenceDrawer event={event} onClose={() => undefined} />);

    fireEvent.click(screen.getByRole("button", { name: "JSON 복사" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("클립보드 복사 실패"));
    expect(screen.getByRole("button", { name: "다시 복사" })).toBeTruthy();
  });
});

describe("EvidenceDrawer image handling", () => {
  it("accepts only credential-free same-origin HTTP(S) evidence URLs", () => {
    expect(safeEvidenceImageUrl("javascript:alert(1)")).toBeNull();
    expect(safeEvidenceImageUrl("data:image/svg+xml,<svg />")).toBeNull();
    expect(safeEvidenceImageUrl("https://user:pass@example.com/evidence.png")).toBeNull();
    expect(safeEvidenceImageUrl("https://evidence.example/player-042.png")).toBeNull();
    expect(safeEvidenceImageUrl("/evidence/player-042.png")).toMatch(/^http:\/\/localhost/);
  });

  it("renders a safe same-origin image and link when the capability is available", () => {
    render(
      <EvidenceDrawer
        event={{ ...event, evidence_image: "/evidence/player-042.png" }}
        evidenceImagesAvailable
        onClose={() => undefined}
      />,
    );

    const image = screen.getByRole("img", { name: "player_042 이벤트 첨부" });
    expect(image.getAttribute("src")).toBe(new URL("/evidence/player-042.png", window.location.href).toString());
    const link = screen.getByRole("link", { name: "원본 열기" });
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("does not render an image when the server capability is disabled or the URL is unsafe", () => {
    const { rerender } = render(
      <EvidenceDrawer
        event={{ ...event, evidence_image: "/evidence/player-042.png" }}
        evidenceImagesAvailable={false}
        onClose={() => undefined}
      />,
    );
    expect(screen.getByText("서버 미지원")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();

    rerender(
      <EvidenceDrawer
        event={{ ...event, evidence_image: "javascript:alert(1)" }}
        evidenceImagesAvailable
        onClose={() => undefined}
      />,
    );
    expect(screen.getByText("안전하지 않은 주소")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
