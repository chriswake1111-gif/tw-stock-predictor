import { Component, type ErrorInfo, type ReactNode } from "react";

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("Evidence Workspace render failure", error, info.componentStack); }
  render() {
    if (this.state.failed) return <div className="page section-state" role="alert"><strong>介面無法呈現此筆資料</strong><p>分析狀態沒有被改寫；請重新整理或檢查唯讀 API。</p></div>;
    return this.props.children;
  }
}
