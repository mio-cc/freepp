import { useState, useEffect } from "react";

interface Props {
  url: string;
  meta: string;
  onClose: () => void;
}

export function SuccessSheet({ url, meta, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copyUrl = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!url) return null;

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <span className="badge badge-success">✓ 提链成功</span>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        <div className="sheet-body">
          <p className="muted" style={{ marginBottom: 10 }}>
            PayPal BA Approve URL 已获取，复制后可在浏览器中完成授权。
          </p>
          <textarea className="textarea" readOnly value={url} rows={3} />
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="btn btn-primary" onClick={copyUrl}>
              {copied ? "✓ 已复制" : "复制 URL"}
            </button>
            <button className="btn" onClick={() => window.open(url, "_blank")}>
              在浏览器中打开
            </button>
          </div>
          <div className="muted" style={{ marginTop: 12, fontSize: 11, fontFamily: "var(--font-mono)" }}>
            {meta}
          </div>
        </div>
      </div>
    </div>
  );
}
