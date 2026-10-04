import { useEffect, useRef, useState, type FormEvent } from "react";
import type { LiveConnectionInput } from "../types";
import { Icon } from "./Icon";

interface ConnectionDialogProps {
  open: boolean;
  loading: boolean;
  initial: LiveConnectionInput;
  error?: string;
  onClose: () => void;
  onConnect: (input: LiveConnectionInput) => void;
}

export function ConnectionDialog({ open, loading, initial, error, onClose, onConnect }: ConnectionDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [input, setInput] = useState(initial);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => setInput(initial), [initial, open]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onConnect({ baseUrl: input.baseUrl.trim(), token: input.token });
  };

  return (
    <dialog
      ref={dialogRef}
      className="connection-dialog"
      aria-labelledby="connection-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!loading) onClose();
      }}
      onClose={() => !loading && onClose()}
    >
      <form onSubmit={submit} className="dialog-card">
        <div className="dialog-head">
          <div>
            <span className="section-kicker">데이터 연결</span>
            <h2 id="connection-dialog-title">중앙 서버</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} disabled={loading} aria-label="닫기">
            <Icon name="close" />
          </button>
        </div>

        <label className="field field-wide">
          <span>서버 주소</span>
          <input
            value={input.baseUrl}
            onChange={(event) => setInput({ ...input, baseUrl: event.target.value })}
            placeholder="/dashboard-api"
            autoComplete="url"
            required
          />
        </label>

        <label className="field field-wide">
          <span>Dashboard 토큰</span>
          <input
            type="password"
            value={input.token}
            onChange={(event) => setInput({ ...input, token: event.target.value })}
            placeholder="Bearer token"
            autoComplete="off"
            required
          />
        </label>

        {error && (
          <div className="error-banner field-wide" role="alert">
            <Icon name="alert" />
            <span>{error}</span>
          </div>
        )}

        <div className="dialog-actions">
          <button className="button button-quiet" type="button" onClick={onClose} disabled={loading}>취소</button>
          <button className="button button-primary" type="submit" disabled={loading}>
            {loading ? <span className="spinner" aria-hidden="true" /> : <Icon name="server" />}
            {loading ? "연결 중" : "연결"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
