import { useState } from 'react';
import type { ReactNode } from 'react';
import { Icon, type IconName } from '../components/shared/Icon';
import { Card } from '../components/shared/Card';
import { PageHeader } from '../components/shared/PageHeader';
import { DropZone } from '../components/shared/DropZone';
import { FileList } from '../components/shared/FileList';
import { ProgressPanel } from '../components/shared/ProgressPanel';
import { ResultsPanel } from '../components/shared/ResultsPanel';
import { PresetCards } from '../components/shared/PresetCards';
import { SettingsPanel } from '../components/shared/SettingsPanel';
import { Checkbox, Select, Slider, TextInput } from '../components/shared/formControls';
import { useToast } from '../components/shared/Toast';
import { MOCK_PRESETS, MOCK_TOOL_REGISTRY } from '../bridge/mockData';
import type { PickedFile } from '../types/bridge';
import { ICON_PATHS } from '../components/shared/iconPaths';

/**
 * Phase 1 checkpoint: every shared component rendered with representative
 * data, for review before Phase 2/3 start consuming them. Not wired into
 * routing — reached via #gallery during dev only (see App.tsx). Will be
 * deleted once Phase 3 pages provide equivalent real-world coverage.
 */
export function ComponentGallery() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([
    { path: 'C:\\demo\\report.pdf', name: 'report.pdf', size: 1_240_000 },
    { path: 'C:\\demo\\invoice.pdf', name: 'invoice.pdf', size: 88_400 },
  ]);
  const [preset, setPreset] = useState('standard');
  const [checked, setChecked] = useState(true);
  const [text, setText] = useState('');
  const [slider, setSlider] = useState(50);
  const [selectVal, setSelectVal] = useState('a');

  return (
    <div className="console">
      <PageHeader
        title="Component gallery (dev only)"
        subtitle="Phase 1 checkpoint — every shared component, console theme"
        backButton={false}
      />

      <SectionTitle>Icons ({Object.keys(ICON_PATHS).length})</SectionTitle>
      <Card>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          {(Object.keys(ICON_PATHS) as IconName[]).map((name) => (
            <div key={name} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, width: 64 }}>
              <Icon name={name} size={22} />
              <span className="mono" style={{ fontSize: 10, color: 'var(--text-3)', textAlign: 'center' }}>
                {name}
              </span>
            </div>
          ))}
        </div>
      </Card>

      <SectionTitle>Toast</SectionTitle>
      <Card>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <GhostButton onClick={() => toast.success('Compressed 3 files — saved 2.1 MB.')}>Success</GhostButton>
          <GhostButton onClick={() => toast.error('Could not open the file.')}>Error</GhostButton>
          <GhostButton onClick={() => toast.warning('Choose an output location.')}>Warning</GhostButton>
          <GhostButton onClick={() => toast.info('Nothing matched the selected options.')}>Info</GhostButton>
        </div>
      </Card>

      <SectionTitle>DropZone + FileList</SectionTitle>
      <DropZone files={files} onFilesChanged={setFiles} multiple compact={files.length > 0} />
      <div style={{ marginTop: 8 }}>
        <FileList
          files={files}
          onRemove={(i) => setFiles((fs) => fs.filter((_, idx) => idx !== i))}
          onReorder={(from, to) =>
            setFiles((fs) => {
              const next = [...fs];
              const [moved] = next.splice(from, 1);
              next.splice(to, 0, moved);
              return next;
            })
          }
        />
      </div>

      <SectionTitle>ProgressPanel</SectionTitle>
      <ProgressPanel pct={62} current={5} total={8} filename="invoice.pdf" onCancel={() => toast.info('Cancelled.')} />

      <SectionTitle>ResultsPanel</SectionTitle>
      <ResultsPanel
        results={{
          files: [
            { name: 'report.pdf', originalSize: 4_200_000, resultSize: 1_800_000, status: 'done' },
            { name: 'invoice.pdf', originalSize: 88_400, resultSize: 61_200, status: 'done' },
            { name: 'scan.pdf', status: 'error', error: 'Password-protected' },
          ],
          totalTime: 3.4,
          totalSaved: 2_629_200,
          outputDir: 'C:\\demo\\output',
        }}
      />

      <SectionTitle>PresetCards</SectionTitle>
      <Card>
        <PresetCards presets={MOCK_PRESETS.presets} selected={preset} onChange={setPreset} />
      </Card>

      <SectionTitle>SettingsPanel + form controls</SectionTitle>
      <SettingsPanel title="Advanced settings" defaultOpen>
        <Checkbox checked={checked} onChange={setChecked} label="Use Ghostscript when available" />
        <div style={{ marginTop: 8 }}>
          <TextInput value={text} onChange={setText} placeholder="Output filename template" />
        </div>
        <div style={{ marginTop: 8 }}>
          <Select
            value={selectVal}
            onChange={setSelectVal}
            options={[
              { value: 'a', label: 'Option A' },
              { value: 'b', label: 'Option B' },
            ]}
          />
        </div>
        <div style={{ marginTop: 8 }}>
          <Slider value={slider} min={0} max={100} onChange={setSlider} />
          <span className="mono" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)' }}>
            {slider}
          </span>
        </div>
      </SettingsPanel>

      <SectionTitle>Tool registry (mock)</SectionTitle>
      <Card>
        <div className="mono" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-2)' }}>
          {MOCK_TOOL_REGISTRY.categories.length} categories, {MOCK_TOOL_REGISTRY.tools.length} tools
        </div>
      </Card>
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        marginTop: 'var(--space-5)',
        marginBottom: 8,
        fontSize: 'var(--font-size-xs)',
        fontWeight: 700,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        color: 'var(--text-3)',
      }}
    >
      {children}
    </div>
  );
}

function GhostButton({ children, onClick }: { children: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: 'transparent',
        border: '1px solid var(--border-strong)',
        color: 'var(--text-1)',
        borderRadius: 'var(--radius-panel-sm)',
        padding: '7px 14px',
        fontSize: 'var(--font-size-sm)',
      }}
    >
      {children}
    </button>
  );
}
