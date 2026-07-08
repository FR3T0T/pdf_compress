import { createContext, useCallback, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { bridgeApi } from '../bridge/bridgeApi';

export interface WorkspaceOp {
  label: string;
}

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
  const ownedRef = useRef(false);

  const load = useCallback((path: string) => {
    setState((prev) => {
      if (ownedRef.current && prev.path) bridgeApi.deleteFile(prev.path);
      return { path, originalName: bridgeApi.basename(path), ops: [] };
    });
    ownedRef.current = false;
  }, []);

  const applyResult = useCallback((newPath: string, opLabel: string) => {
    setState((prev) => {
      if (ownedRef.current && prev.path && prev.path !== newPath) {
        bridgeApi.deleteFile(prev.path);
      }
      return { ...prev, path: newPath, ops: [...prev.ops, { label: opLabel }] };
    });
    ownedRef.current = true;
  }, []);

  const clear = useCallback(() => {
    setState((prev) => {
      if (ownedRef.current && prev.path) bridgeApi.deleteFile(prev.path);
      return EMPTY_STATE;
    });
    ownedRef.current = false;
  }, []);

  const exportTo = useCallback(async (destPath: string): Promise<boolean> => {
    if (!state.path) return false;
    const res = await bridgeApi.copyFile(state.path, destPath);
    return res.success;
  }, [state.path]);

  const value: WorkspaceContextValue = { ...state, load, applyResult, clear, exportTo };

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace() must be used within a <WorkspaceProvider>.');
  return ctx;
}
