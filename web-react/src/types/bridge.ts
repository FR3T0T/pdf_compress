/**
 * Shapes verified directly against ui/bridge.py — field names are verbatim,
 * not guessed. Do not rename without checking the Python side.
 */

/** getToolRegistry() -> tools[] entry (ui/bridge.py:600-607) */
export interface ToolDef {
  key: string;
  title: string;
  description: string;
  icon: string;
  category: string;
  acceptedExtensions: string[];
}

/** getToolRegistry() -> categories[] entry (ui/bridge.py:609) */
export interface Category {
  key: string;
  label: string;
}

/** getToolRegistry() full response (ui/bridge.py:611-614) */
export interface ToolRegistry {
  tools: ToolDef[];
  categories: Category[];
}

/** getPresets() -> presets[] entry (ui/bridge.py:365-371) */
export interface Preset {
  key: string;
  name: string;
  description: string;
  targetDpi: Record<string, number>;
  jpegQuality: number;
}

/** getPresets() full response (ui/bridge.py:372-376) */
export interface PresetsResponse {
  presets: Preset[];
  defaultPreset: string;
  ghostscriptAvailable: boolean;
}

/** progressUpdate signal payload (ui/bridge.py _progress_payload, line 237-250) */
export interface ProgressPayload {
  toolKey: string;
  current: number;
  total: number;
  pct: number;
  filename: string;
}

/**
 * operationDone signal payload (ui/bridge.py _done_payload, line 206-234).
 * `results` shape is tool-specific — callers narrow/cast it per tool.
 */
export interface DonePayload<TResult = unknown> {
  toolKey: string;
  success: boolean;
  message: string;
  results?: TResult;
}

/** A file picked via BridgeAPI.openFiles() or the "files-dropped" signal. */
export interface PickedFile {
  path: string;
  name: string;
  size?: number;
  pages?: number;
}
