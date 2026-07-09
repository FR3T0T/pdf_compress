import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { bridgeApi } from '../bridge/bridgeApi';
import type { AnalyzeReport } from '../types/analyze';

export interface WorkspaceOp {
  label: string;
}

/**
 * Result of the background pdf_analyze scan kicked off automatically when a
 * file is loaded into the workspace (see WorkspaceProvider.load below).
 * This is DETECTION, not a safety guarantee — it flags known PDF-specific
 * risks (embedded JS, launch/auto-run actions, external links, embedded
 * files); it is not antivirus and finding nothing doesn't mean the file is
 * "safe". `findingCount` only counts non-info-severity findings (the ones
 * worth a human glancing at), not every informational item pdf_analyze
 * reports.
 */
export type WorkspaceScanState =
  | { status: 'idle' }
  | { status: 'scanning' }
  | { status: 'done'; findingCount: number; report: AnalyzeReport }
  | { status: 'error' };

const IDLE_SCAN: WorkspaceScanState = { status: 'idle' };

interface WorkspaceState {
  /** Current working document path, or null if nothing is loaded. */
  path: string | null;
  /** Basename of the file originally loaded — fixed at load time even as
   *  `path` moves on to successive transform outputs, so the UI can still
   *  say "Working document: <original name>". */
  originalName: string | null;
  /** Operations applied so far, in order, most recent last. */
  ops: WorkspaceOp[];
}

export interface WorkspaceContextValue extends WorkspaceState {
  /** Load a file as the (brand-new) working document, replacing whatever
   *  was there and cleaning up any workspace-owned temp file it left behind. */
  load: (path: string) => void;
  /** Record a tool's output as the new working document — the
   *  running-result mechanic. Deletes the just-superseded temp file, but
   *  never the original file the workspace was first loaded from. */
  applyResult: (newPath: string, opLabel: string) => void;
  /** Clear the workspace entirely, cleaning up any owned temp file. */
  clear: () => void;
  /** Copy the current working document out to a user-chosen path. Resolves
   *  false (and leaves the workspace untouched) if there's nothing loaded
   *  or the copy fails. */
  exportTo: (destPath: string) => Promise<boolean>;
  /** Background pdf_analyze scan result for the currently loaded document —
   *  see WorkspaceScanState above. Idle/error means "no scan info to show",
   *  not "confirmed risk-free". */
  scan: WorkspaceScanState;
  /** True while a tool is actively running against the workspace document
   *  (set via useWorkspaceBusy below). The global bar disables Load/Export/
   *  Clear while true, since those would race a transform reading/writing
   *  the current working file. */
  busy: boolean;
  /** Internal: see useWorkspaceBusy below. */
  setBusy: (busy: boolean) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

const EMPTY_STATE: WorkspaceState = { path: null, originalName: null, ops: [] };

/**
 * Holds the workspace's single "working document" — one file path plus the
 * ops applied to it — above the router/AppShell so it survives navigating
 * from tool to tool (AppShell's keep-alive keeps each tool page's own state
 * alive too, but the workspace document is shared *across* pages, so it
 * lives one level higher, wrapping RouterProvider in App.tsx).
 *
 * Phase 1: a single document only (no undo, no multi-document array yet —
 * see the Phase 2 note in App.tsx). Each transform's output is a new temp
 * file under bridgeApi.getWorkspaceDir(); this context tracks whether the
 * current `path` is one of those workspace-owned temp files (safe to
 * delete when superseded or cleared) versus the user's original input
 * (never deleted by us).
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<WorkspaceState>(EMPTY_STATE);
  const [busy, setBusy] = useState(false);
  const [scan, setScan] = useState<WorkspaceScanState>(IDLE_SCAN);
  const ownedRef = useRef(false);
  // The path the most recent scan was started for. Guards against a scan
  // response arriving after the workspace has already moved on to a
  // different (or no) document — e.g. Load A, then quickly Load B or Clear
  // before A's scan resolves.
  const scanPathRef = useRef<string | null>(null);

  // Non-blocking, best-effort security scan of `path` -- never delays or
  // blocks the caller, and any failure just leaves scan at idle (no badge)
  // rather than surfacing an error. The scanPathRef guard makes the newest
  // scan win: if the workspace moves on to a different (or no) document
  // before this scan resolves, its result is discarded. Called on load AND
  // after every transform (applyResult), so the badge always reflects the
  // CURRENT working document -- e.g. a Flatten that strips JS lowers the
  // badge instead of showing the pre-transform findings (FE-02).
  const startScan = useCallback((path: string) => {
    scanPathRef.current = path;
    setScan({ status: 'scanning' });
    bridgeApi
      .analyzeDocument(path)
      .then((res) => {
        if (scanPathRef.current !== path) return;
        if (!res.success || !res.report) {
          setScan(IDLE_SCAN);
          return;
        }
        const { high, medium, low } = res.report.counts;
        setScan({ status: 'done', findingCount: high + medium + low, report: res.report });
      })
      .catch(() => {
        if (scanPathRef.current === path) setScan(IDLE_SCAN);
      });
  }, []);

  const load = useCallback((path: string) => {
    setState((prev) => {
      if (ownedRef.current && prev.path) bridgeApi.deleteFile(prev.path);
      return { path, originalName: bridgeApi.basename(path), ops: [] };
    });
    ownedRef.current = false;
    startScan(path);
  }, [startScan]);

  const applyResult = useCallback((newPath: string, opLabel: string) => {
    setState((prev) => {
      if (ownedRef.current && prev.path && prev.path !== newPath) {
        bridgeApi.deleteFile(prev.path);
      }
      return { ...prev, path: newPath, ops: [...prev.ops, { label: opLabel }] };
    });
    ownedRef.current = true;
    // Re-scan the transform's output so the badge reflects the new document.
    startScan(newPath);
  }, [startScan]);

  const clear = useCallback(() => {
    setState((prev) => {
      if (ownedRef.current && prev.path) bridgeApi.deleteFile(prev.path);
      return EMPTY_STATE;
    });
    ownedRef.current = false;
    scanPathRef.current = null;
    setScan(IDLE_SCAN);
  }, []);

  const exportTo = useCallback(async (destPath: string): Promise<boolean> => {
    if (!state.path) return false;
    const res = await bridgeApi.copyFile(state.path, destPath);
    return res.success;
  }, [state.path]);

  const value: WorkspaceContextValue = { ...state, load, applyResult, clear, exportTo, scan, busy, setBusy };

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace() must be used within a <WorkspaceProvider>.');
  return ctx;
}

/**
 * A tool page calls this with its own "am I currently running against the
 * workspace document" boolean (e.g. `op.status === 'running' && !!workspace.path`)
 * so the global WorkspaceBar can disable Load/Export/Clear for the duration —
 * same pattern as Router's usePageBusy, but for the workspace instead of
 * navigation.
 */
export function useWorkspaceBusy(busy: boolean): void {
  const { setBusy } = useWorkspace();
  useEffect(() => {
    setBusy(busy);
    return () => setBusy(false);
  }, [busy, setBusy]);
}
