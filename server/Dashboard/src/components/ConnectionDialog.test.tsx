// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ConnectionDialog } from "./ConnectionDialog";

let originalShowModal: typeof HTMLDialogElement.prototype.showModal;
let originalClose: typeof HTMLDialogElement.prototype.close;

beforeEach(() => {
  originalShowModal = HTMLDialogElement.prototype.showModal;
  originalClose = HTMLDialogElement.prototype.close;
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

afterEach(() => {
  cleanup();
  HTMLDialogElement.prototype.showModal = originalShowModal;
  HTMLDialogElement.prototype.close = originalClose;
});

describe("ConnectionDialog", () => {
  it("shows a connection failure inside the open dialog", () => {
    render(
      <ConnectionDialog
        open
        loading={false}
        initial={{ baseUrl: "/dashboard-api", token: "test-token" }}
        error="중앙 서버에 연결할 수 없습니다."
        onClose={() => undefined}
        onConnect={() => undefined}
      />,
    );

    expect(screen.getByRole("dialog", { name: "중앙 서버" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("중앙 서버에 연결할 수 없습니다.");
  });
});
