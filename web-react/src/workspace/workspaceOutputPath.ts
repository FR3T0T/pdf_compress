import { bridgeApi } from '../bridge/bridgeApi';

/**
 * Builds a new, guaranteed-unique output path inside the workspace temp dir
 * for a running-result transform on a tool that takes an explicit single
 * `output_path` (PageNumbers, PageOps, Crop, Flatten, Metadata, Nup,
 * Redact) rather than an `output_dir`+`naming` template (Watermark,
 * Compress, Protect use `{name}_ws<opIndex>` directly against
 * bridgeApi.getWorkspaceDir() instead).
 *
 * `opIndex` should be `workspace.ops.length + 1` so it strictly increases
 * every run — every call therefore produces a distinct filename even when
 * `currentPath`'s basename repeats, so a transform never silently
 * overwrites the file it just read (there's no server-side dedupe for a
 * caller-supplied full path, unlike naming-template tools which go through
 * pdf_ops.py's contained_output_path).
 */
export function workspaceOutputPath(wsDir: string, currentPath: string, opIndex: number): string {
  const baseName = bridgeApi.basename(currentPath).replace(/\.(pdf|epdf)$/i, '');
  return `${wsDir}\\${baseName}_ws${opIndex}.pdf`;
}
